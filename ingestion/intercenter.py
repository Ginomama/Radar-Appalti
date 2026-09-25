"""Bandi aperti su Intercenter-ER (Emilia-Romagna) — task R41.

STESSA LOGICA DI SUAM (R40), UN'ALTRA REGIONE. Qui il portale e' un Plone
(design_italia_meta_type "Bando Intercenter") ed espone una vera REST API
pubblica, senza login e senza CSRF — molto meglio del form POST di SUAM:

    GET /.../bandi-altri-enti-aperti/@search?fullobjects=1&b_size=50&b_start=N
    Header: Accept: application/json

La cartella "bandi-altri-enti-aperti" e' gia' curata dalla fonte (contiene
solo bandi di ENTI DIVERSI da Intercent-ER, con termini di partecipazione
ancora aperti) — niente filtro di stato da ricostruire lato nostro, a
differenza di SUAM e di START Toscana.

Ogni bando arriva con un blocco @components["intercenter-data"]["data"] gia'
strutturato: ente_appaltante, importo_appalto, scadenza (ISO con timezone),
cig (lista per lotto — un bando multi-lotto ha piu' CIG, si prende quello
del primo lotto), procedura_gara, stato_procedura. Verificato dal vivo il
2026-09-25: 252 bandi in questa sezione, "items_total" nella risposta dice
quando fermare la paginazione.

Uso:
    python intercenter.py --gate      # vale la pena? volume e aggancio
    python intercenter.py --ingest    # carica intercenter_avviso
    python intercenter.py --aperti    # i bandi ancora aperti, dal piu' vicino

Solo libreria standard.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import date, datetime

QUI = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(QUI, "radar.db")

BASE = "https://intercenter.regione.emilia-romagna.it"
CARTELLA = ("/bandi-e-strumenti-di-acquisto/bandi-altri-enti/"
            "altri-enti-bandi-e-procedure-di-gara/bandi-altri-enti-aperti")
RICERCA = BASE + CARTELLA + "/@search"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

DIM_PAGINA = 50
TETTO_ITEM = 2000  # freno di sicurezza: oltre non vale piu' segnale sotto soglia

# Stessa lista di rumore di suam.py/ted.py: ogni fonte ha la sua copia
# apposta, sono tre righe, non vale un modulo condiviso per cosi' poco.
RUMORE = re.compile(
    r"\b(s\.?p\.?a\.?|s\.?r\.?l\.?|societa'?|spa|srl|scarl|"
    r"azienda|ente|istituto|comune di|citta' di|provincia di|regione)\b", re.I)


def richiesta_json(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def importo_it(v):
    if not v:
        return None
    m = re.search(r"[\d.]+,\d+|\d+", v)
    if not m:
        return None
    v = m.group(0).replace(".", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def data_iso(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def normalizza(item):
    d = (item.get("@components", {}).get("intercenter-data", {}) or {}).get("data") or {}
    codice = d.get("id") or item.get("id")
    if not codice:
        return None
    cig_lista = d.get("cig") or []
    cig = cig_lista[0].get("codice") if cig_lista else None
    return dict(
        codice=str(codice),
        ente=d.get("ente_appaltante"),
        titolo=d.get("title") or item.get("title"),
        # "titolo" spesso e' un codice interno poco parlante ("RIA 843/2026",
        # "APA 619"...) o addirittura "Senza Titolo": la "description"
        # dell'API e' il vero oggetto dell'appalto, e senza usarla il parere
        # AI (verdetto_regionale.py) ragiona quasi solo su importo/procedura
        # e tende al "si" generico — verificato dal vivo confrontando i
        # motivi con il tema reale del bando.
        descrizione=(d.get("description") or "").strip()[:600] or None,
        tipo=d.get("procedura_gara") or d.get("tipo_bando_gara"),
        importo=importo_it(d.get("importo_appalto")),
        pubblicato=data_iso(d.get("pubdate")),
        scadenza=data_iso(d.get("scadenza")),
        cig=cig,
        stato=d.get("stato_procedura"),
        link=item.get("@id"),
    )


def cerca_tutti():
    righe, inizio, totale = [], 0, None
    while totale is None or inizio < totale:
        url = f"{RICERCA}?fullobjects=1&b_size={DIM_PAGINA}&b_start={inizio}"
        pagina = richiesta_json(url)
        totale = pagina.get("items_total", 0)
        for item in pagina.get("items", []):
            r = normalizza(item)
            if r:
                righe.append(r)
        inizio += DIM_PAGINA
        if inizio >= TETTO_ITEM:
            print(f"  ATTENZIONE: raggiunto il tetto di sicurezza "
                  f"({TETTO_ITEM} bandi) — potrebbero mancarne.")
            break
    return righe


def norm(nome):
    if not nome:
        return None
    s = re.sub(r"[^\w\s]", " ", nome.lower())
    s = RUMORE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip() or None


def indice_enti(cx):
    m = {}
    for cf, den in cx.execute(
            "SELECT cf_ente, denominazione_ipa FROM ente_contatti "
            "WHERE denominazione_ipa IS NOT NULL"):
        k = norm(den)
        if k and k not in m:
            m[k] = cf
    return m


def aggancia(righe, indice):
    n = 0
    for r in righe:
        cf = indice.get(norm(r["ente"]))
        r["cf_ente"] = cf
        n += bool(cf)
    return n


DDL = """
CREATE TABLE IF NOT EXISTS intercenter_avviso (
    codice        TEXT PRIMARY KEY,
    ente          TEXT,
    cf_ente       TEXT,
    titolo        TEXT,
    descrizione   TEXT,
    tipo          TEXT,
    importo       REAL,
    pubblicato    TEXT,
    scadenza      TEXT,
    cig           TEXT,
    stato         TEXT,
    link          TEXT,
    ingerito_il   TEXT DEFAULT CURRENT_TIMESTAMP,
    verdetto        TEXT,
    verdetto_motivo TEXT,
    notificato_il   TEXT
);
CREATE INDEX IF NOT EXISTS ix_intercenter_scadenza ON intercenter_avviso (scadenza);
CREATE INDEX IF NOT EXISTS ix_intercenter_cf ON intercenter_avviso (cf_ente);
"""


def gate():
    righe = cerca_tutti()
    cx = sqlite3.connect(DB) if os.path.exists(DB) else None
    agganciati = 0
    if cx:
        agganciati = aggancia(righe, indice_enti(cx))
        cx.close()

    con_cig = sum(1 for r in righe if r["cig"])
    con_valore = [r for r in righe if r["importo"]]
    print(f"\n  bandi 'aperti' adesso su Intercenter Emilia-Romagna  {len(righe):>7,}")
    print(f"  ...agganciati a un nostro ente           {agganciati:>7,}")
    print(f"  ...con CIG                               {con_cig:>7,}")
    if con_valore:
        tot = sum(r["importo"] for r in con_valore)
        print(f"  ...con importo                           {len(con_valore):>7,}"
              f"  per {tot/1e6:,.1f} M€")

    oggi = date.today()
    # La cartella "aperti" della fonte non e' sempre ripulita in tempo reale:
    # capita di trovarci un bando con scadenza gia' passata (visto dal vivo
    # il 2026-09-25). Si filtra qui, non ci si fida solo del nome cartella.
    aperte = [r for r in righe if r["scadenza"] and r["scadenza"] >= oggi]
    if aperte:
        print("\n  le prossime scadenze:")
        for r in sorted(aperte, key=lambda x: x["scadenza"])[:10]:
            gg = (r["scadenza"] - oggi).days
            v = f"{r['importo']/1000:,.0f} k€" if r["importo"] else "—"
            print(f"    {r['scadenza']}  fra {gg} gg  {v:>12}  "
                  f"{(r['ente'] or '?')[:38]:40s} {(r['titolo'] or '')[:44]}")

    print("\n  " + "-" * 66)
    print(f"  Volume molto piu' alto di SUAM (una sola regione, ma un portale "
          f"condiviso\n  da centinaia di enti diversi) — qui vale la pena "
          f"usarlo come motore, non\n  solo come segnale.")
    print("  " + "-" * 66 + "\n")
    return 0


def ingest():
    righe = cerca_tutti()
    cx = sqlite3.connect(DB)
    cx.executescript(DDL)
    n_agg = aggancia(righe, indice_enti(cx))
    col = ["codice", "ente", "cf_ente", "titolo", "descrizione", "tipo", "importo",
           "pubblicato", "scadenza", "cig", "stato", "link"]
    cx.executemany(
        f"INSERT INTO intercenter_avviso ({', '.join(col)}) "
        f"VALUES ({', '.join('?' * len(col))}) "
        f"ON CONFLICT (codice) DO UPDATE SET "
        + ", ".join(f"{c}=excluded.{c}" for c in col[1:])
        + ", ingerito_il=CURRENT_TIMESTAMP",
        [tuple(str(r[c]) if isinstance(r[c], date) else r[c] for c in col)
         for r in righe])
    cx.commit()
    n = cx.execute("SELECT count(*) FROM intercenter_avviso").fetchone()[0]
    cx.close()
    print(f"intercenter_avviso: {n:,} righe in tutto, {len(righe):,} viste ora, "
          f"{n_agg:,} agganciate a un ente noto")
    return 0


def aperti():
    if not os.path.exists(DB):
        sys.exit("radar.db non trovato: lancia prima --ingest")
    cx = sqlite3.connect(DB)
    cx.row_factory = sqlite3.Row
    try:
        righe = cx.execute(
            "SELECT * FROM intercenter_avviso WHERE scadenza >= date('now') "
            "ORDER BY scadenza LIMIT 60").fetchall()
    except sqlite3.OperationalError:
        sys.exit("tabella intercenter_avviso assente: lancia prima --ingest")
    if not righe:
        print("\n  nessun bando aperto in archivio. Rilancia --ingest.\n")
        return 0
    print(f"\n=== {len(righe)} bandi Intercenter Emilia-Romagna ancora aperti ===\n")
    for r in righe:
        gg = (date.fromisoformat(r["scadenza"]) - date.today()).days
        v = f"{r['importo']/1000:,.0f} k€" if r["importo"] else "—"
        noto = " ●" if r["cf_ente"] else "  "
        print(f"  {r['scadenza']}  fra {gg:>3} gg{noto} {v:>12}  "
              f"{(r['ente'] or '?')[:40]}")
        print(f"      {(r['titolo'] or '')[:74]}")
        print(f"      {r['link']}")
    print("\n  ● = ente che conosciamo gia' dallo storico ANAC.\n")
    cx.close()
    return 0


def main():
    # Console Windows in cp1252: stessa riga di suam.py/esito.py.
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--ingest", action="store_true")
    ap.add_argument("--aperti", action="store_true")
    a = ap.parse_args()
    if a.aperti:
        return aperti()
    if a.ingest:
        return ingest()
    return gate()


if __name__ == "__main__":
    sys.exit(main())
