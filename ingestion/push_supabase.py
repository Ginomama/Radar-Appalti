#!/usr/bin/env python3
"""
Push del livello di servizio: radar.db (locale) -> Supabase.

Su Supabase va SOLO dato derivato. Le tabelle grezze restano qui: sul piano
Free il database va in sola lettura oltre i 500 MB, e i 686.000 record grezzi
ci arriverebbero in pochi mesi. Le tre viste stanno in ~30 MB e non crescono.

Strategia per tabella: TRUNCATE + COPY dentro UNA transazione.
Non un UPSERT: l'insieme si RIDUCE nel tempo (un contratto scaduto esce dalle
scadenze) e un upsert lascerebbe righe fantasma. Il TRUNCATE dentro la
transazione e' atomico — chi legge vede i dati vecchi fino al COMMIT, mai una
tabella vuota.

Configurazione (mai in un file versionato):
    setx RADAR_SUPABASE_DSN "postgresql://postgres.<REF>:<PWD>@aws-<REGION>.pooler.supabase.com:5432/postgres"

Dipendenza: psycopg[binary]. E' l'unica del progetto, e sta solo qui:
l'ingestion resta a libreria standard.

Uso:
    python push_supabase.py --dry-run     # non si connette, mostra cosa spedirebbe
    python push_supabase.py               # push vero
"""

import argparse
import os
import sqlite3
import sys
from datetime import date, datetime

QUI = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(QUI, "radar.db")
SCHEMA_PG = os.path.join(QUI, "schema_supabase.sql")

# vista locale -> (tabella Supabase, colonne sorgente, colonne destinazione, filtro)
# Le colonne sono quelle di docs/setup-supabase.md. giorni_alla_scadenza NON
# viene spedito: si calcola nella vista radar.v_scadenze a ogni query, perche'
# salvarlo significherebbe avere un numero sbagliato il giorno dopo.
MAPPA = [
    # v_scadenze_contattabili = v_scadenze_prossime + recapiti IndicePA.
    # I contatti sono denormalizzati qui dentro invece che in una tabella a
    # parte: n8n legge una riga sola e ha il lead completo, senza join.
    ("v_scadenze_contattabili", "radar.scadenze", [
        "cig", "oggetto_lotto", "cpv_norm", "provincia", "ente",
        "cf_amministrazione_appaltante", "data_stipula_contratto",
        "data_termine_contrattuale", "importo_aggiudicazione",
        "fornitore_uscente", "n_fornitori", "cf_fornitore_uscente",
        "pec", "mail_alt", "denominazione_ipa", "tipologia_amm",
        "sito_istituzionale", "categoria", "fornitore_persona_fisica",
        "punteggio", "prob_apertura", "valore_atteso",
    ], [
        "cig", "oggetto_lotto", "cpv_norm", "provincia", "ente",
        "cf_ente", "data_stipula_contratto",
        "data_termine_contrattuale", "importo_aggiudicazione",
        "fornitore_uscente", "n_fornitori", "cf_fornitore_uscente",
        "pec", "mail_alt", "denominazione_ipa", "tipologia_amm",
        "sito_istituzionale", "categoria", "fornitore_persona_fisica",
        "punteggio", "prob_apertura", "valore_atteso",
    ], ""),
    ("v_competitor", "radar.competitor", [
        "codice_fiscale", "vincitore", "gare_vinte", "valore_vinto",
        "gare_competitive", "ribasso_medio_competitive", "concorrenti_medi",
        "enti_serviti", "province_presidiate", "prima_gara", "ultima_gara",
    ], None, ""),
    ("v_ente", "radar.ente", [
        "cf_ente", "ente", "provincia", "lotti", "gare", "importo_bandito",
        "importo_aggiudicato", "gare_competitive", "ribasso_medio_competitive",
        "concorrenti_medi", "lotti_pnrr",
    ], None,
     # 1 riga su 20.929 ha cf_ente NULL (il CIG con cf_amministrazione_appaltante
     # vuoto): su Supabase e' chiave primaria, il COPY fallirebbe.
     "WHERE cf_ente IS NOT NULL AND trim(cf_ente) <> ''"),
    # R17. Va spedita anche se e' storia passata: e' quella che dice a n8n
    # (e a chi guarda la console) se su quell'ente conviene insistere.
    ("v_esito", "radar.esito", [
        "cig", "cf_ente", "ente", "provincia", "oggetto", "cod_cpv",
        "data_termine", "importo", "fornitore_uscente", "cig_seguito",
        "tipo_scelta_seguito", "fornitore_subentrante", "esito",
        "contendibile", "punteggio",
    ], None, ""),
]

# Colonne che in Postgres sono date: SQLite le tiene come testo ISO, e le
# stringhe vuote vanno convertite in NULL o COPY fallisce.
COL_DATA = {"data_stipula_contratto", "data_termine_contrattuale",
            "prima_gara", "ultima_gara", "data_termine"}


def leggi(cx, vista, colonne, filtro=""):
    sql = f"SELECT {', '.join(colonne)} FROM {vista} {filtro}"
    cur = cx.execute(sql)
    for r in cur:
        yield [pulisci(c, v) for c, v in zip(colonne, r)]


def pulisci(colonna, v):
    if v is None:
        return None
    if colonna in COL_DATA:
        s = str(v).strip()[:10]
        if len(s) != 10 or not s[:4].isdigit():
            return None
        try:                                  # scarta 0001-01-01 e simili
            a = int(s[:4])
            return s if 1990 <= a <= 2100 else None
        except ValueError:
            return None
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return v


