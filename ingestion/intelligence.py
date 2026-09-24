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

R38 — ANOMALIE. Stessa passata calcola anche gli importi sproporzionati: un
CIG il cui importo e' almeno 20 volte il secondo piu' grande dello stesso
ente (o l'unico mai visto, sopra soglia), es. COMUNE DI BAGALADI con un
contratto da 3 miliardi contro contratti da poche migliaia di euro — quasi
certamente un errore di inserimento a monte in ANAC, non qualcosa che
abbiamo calcolato male noi. Non si filtra nulla: si segnala, la console
mostra il numero cosi' com'e' mostrando anche il sospetto, la decisione se
fidarsene resta di chi legge (alcuni, come le Agenzie fiscali, potrebbero
essere contratti-quadro nazionali genuinamente enormi, non errori).

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

CREATE TABLE IF NOT EXISTS anomalia_importo (
    cig             TEXT PRIMARY KEY,
    cf_ente         TEXT,
    ente            TEXT,
    prov            TEXT,
    fornitore       TEXT,
    importo         REAL,
    secondo_importo REAL,
    rapporto        REAL,
    calcolato_il    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_anom_importo ON anomalia_importo (importo DESC);
"""

# Soglia doppia apposta: sopra i 50M E' ALMENO 20 VOLTE il secondo contratto
# piu' grande dello stesso ente (o l'unico mai visto). Un'agenzia che compra
# sempre in grande non scatta — ha altri contratti nello stesso ordine di
# grandezza; scatta solo chi ha UN valore fuori scala contro tutto il resto
# del suo storico, la firma di un dato inserito male, non di una spesa vera.
SOGLIA_ANOMALIA = 50_000_000
RAPPORTO_ANOMALIA = 20

ANOMALIE = f"""
WITH per_cig AS (
    SELECT c.cf_amministrazione_appaltante AS cf_ente,
           max(c.denominazione_amministrazione_appaltante) AS ente,
           max(c.provincia) AS prov,
           c.cig,
           max(g.denominazione) AS fornitore,
           max(a.importo_aggiudicazione) AS importo
    FROM cig c
    JOIN aggiudicatari g ON g.cig = c.cig
    LEFT JOIN aggiudicazioni a ON a.id_aggiudicazione = g.id_aggiudicazione
    WHERE a.importo_aggiudicazione IS NOT NULL
      AND c.cf_amministrazione_appaltante IS NOT NULL
    GROUP BY c.cig
),
classificate AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY cf_ente ORDER BY importo DESC
    ) AS pos
    FROM per_cig
)
SELECT p1.cig, p1.cf_ente, p1.ente, p1.prov, p1.fornitore, p1.importo,
       p2.importo AS secondo_importo,
       CASE WHEN p2.importo > 0 THEN p1.importo / p2.importo END AS rapporto
FROM classificate p1
LEFT JOIN classificate p2 ON p2.cf_ente = p1.cf_ente AND p2.pos = 2
WHERE p1.pos = 1 AND p1.importo >= {SOGLIA_ANOMALIA}
  AND (p2.importo IS NULL OR p1.importo >= {RAPPORTO_ANOMALIA} * p2.importo)
ORDER BY p1.importo DESC
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
    cx.execute("DELETE FROM anomalia_importo")
    anomale = cx.execute(ANOMALIE).fetchall()
    cx.executemany(
        "INSERT INTO anomalia_importo "
        "(cig, cf_ente, ente, prov, fornitore, importo, secondo_importo, rapporto) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", anomale)
    cx.commit()
    n = cx.execute("SELECT count(*) FROM ente_fornitore_dominante").fetchone()[0]
    na = cx.execute("SELECT count(*) FROM anomalia_importo").fetchone()[0]
    cx.close()
    print(f"ente_fornitore_dominante: {n:,} enti")
    print(f"anomalia_importo: {na} contratti sproporzionati segnalati")
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
