#!/usr/bin/env python3
"""
Parere AI sui bandi SUAM aperti, e avviso Telegram su quelli fattibili.

Stessa idea di ted_verdetto.py (R34), altra fonte. Il SUAM (R40) non ha CPV
ne' un oggetto esteso come TED: la valutazione si appoggia solo su titolo,
tipologia appalto, ente e importo — meno contesto di TED, ma il titolo SUAM
e' quasi sempre gia' descrittivo (visto nei primi bandi reali: "gestione
integrata del Canone Unico...", "servizio di brokeraggio assicurativo").

A DIFFERENZA DI TED: qui non basta scrivere il parere, serve anche
AVVISARE — e' la richiesta esplicita dell'utente ("avvisami quando
usciranno quelli fattibili per noi"), TED non lo fa ancora. --telegram manda
un messaggio solo per i bandi con verdetto 'si' non ancora notificati
(WHERE notificato_il IS NULL): un bando fattibile viene segnalato una volta
sola, non ogni giorno finche' scade.

Uso:
    python suam_verdetto.py --dry-run     # elenca chi verrebbe valutato
    python suam_verdetto.py               # valuta e scrive in radar.db
    python suam_verdetto.py --telegram    # + avvisa sui 'si' non ancora notificati

Dipendenza: solo urllib — nessun pacchetto 'anthropic', stessa scelta di
ted_verdetto.py.
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

# Stessa identica descrizione di ted_verdetto.py: e' lo stesso "chi siamo",
# non ha senso che i due pareri (TED/SUAM) valutino con criteri diversi.
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
Tipologia appalto: {tipo}
Valore stimato: {valore}

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


ALTER = ("ALTER TABLE suam_avviso ADD COLUMN notificato_il TEXT",)


def allinea_schema(cx):
    # Stesso motivo di ted.py: SQLite non ha 'ADD COLUMN IF NOT EXISTS', e
    # su un database esistente (creato prima che questa colonna esistesse)
    # va aggiunta a parte, ignorando l'errore se c'e' gia'.
    for stmt in ALTER:
        try:
            cx.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e):
                raise
    cx.commit()


def pulisci_md(v):
    """Stessa funzione di pec_imap.py, duplicata qui apposta (vedi il
    commento su RUMORE in suam.py: tre righe non valgono un modulo
    condiviso). Senza, un ente con '_' o '*' nel nome rompe il messaggio."""
    return re.sub(r"[_*`\[\]]", " ", v or "")


def notifica_fattibili(cx):
    righe = cx.execute(
        "SELECT codice, ente, titolo, importo, scadenza, verdetto_motivo, link "
        "FROM suam_avviso WHERE verdetto='si' AND notificato_il IS NULL "
        "AND scadenza >= date('now') ORDER BY scadenza").fetchall()
    if not righe:
        return 0

    testo = [f"{len(righe)} bando/i SUAM Marche che sembrano fattibili per noi:\n"]
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
        "UPDATE suam_avviso SET notificato_il=CURRENT_TIMESTAMP WHERE codice=?",
        [(r["codice"],) for r in righe])
    cx.commit()
    return len(righe)


def main():
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                     help="elenca chi verrebbe valutato, non chiama l'API")
    ap.add_argument("--telegram", action="store_true",
                     help="dopo la valutazione, avvisa sui 'si' non ancora notificati")
    ap.add_argument("--tetto", type=int, default=60,
                     help="massimo bandi valutati in un giro (default 60)")
    a = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit("radar.db non trovato: lancia prima suam.py --ingest")

    cx = sqlite3.connect(DB)
    cx.row_factory = sqlite3.Row
    allinea_schema(cx)

    righe = cx.execute(
        "SELECT codice, ente, titolo, tipo, importo FROM suam_avviso "
        "WHERE scadenza >= date('now') AND verdetto IS NULL "
        "ORDER BY scadenza LIMIT ?", (a.tetto,)).fetchall()

    if a.dry_run:
        if not righe:
            print("nessun bando aperto senza verdetto")
        else:
            print(f"{len(righe)} bandi verrebbero valutati:\n")
            for r in righe:
                print(f"  {r['codice']:10s} {(r['ente'] or '')[:40]}")
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
                # Stesso motivo di ted_verdetto.py: un bando non valutato
                # oggi resta verdetto IS NULL e si rivaluta al prossimo giro.
                print(f"  fallito {r['codice']}: {e}")
                falliti += 1
                continue
            cx.execute(
                "UPDATE suam_avviso SET verdetto=?, verdetto_motivo=? WHERE codice=?",
                (verdetto, motivo, r["codice"]))
            cx.commit()
            ok += 1
        print(f"valutati {ok} bandi, {falliti} falliti")
    else:
        print("nessun bando aperto senza verdetto")

    if a.telegram:
        n = notifica_fattibili(cx)
        print(f"avvisati {n} bando/i fattibili su Telegram" if n
              else "nessun bando fattibile nuovo da segnalare")

    cx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
