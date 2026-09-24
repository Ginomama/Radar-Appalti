#!/usr/bin/env python3
"""
Parere AI sui bandi TED aperti (task R34).

Il problema che risolve: "leggi il bando" apre 20-30 pagine TED alla
settimana, ognuna con un oggetto di due pagine da capire se vale la pena
rispondere. La console mostra gia' titolo, CPV e valore, ma non basta a
capire al volo se e' nel nostro raggio d'azione (sviluppo/integrazioni/
gestionali) o fuori (licenze, telco, datacenter) — quello richiede leggere
l'oggetto per intero.

Un modello economico (Haiku) legge i campi gia' ingeriti da ted.py — non il
bando intero, che richiederebbe scaricare/parsare il PDF ufficiale — e
scrive un parere netto: si'/forse/no + una riga di motivo. E' un parere,
non un filtro: il bando resta visibile comunque, il "leggi il bando" resta
il posto dove si controlla davvero prima di scrivere.

Costo tenuto sotto controllo per costruzione: valuta solo i bandi ancora
aperti che non hanno gia' un verdetto (WHERE verdetto IS NULL), quindi ogni
bando viene giudicato una volta sola, mai ri-giudicato a ogni giro.

Uso:
    python ted_verdetto.py --dry-run     # elenca chi verrebbe valutato
    python ted_verdetto.py               # valuta e scrive in radar.db

Dipendenza: solo urllib — nessun pacchetto 'anthropic', stessa scelta di
notifica.py per Telegram.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request

QUI = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(QUI, "radar.db")
ENVFILE = os.path.join(QUI, ".env.local")
API = "https://api.anthropic.com/v1/messages"
MODELLO = "claude-haiku-4-5-20251001"

# Stessa fonte di verita' di COSA_FACCIAMO/RILEVANTI in genera_pec.py: se
# cambia li' cosa sappiamo servire, va cambiato anche qui.
CHI_SIAMO = (
    "FlowLine e' una piccola agenzia italiana (4 persone) che costruisce "
    "automazioni su misura per la pubblica amministrazione: colleghiamo "
    "sistemi gia' in uso (gestionali, protocollo, banche dati, posta) "
    "eliminando passaggi manuali ripetitivi.\n\n"
    "Ambiti che sappiamo servire davvero: sviluppo software e integrazioni "
    "su misura, dati e analytics, gestione documentale, automazione di "
    "gestionali esistenti, consulenza IT/analisi processi.\n\n"
    "NON facciamo: rivendita di licenze software, connettivita'/telco, "
    "infrastruttura datacenter o hosting puro, hardware.\n\n"
    "Siamo in 4: un bando enorme (valore molto alto, molti lotti, requisiti "
    "di fatturato pregresso fuori portata per una piccola agenzia) e' 'forse' "
    "o 'no' anche se il tema e' giusto — non regge un progetto cosi' da soli."
)

PROMPT = """{chi_siamo}

Valuta se vale la pena rispondere a questo bando, con un parere netto.

Ente: {ente}
Titolo: {titolo}
CPV: {cpv}
Natura: {natura}
Valore stimato: {valore}
Oggetto: {oggetto}

Rispondi ESATTAMENTE in questo formato, due righe, niente altro prima o dopo:
VERDETTO: si|forse|no
MOTIVO: <una frase, massimo 20 parole, in italiano>
"""


def conf(chiave):
    v = os.environ.get(chiave)
    if v:
        return v.strip()
    if not os.path.exists(ENVFILE):
        return None
    trovato = None
    with open(ENVFILE, encoding="utf-8") as f:
        for riga in f:
            riga = riga.strip()
            if riga.startswith("#") or "=" not in riga:
                continue
            k, _, val = riga.partition("=")
            if k.strip() == chiave:
                trovato = val.strip().strip('"').strip("'")
    return trovato or None


def euro(v):
    return f"{v:,.0f} EUR".replace(",", ".") if v else "non indicato"


def valuta(bando, chiave):
    prompt = PROMPT.format(
        chi_siamo=CHI_SIAMO,
        ente=bando["ente"] or "non indicato",
        titolo=bando["titolo"] or "non indicato",
        cpv=bando["cpv"] or "non indicato",
        natura=bando["natura"] or "non indicata",
        valore=euro(bando["valore"]),
        oggetto=(bando["oggetto"] or "nessun dettaglio disponibile")[:800],
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="elenca chi verrebbe valutato, non chiama l'API")
    ap.add_argument("--tetto", type=int, default=60,
                    help="massimo bandi valutati in un giro (default 60)")
    a = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit("radar.db non trovato: lancia prima ted.py --ingest")

    cx = sqlite3.connect(DB)
    cx.row_factory = sqlite3.Row
    righe = cx.execute(
        "SELECT numero, ente, titolo, cpv, natura, valore, oggetto "
        "FROM ted_avviso WHERE scadenza >= date('now') AND verdetto IS NULL "
        "ORDER BY scadenza LIMIT ?", (a.tetto,)).fetchall()

    if not righe:
        print("nessun bando aperto senza verdetto")
        return 0

    if a.dry_run:
        print(f"{len(righe)} bandi verrebbero valutati:\n")
        for r in righe:
            print(f"  {r['numero']:16s} {(r['ente'] or '')[:40]}")
        return 0

    chiave = conf("ANTHROPIC_API_KEY")
    if not chiave:
        sys.exit("ANTHROPIC_API_KEY mancante in .env.local")

    ok, falliti = 0, 0
    for r in righe:
        try:
            verdetto, motivo = valuta(r, chiave)
        except (urllib.error.URLError, ValueError, KeyError, TimeoutError) as e:
            # Un bando che non si valuta oggi si rivaluta al prossimo giro
            # (resta verdetto IS NULL): non e' un guasto che blocca gli altri.
            print(f"  fallito {r['numero']}: {e}")
            falliti += 1
            continue
        cx.execute(
            "UPDATE ted_avviso SET verdetto=?, verdetto_motivo=? WHERE numero=?",
            (verdetto, motivo, r["numero"]))
        cx.commit()
        ok += 1

    cx.close()
    print(f"valutati {ok} bandi, {falliti} falliti")
    return 1 if falliti and not ok else 0


if __name__ == "__main__":
    sys.exit(main())