def dry_run():
    cx = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    print(f"sorgente: {DB}\n")
    tot_righe = 0
    for vista, tabella, col_src, col_dst, filtro in MAPPA:
        col_dst = col_dst or col_src
        n = cx.execute(f"SELECT count(*) FROM {vista} {filtro}").fetchone()[0]
        tot_righe += n
        print(f"  {vista:24s} -> {tabella:20s} {n:7,} righe, "
              f"{len(col_dst)} colonne")
        prima = next(leggi(cx, vista, col_src, filtro), None)
        if prima:
            campioni = ", ".join(
                f"{c}={str(v)[:26]!r}" for c, v in list(zip(col_dst, prima))[:4])
            print(f"      esempio: {campioni}")
        mancanti = [c for c, v in zip(col_dst, prima or []) if v is None]
        if mancanti:
            print(f"      NULL nella prima riga: {', '.join(mancanti)}")
    print(f"\n  totale {tot_righe:,} righe da spedire")
    print("\n  (dry-run: nessuna connessione aperta)")
    cx.close()


def push(dsn):
    try:
        import psycopg
    except ImportError:
        sys.exit("psycopg non installato. Esegui:  pip install \"psycopg[binary]\"")

    cx = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    with psycopg.connect(dsn, connect_timeout=30) as pg:
        # Lo schema si allinea PRIMA del push. Le viste Postgres congelano le
        # colonne alla creazione: gestirle a mano nel dashboard le lascia
        # indietro rispetto al codice, ed e' gia' successo due volte.
        with open(SCHEMA_PG, encoding="utf-8") as f:
            with pg.cursor() as cur:
                cur.execute(f.read())
        pg.commit()
        print("  schema allineato")

        for vista, tabella, col_src, col_dst, filtro in MAPPA:
            col_dst = col_dst or col_src
            inizio = datetime.now()
            n = 0
            try:
                with pg.transaction():
                    with pg.cursor() as cur:
                        cur.execute(f"TRUNCATE {tabella}")
                        lista = ", ".join(col_dst)
                        with cur.copy(
                                f"COPY {tabella} ({lista}) FROM STDIN") as cp:
                            for riga in leggi(cx, vista, col_src, filtro):
                                cp.write_row(riga)
                                n += 1
                        cur.execute(
                            "INSERT INTO radar.sync_log "
                            "(tabella, righe, esito, iniziato_il, finito_il) "
                            "VALUES (%s, %s, 'ok', %s, now())",
                            (tabella, n, inizio))
                print(f"  {tabella:20s} {n:7,} righe  ok")
            except Exception as e:
                # il log dell'errore va fuori dalla transazione fallita
                with pg.cursor() as cur:
                    cur.execute(
                        "INSERT INTO radar.sync_log "
                        "(tabella, righe, esito, iniziato_il, finito_il, note) "
                        "VALUES (%s, %s, 'errore', %s, now(), %s)",
                        (tabella, n, inizio, str(e)[:500]))
                pg.commit()
                print(f"  {tabella:20s} ERRORE: {str(e)[:160]}")
                raise
    cx.close()
    print("\npush completato")


ENVFILE = os.path.join(QUI, ".env.local")


def leggi_dsn():
    """
    Prima la variabile d'ambiente, poi ingestion/.env.local (git-ignored).

    Il file serve perche' setx scrive nel registro di Windows e i processi
    gia' avviati non lo rileggono: senza riavviare il terminale la variabile
    non arriva. Il file invece si legge subito.
    """
    dsn = os.environ.get("RADAR_SUPABASE_DSN")
    if dsn:
        return dsn.strip()
    if os.path.exists(ENVFILE):
        # utf-8-sig: il Blocco note di Windows aggiunge il BOM senza dirlo
        for riga in open(ENVFILE, encoding="utf-8-sig"):
            riga = riga.strip()
            if not riga or riga.startswith("#"):
                continue
            if riga.startswith("RADAR_SUPABASE_DSN"):
                riga = riga.split("=", 1)[1]
            riga = riga.strip().strip('"').strip("'")
            if riga.startswith("postgres"):     # accetta anche il DSN nudo
                return riga
    return None


def maschera(testo, dsn):
    """
    Toglie la password da qualunque testo prima di stamparlo. Un traceback di
    psycopg puo' contenere la stringa di connessione per intero.
    """
    if not dsn:
        return testo
    testo = testo.replace(dsn, "<DSN>")
    if "://" in dsn and "@" in dsn:
        cred = dsn.split("://", 1)[1].split("@", 1)[0]
        if ":" in cred:
            pwd = cred.split(":", 1)[1]
            if len(pwd) > 3:
                testo = testo.replace(pwd, "<PASSWORD>")
    return testo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="mostra cosa spedirebbe, senza connettersi")
    a = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit(f"database non trovato: {DB}")
    if a.dry_run:
        dry_run()
        return

    dsn = leggi_dsn()
    if not dsn:
        sys.exit(
            "Connessione non configurata. Due modi, uno dei due:\n\n"
            "  1) file  ingestion/.env.local  (git-ignored) con dentro:\n"
            "     RADAR_SUPABASE_DSN=postgresql://postgres.<REF>:<PWD>"
            "@aws-<REGION>.pooler.supabase.com:5432/postgres\n\n"
            "  2) variabile d'ambiente RADAR_SUPABASE_DSN\n"
            "     (con setx serve riaprire il terminale)")
    if not dsn.startswith("postgres"):
        sys.exit("il DSN non sembra una stringa di connessione postgresql://")
    if ".pooler.supabase.com" not in dsn:
        print("[attenzione] non stai usando la Session pooler: la connessione "
              "diretta e' solo IPv6 sul piano Free.", file=sys.stderr)
    try:
        push(dsn)
    except Exception as e:                     # niente password nei log
        sys.exit(maschera(f"{type(e).__name__}: {e}", dsn))


if __name__ == "__main__":
    main()
