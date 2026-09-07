#!/usr/bin/env python3
"""
Backup del Radar (task R22).

COSA SI PERDE DAVVERO. Le tabelle non sono tutte uguali:

  radar.invio, radar.notificato   esistono SOLO su Supabase. Nessuno le
                                  rigenera: sono la memoria di quali PEC sono
                                  partite, a chi, quando, e chi ha risposto.
                                  Perderle significa non sapere piu' a chi hai
                                  gia' scritto — e riscrivere allo stesso
                                  protocollo e' l'errore che si nota di piu'.

  radar.scadenze, ente,           vengono ricostruite da push_supabase.py a
  competitor                      partire dal SQLite locale. Perderle costa un
                                  push, non un disastro.

  radar.db (SQLite)               ricostruibile da ANAC, ma sono ore di
                                  download e il WAF non ama la fretta.

Quindi il backup di default salva le prime due — sono minuscole, si possono
fare ogni giorno — e con --completo aggiunge anche il SQLite.

PERCHE' SERVE. Il piano Supabase Free non ha point-in-time recovery, e
push_supabase.py lavora in TRUNCATE + COPY: un push partito su una fonte
corrotta svuota le tabelle e non c'e' undo. Il SQLite locale e' la sola copia,
e sta su una macchina sola.

DOVE FINISCE. Fuori da OneDrive, di proposito: una cartella sincronizzata
propaga in pochi secondi anche la cancellazione, e un backup che sparisce
insieme all'originale non e' un backup.

Uso:
    python backup.py                      # tabelle operative, rotazione a 12
    python backup.py --completo           # aggiunge il SQLite compresso
    python backup.py --elenco             # cosa c'e' salvato
    python backup.py --verifica ULTIMO    # rilegge un backup e lo valida
    python backup.py --ripristina FILE    # riscrive radar.invio e notificato

Dipendenza: psycopg. Il resto e' libreria standard.
"""

import argparse
import gzip
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime

import psycopg
from push_supabase import leggi_dsn, maschera

QUI = os.path.dirname(os.path.abspath(__file__))
SQLITE = os.path.join(QUI, "radar.db")

# Le tabelle senza le quali non si torna indietro.
OPERATIVE = ("invio", "notificato")

# Quante generazioni tenere. Le operative pesano decine di KB: tenerne dodici
# costa niente e copre un errore accorto dopo settimane. Il SQLite pesa
# centinaia di MB, quindi tre.
GENERAZIONI_OPERATIVE = 12
GENERAZIONI_COMPLETE = 3


def destinazione(scelta=None):
    """Fuori da OneDrive, salvo indicazione diversa."""
    if scelta:
        return os.path.abspath(scelta)
    base = (os.environ.get("RADAR_BACKUP_DIR")
            or os.environ.get("LOCALAPPDATA")
            or os.path.expanduser("~"))
    return os.path.join(base, "radar-backup")


def avvisa_se_sincronizzata(cartella):
    spie = ("onedrive", "dropbox", "google drive", "icloud")
    giu = cartella.lower()
    for s in spie:
        if s in giu:
            print(f"[avviso] la destinazione sta dentro {s}: una cartella "
                  f"sincronizzata propaga anche le cancellazioni. Meglio un "
                  f"percorso locale o un disco esterno (--dove).",
                  file=sys.stderr)
            return


# ------------------------------------------------------------- estrazione
def estrai(cur, tabella):
    """Righe come lista di dizionari, con i tipi resi serializzabili."""
    cur.execute(f"SELECT * FROM radar.{tabella}")
    colonne = [d[0] for d in cur.description]
    righe = []
    for r in cur.fetchall():
        d = {}
        for c, v in zip(colonne, r):
            d[c] = v.isoformat() if hasattr(v, "isoformat") else v
        righe.append(d)
    return colonne, righe


def salva_operative(dsn, cartella):
    quando = datetime.now()
    dati = {"generato": quando.isoformat(timespec="seconds"),
            "sorgente": "supabase", "tabelle": {}}
    with psycopg.connect(dsn, connect_timeout=30) as pg, pg.cursor() as cur:
        for t in OPERATIVE:
            colonne, righe = estrai(cur, t)
            dati["tabelle"][t] = {"colonne": colonne, "righe": righe}
            print(f"  radar.{t:12s} {len(righe):6d} righe")

    nome = f"operative-{quando:%Y%m%d-%H%M%S}.json.gz"
    percorso = os.path.join(cartella, nome)
    with gzip.open(percorso, "wt", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False)
    return percorso


def salva_sqlite(cartella):
    """VACUUM INTO invece di copiare il file: fa una copia coerente anche se
    un altro processo sta scrivendo, cosa che copy() non garantisce."""
    if not os.path.exists(SQLITE):
        print("  [salto] radar.db non trovato", file=sys.stderr)
        return None
    quando = datetime.now()
    grezzo = os.path.join(cartella, f".tmp-{quando:%Y%m%d-%H%M%S}.db")
    with sqlite3.connect(SQLITE) as cx:
        cx.execute("VACUUM INTO ?", (grezzo,))

    finale = os.path.join(cartella, f"radar-{quando:%Y%m%d-%H%M%S}.db.gz")
    with open(grezzo, "rb") as sorgente, gzip.open(finale, "wb", 6) as dest:
        shutil.copyfileobj(sorgente, dest, 1024 * 1024)
    os.remove(grezzo)
    return finale


