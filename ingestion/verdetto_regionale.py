"""Parere AI + avviso Telegram sui bandi regionali aperti — R41/R42.

STESSA IDEA DI suam_verdetto.py (R40/R34), PARAMETRIZZATO PER FONTE. SUAM ha
avuto la sua copia dedicata perche' all'epoca era l'unica fonte regionale;
con la seconda e la terza (Intercenter Emilia-Romagna, START Toscana) tre
copie quasi identiche di 200 righe smettono di essere "poca duplicazione" e
diventano manutenzione moltiplicata a ogni piccola modifica del prompt — qui
la fonte e' un parametro (--fonte), non un file.

suam_verdetto.py resta com'e' apposta: non c'e' motivo di toccare uno script
che funziona per farlo rientrare in questo, il giorno in cui servisse si fa
in un giro a parte.

Uso:
    python verdetto_regionale.py --fonte intercenter --dry-run
    python verdetto_regionale.py --fonte intercenter
    python verdetto_regionale.py --fonte intercenter --telegram
    python verdetto_regionale.py --fonte start_toscana --telegram

Dipendenza: solo urllib — nessun pacchetto 'anthropic', stessa scelta di
ted_verdetto.py/suam_verdetto.py.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notifica import conf, invia_telegram  # noqa: E402

QUI = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(QUI, "radar.db")
API = "https://api.anthropic.com/v1/messages"
MODELLO = "claude-haiku-4-5-20251001"

# Una riga per fonte: tabella sqlite e nome per i messaggi Telegram.
FONTI = {
    "intercenter": dict(tabella="intercenter_avviso",
                         etichetta="Intercenter Emilia-Romagna"),
    "start_toscana": dict(tabella="start_avviso",
                           etichetta="START Toscana"),
}

# Stessa identica descrizione di ted_verdetto.py/suam_verdetto.py: e' lo
# stesso "chi siamo", non ha senso che i pareri valutino con criteri diversi
# a seconda della fonte del bando.
CHI_SIAMO = (
    "FlowLine e' una piccola agenzia italiana (4 persone) che costruisce "
    "automazioni su misura per la pubblica amministrazione: colleghiamo "
    "sistemi gia' in uso (gestionali, protocollo, banche dati, posta) "
    "eliminando passaggi manuali ripetitivi.\n\n"
    "Ambiti che sappiamo servire davvero: sviluppo software e integrazioni "
    "su misura, dati e analytics, gestione documentale, automazione di "
    "gestionali esistenti, consulenza IT/analisi processi.\n\n"
    "NON facciamo: rivendita di licenze software, connettivita'/telco, "
    "infrastruttura datacenter o hosting puro, hardware, e ovviamente nulla "
    "fuori dall'IT (assicurazioni, riscossione tributi, opere edili...).\n\n"
    "Siamo in 4: un bando enorme (valore molto alto, requisiti di fatturato "
    "pregresso o SLA su un sistema gia' in produzione fuori portata per una "
    "piccola agenzia, gara europea sopra soglia contro grandi player come "
    "Maggioli/Dedagroup/Engineering) e' 'forse' o 'no' anche se il tema e' "
    "giusto — non regge un progetto cosi' da soli."
)

PROMPT = """{chi_siamo}

Valuta se vale la pena rispondere a questo bando, con un parere netto.

Ente: {ente}
Titolo: {titolo}
Descrizione: {descrizione}
Tipologia appalto: {tipo}
Valore stimato: {valore}

Il titolo puo' essere un codice interno poco parlante o mancante — se la
descrizione c'e', e' quella il vero oggetto dell'appalto: basa il parere su
quella, non solo su importo e tipo di procedura.

