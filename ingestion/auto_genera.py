#!/usr/bin/env python3
"""
Generazione automatica di bozze PEC per i lead migliori (R30).

Ogni notte, dopo la lettura delle ricevute PEC, guarda i contratti in
scadenza con punteggio >= SOGLIA e sceglie fino a TETTO enti non ancora
contattati in nessun lotto. Per ciascuno scrive una PEC pronta nel lotto
fisso 'pec-auto' — la stessa funzione genera_pec.genera_singolo() che usa
anche il bottone "Genera PEC" della console, cosi' le due strade producono
esattamente lo stesso testo.

Non manda niente: la bozza resta da rivedere e inviare dalla console, come
tutte le altre — l'invio e' sempre e solo manuale. Soglia e tetto sono
scelte commerciali (quanto ci si fida del punteggio, quante bozze si riesce
a rivedere ogni mattina), non tecniche: si cambiano da riga di comando o
nel piano di job.py, non qui dentro.

Uso:
    python auto_genera.py                 # soglia 60, tetto 5
    python auto_genera.py --soglia 35 --tetto 10

Dipendenza: psycopg.
"""

import argparse
import json
import os
from datetime import datetime

import psycopg

import genera_pec
from push_supabase import leggi_dsn, maschera

LOTTO = "pec-auto"
QUI = os.path.dirname(os.path.abspath(__file__))
# Stesso posto dei log di job.py, gia' fuori da git: la console legge questo
# file per dire se il job di stanotte e' partito davvero, non solo se
# esistono bozze (che restano li' finche' non vengono inviate).
STATO = os.path.join(QUI, "logs", "auto-genera-stato.json")


def scrivi_stato(soglia, tetto, generate, saltati):
    os.makedirs(os.path.dirname(STATO), exist_ok=True)
    corpo = {
        "eseguito_il": datetime.now().isoformat(timespec="seconds"),
        "soglia": soglia,
        "tetto": tetto,
        "generate": len(generate),
        "candidati_scartati": len(saltati),
    }
    with open(STATO, "w", encoding="utf-8") as f:
        json.dump(corpo, f, ensure_ascii=False, indent=2)


def candidati(cur, soglia, tetto):
    # GROUP BY cf_ente: piu' contratti dello stesso ente non devono contare
    # come piu' candidati, genera_singolo li raggruppa comunque in una PEC
    # sola. Si chiede piu' del tetto perche' alcuni verranno scartati da
    # genera_singolo (gia' in un altro lotto, o fuori dalla sua finestra).
    cur.execute("""
        SELECT cf_ente, max(punteggio) AS pt
        FROM radar.v_scadenze
        WHERE fornitore_persona_fisica = 0
          AND tipologia_amm = 'Pubbliche Amministrazioni'
          AND pec IS NOT NULL AND cf_ente IS NOT NULL
          AND punteggio >= %s
        GROUP BY cf_ente
        ORDER BY pt DESC
        LIMIT %s""", (soglia, tetto * 4))
    return [r[0] for r in cur.fetchall()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--soglia", type=int, default=60,
                    help="punteggio minimo (bande R18: 60 eccezionale, 35 ottimo)")
    ap.add_argument("--tetto", type=int, default=5,
                    help="numero massimo di bozze generate in un giro")
    a = ap.parse_args()

    dsn = leggi_dsn()
    if not dsn:
        raise SystemExit("connessione non configurata: vedi ingestion/.env.local")

    generate, saltati = [], []
    try:
        with psycopg.connect(dsn, connect_timeout=30) as pg, pg.cursor() as cur:
            for cf in candidati(cur, a.soglia, a.tetto):
                if len(generate) >= a.tetto:
                    break
                r = genera_pec.genera_singolo(cur, cf, lotto=LOTTO)
                if r.get("generato"):
                    generate.append(r)
                    pg.commit()
                else:
                    saltati.append((cf, r.get("motivo")))
                    pg.rollback()
    except Exception as e:
        raise SystemExit(maschera(f"{type(e).__name__}: {e}", dsn))

    scrivi_stato(a.soglia, a.tetto, generate, saltati)

    print(f"{len(generate)} bozze generate in '{LOTTO}' "
          f"(soglia {a.soglia}, tetto {a.tetto}):")
    for r in generate:
        print(f"  #{r['progressivo']:02d} {r['ente'][:50]:52s} {r['n_contratti']} contr.")
    if saltati:
        print(f"{len(saltati)} enti sopra soglia scartati:")
        for cf, motivo in saltati[:10]:
            print(f"  - {cf}: {motivo}")


if __name__ == "__main__":
    main()