# ------------------------------------------------------------- rotazione
def ruota(cartella, prefisso, tieni):
    trovati = sorted(f for f in os.listdir(cartella)
                     if f.startswith(prefisso) and not f.startswith(".tmp"))
    for vecchio in trovati[:-tieni] if tieni else []:
        os.remove(os.path.join(cartella, vecchio))
        print(f"  rimosso il piu' vecchio: {vecchio}")


# ------------------------------------------------------------- verifica
def verifica(percorso):
    """Un backup mai riletto non e' un backup: si apre e si contano le righe."""
    with gzip.open(percorso, "rt", encoding="utf-8") as f:
        d = json.load(f)
    print(f"  generato il {d.get('generato', '?')}")
    ok = True
    for t in OPERATIVE:
        blocco = d.get("tabelle", {}).get(t)
        if blocco is None:
            print(f"  MANCA la tabella {t}")
            ok = False
            continue
        print(f"  radar.{t:12s} {len(blocco['righe']):6d} righe, "
              f"{len(blocco['colonne'])} colonne")
    return ok


def ultimo(cartella, prefisso="operative-"):
    trovati = sorted(f for f in os.listdir(cartella) if f.startswith(prefisso))
    if not trovati:
        sys.exit(f"nessun backup '{prefisso}*' in {cartella}")
    return os.path.join(cartella, trovati[-1])


# ------------------------------------------------------------ ripristino
def ripristina(dsn, percorso):
    with gzip.open(percorso, "rt", encoding="utf-8") as f:
        d = json.load(f)

    print(f"Backup del {d.get('generato', '?')}:")
    for t in OPERATIVE:
        print(f"  radar.{t:12s} {len(d['tabelle'][t]['righe']):6d} righe")
    print("\nIl ripristino SOSTITUISCE il contenuto attuale di queste tabelle.")
    if input("Scrivi RIPRISTINA per procedere: ").strip() != "RIPRISTINA":
        sys.exit("annullato.")

    with psycopg.connect(dsn, connect_timeout=30) as pg:
        with pg.cursor() as cur:
            for t in OPERATIVE:
                blocco = d["tabelle"][t]
                colonne = blocco["colonne"]
                # id e' GENERATED ALWAYS: si lascia rigenerare.
                usate = [c for c in colonne if c != "id"]
                cur.execute(f"TRUNCATE radar.{t}")
                if not blocco["righe"]:
                    continue
                segna = ", ".join(["%s"] * len(usate))
                cur.executemany(
                    f"INSERT INTO radar.{t} ({', '.join(usate)}) "
                    f"VALUES ({segna})",
                    [[r.get(c) for c in usate] for r in blocco["righe"]])
                print(f"  radar.{t} ripristinata")
        pg.commit()
    print("\nfatto.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dove", help="cartella di destinazione")
    ap.add_argument("--completo", action="store_true",
                    help="salva anche il SQLite locale compresso")
    ap.add_argument("--elenco", action="store_true")
    ap.add_argument("--verifica", metavar="FILE|ULTIMO")
    ap.add_argument("--ripristina", metavar="FILE|ULTIMO")
    a = ap.parse_args()

    cartella = destinazione(a.dove)
    os.makedirs(cartella, exist_ok=True)
    avvisa_se_sincronizzata(cartella)

    if a.elenco:
        voci = sorted(f for f in os.listdir(cartella) if not f.startswith(".tmp"))
        if not voci:
            sys.exit(f"nessun backup in {cartella}")
        print(f"=== {cartella} ===\n")
        for f in voci:
            p = os.path.join(cartella, f)
            mb = os.path.getsize(p) / 1e6
            quando = datetime.fromtimestamp(os.path.getmtime(p))
            print(f"  {f:44s} {mb:8.2f} MB   {quando:%d/%m/%Y %H:%M}")
        return

    if a.verifica:
        p = ultimo(cartella) if a.verifica == "ULTIMO" else a.verifica
        print(f"=== {os.path.basename(p)} ===")
        sys.exit(0 if verifica(p) else 1)

    dsn = leggi_dsn()
    if not dsn:
        sys.exit("connessione non configurata: vedi ingestion/.env.local")

    if a.ripristina:
        p = ultimo(cartella) if a.ripristina == "ULTIMO" else a.ripristina
        try:
            ripristina(dsn, p)
        except Exception as e:
            sys.exit(maschera(f"{type(e).__name__}: {e}", dsn))
        return

    print(f"Backup in {cartella}\n")
    try:
        p = salva_operative(dsn, cartella)
    except Exception as e:
        sys.exit(maschera(f"{type(e).__name__}: {e}", dsn))
    print(f"  -> {os.path.basename(p)} "
          f"({os.path.getsize(p)/1024:.0f} KB)")

    # Si rilegge subito: un file scritto e mai aperto non prova nulla.
    print("\n  rilettura di controllo:")
    if not verifica(p):
        sys.exit("il backup appena scritto non e' valido.")

    ruota(cartella, "operative-", GENERAZIONI_OPERATIVE)

    if a.completo:
        print("\n  SQLite locale (puo' richiedere qualche minuto):")
        q = salva_sqlite(cartella)
        if q:
            print(f"  -> {os.path.basename(q)} "
                  f"({os.path.getsize(q)/1e6:.0f} MB)")
            ruota(cartella, "radar-", GENERAZIONI_COMPLETE)

    print("\nfatto.")


if __name__ == "__main__":
    main()