Rispondi ESATTAMENTE in questo formato, due righe, niente altro prima o dopo:
VERDETTO: si|forse|no
MOTIVO: <una frase, massimo 20 parole, in italiano>
"""


def euro(v):
    return f"{v:,.0f} EUR".replace(",", ".") if v else "non indicato"


def valuta(bando, chiave):
    prompt = PROMPT.format(
        chi_siamo=CHI_SIAMO,
        ente=bando["ente"] or "non indicato",
        titolo=bando["titolo"] or "non indicato",
        descrizione=bando["descrizione"] or "non disponibile",
        tipo=bando["tipo"] or "non indicata",
        valore=euro(bando["importo"]),
    )
    dati = json.dumps({
        "model": MODELLO,
        "max_tokens": 150,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(API, data=dati, headers={
        "x-api-key": chiave,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        risposta = json.loads(r.read())
    testo = risposta["content"][0]["text"]
    m_v = re.search(r"VERDETTO:\s*(si|sì|forse|no)", testo, re.I)
    m_m = re.search(r"MOTIVO:\s*(.+)", testo, re.I)
    if not m_v:
        raise ValueError(f"risposta senza VERDETTO riconoscibile: {testo[:200]!r}")
    verdetto = m_v.group(1).lower().replace("sì", "si")
    motivo = m_m.group(1).strip()[:220] if m_m else None
    return verdetto, motivo


def allinea_schema(cx, tabella):
    # Stesso motivo di suam_verdetto.py: SQLite non ha 'ADD COLUMN IF NOT
    # EXISTS', su una tabella creata prima che questa colonna esistesse va
    # aggiunta a parte, ignorando l'errore se c'e' gia'.
    try:
        cx.execute(f"ALTER TABLE {tabella} ADD COLUMN notificato_il TEXT")
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e):
            raise
    cx.commit()


def pulisci_md(v):
    """Stessa funzione di pec_imap.py/suam_verdetto.py, duplicata qui
    apposta (vedi il commento su RUMORE in suam.py): senza, un ente con '_'
    o '*' nel nome rompe il messaggio Telegram."""
    return re.sub(r"[_*`\[\]]", " ", v or "")


def notifica_fattibili(cx, tabella, etichetta):
    righe = cx.execute(
        f"SELECT codice, ente, titolo, importo, scadenza, verdetto_motivo, link "
        f"FROM {tabella} WHERE verdetto='si' AND notificato_il IS NULL "
        f"AND scadenza >= date('now') ORDER BY scadenza").fetchall()
    if not righe:
        return 0

    testo = [f"{len(righe)} bando/i {etichetta} che sembrano fattibili per noi:\n"]
    for r in righe:
        gg = (date.fromisoformat(r["scadenza"]) - date.today()).days
        v = f"{r['importo']/1000:,.0f} k€" if r["importo"] else "importo n.d."
        testo.append(
            f"*{pulisci_md((r['ente'] or '?')[:50])}* — {v}, scade fra {gg} gg\n"
            f"{pulisci_md((r['titolo'] or '')[:120])}\n"
            f"_{pulisci_md(r['verdetto_motivo'] or '')}_\n"
            f"{r['link']}\n")
    invia_telegram("\n".join(testo))

    cx.executemany(
        f"UPDATE {tabella} SET notificato_il=CURRENT_TIMESTAMP WHERE codice=?",
        [(r["codice"],) for r in righe])
    cx.commit()
    return len(righe)


def main():
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--fonte", required=True, choices=sorted(FONTI),
                     help="quale tabella valutare")
    ap.add_argument("--dry-run", action="store_true",
                     help="elenca chi verrebbe valutato, non chiama l'API")
    ap.add_argument("--telegram", action="store_true",
                     help="dopo la valutazione, avvisa sui 'si' non ancora notificati")
    ap.add_argument("--tetto", type=int, default=60,
                     help="massimo bandi valutati in un giro (default 60)")
    a = ap.parse_args()
    tabella, etichetta = FONTI[a.fonte]["tabella"], FONTI[a.fonte]["etichetta"]

    if not os.path.exists(DB):
        sys.exit(f"radar.db non trovato: lancia prima {a.fonte}.py --ingest")

    cx = sqlite3.connect(DB)
    cx.row_factory = sqlite3.Row
    allinea_schema(cx, tabella)

    righe = cx.execute(
        f"SELECT codice, ente, titolo, descrizione, tipo, importo FROM {tabella} "
        f"WHERE scadenza >= date('now') AND verdetto IS NULL "
        f"ORDER BY scadenza LIMIT ?", (a.tetto,)).fetchall()

    if a.dry_run:
        if not righe:
            print("nessun bando aperto senza verdetto")
        else:
            print(f"{len(righe)} bandi verrebbero valutati:\n")
            for r in righe:
                print(f"  {r['codice']:12s} {(r['ente'] or '')[:40]}")
        cx.close()
        return 0

    if righe:
        chiave = conf("ANTHROPIC_API_KEY")
        if not chiave:
            sys.exit("ANTHROPIC_API_KEY mancante in .env.local")
        ok, falliti = 0, 0
        for r in righe:
            try:
                verdetto, motivo = valuta(r, chiave)
            except (urllib.error.URLError, ValueError, KeyError, TimeoutError) as e:
                # Stesso motivo di ted_verdetto.py/suam_verdetto.py: un
                # bando non valutato oggi resta verdetto IS NULL e si
                # rivaluta al prossimo giro.
                print(f"  fallito {r['codice']}: {e}")
                falliti += 1
                continue
            cx.execute(
                f"UPDATE {tabella} SET verdetto=?, verdetto_motivo=? WHERE codice=?",
                (verdetto, motivo, r["codice"]))
            cx.commit()
            ok += 1
        print(f"valutati {ok} bandi, {falliti} falliti")
    else:
        print("nessun bando aperto senza verdetto")

    if a.telegram:
        n = notifica_fattibili(cx, tabella, etichetta)
        print(f"avvisati {n} bando/i fattibili su Telegram" if n
              else "nessun bando fattibile nuovo da segnalare")

    cx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
