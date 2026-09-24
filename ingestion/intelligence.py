#!/usr/bin/env python3
"""
Chi presidia quale ente (task R36).

Il problema che risolve: `scheda.py` dice gia' "chi glieli tiene" per UN
ente alla volta ("Chi glieli tiene" nella scheda, cliccando dentro dalla
console) — ma per farsi un'idea di chi domina il mercato IT della PA nel
complesso bisognerebbe aprire migliaia di schede una per una. Qui la stessa
domanda si fa per tutti gli enti insieme: per ognuno, chi e' il fornitore
che vale di piu' nel suo storico.

Perche' e' precalcolato, non live. La query aggrega cig+aggiudicatari+
aggiudicazioni su 21mila enti: ~5 secondi in locale, troppo per ogni
apertura della console (che gia' soffre di connessioni lente verso
Supabase). Si calcola una volta al mese, nello stesso giro che rifà
`punteggio.py --calibra` — i dati sorgente (aggiudicazioni) cambiano alla
stessa cadenza.

Non e' un filtro di liceita' (R3): quello riguarda i CONTATTI degli enti
(non scrivere a persone fisiche), qui si guardano i fornitori vincitori,
dato pubblico ANAC, uso puramente informativo.

Uso:
    python intelligence.py --calcola   # ricalcola e scrive in radar.db

Solo libreria standard.
"""

import argparse
import os
import sqlite3
import sys

QUI = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(QUI, "radar.db")

DDL = """
CREATE TABLE IF NOT EXISTS ente_fornitore_dominante (
    cf_ente         TEXT PRIMARY KEY,
    ente            TEXT,
    prov            TEXT,
    cf_fornitore    TEXT,
    fornitore       TEXT,
    contratti       INTEGER,
    valore          REAL,
    ultimo          TEXT,
    calcolato_il    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_efd_valore ON ente_fornitore_dominante (valore DESC);
"""

# Per ogni coppia ente/fornitore, quanto vale in totale e in quanti
# contratti — poi si tiene solo il fornitore che vale di piu' per ogni
# ente (stessa logica di scheda.py, ma per tutti gli enti in un colpo solo
# invece che uno alla volta).
CALCOLA = """
WITH coppie AS (
    SELECT c.cf_amministrazione_appaltante AS cf_ente,
           max(c.denominazione_amministrazione_appaltante) AS ente,
           max(c.provincia) AS prov,
           g.codice_fiscale AS cf_fornitore,
           max(g.denominazione) AS fornitore,
           count(DISTINCT g.cig) AS contratti,
           sum(a.importo_aggiudicazione) AS valore,
           max(c.data_pubblicazione) AS ultimo
    FROM cig c
    JOIN aggiudicatari g ON g.cig = c.cig
    LEFT JOIN aggiudicazioni a ON a.id_aggiudicazione = g.id_aggiudicazione
    WHERE c.cf_amministrazione_appaltante IS NOT NULL
      AND g.codice_fiscale IS NOT NULL
    GROUP BY cf_ente, cf_fornitore
),
classificate AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY cf_ente ORDER BY valore DESC, contratti DESC
    ) AS pos
    FROM coppie
)
SELECT cf_ente, ente, prov, cf_fornitore, fornitore, contratti, valore, ultimo
FROM classificate WHERE pos = 1
"""


def calcola():
    cx = sqlite3.connect(DB)
    cx.executescript(DDL)
    cx.execute("DELETE FROM ente_fornitore_dominante")
    righe = cx.execute(CALCOLA).fetchall()
    cx.executemany(
        "INSERT INTO ente_fornitore_dominante "
        "(cf_ente, ente, prov, cf_fornitore, fornitore, contratti, valore, ultimo) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", righe)
    cx.commit()
    n = cx.execute("SELECT count(*) FROM ente_fornitore_dominante").fetchone()[0]
    cx.close()
    print(f"ente_fornitore_dominante: {n:,} enti")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calcola", action="store_true")
    a = ap.parse_args()
    if not a.calcola:
        ap.print_help()
        return 0
    if not os.path.exists(DB):
        sys.exit("radar.db non trovato: lancia prima ingest.py")
    return calcola()


if __name__ == "__main__":
    sys.exit(main())
