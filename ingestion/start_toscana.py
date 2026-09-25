"""Bandi aperti su START (Sistema Telematico Acquisti Regionale Toscana) — R42.

STESSA LOGICA DI SUAM/INTERCENTER, UN'ALTRA REGIONE. START e' un portale
Pleiade (stesso motore, versione diversa) con un limite in piu' rispetto a
SUAM: la lista e' HTML server-side come li', ma non porta la SCADENZA come
colonna — solo Oggetto/Tipo/CIG/Importo/Stato/Data di pubblicazione. Senza
scadenza non si può ordinare "quanto manca" ne' filtrare gli aperti davvero,
quindi qui la si va a prendere dalla pagina di dettaglio — non l'HTML (e'
un'app Angular, "tendering-app", zero dati nel primo GET), ma la sua REST
API interna, che verificato dal vivo il 2026-09-25 risponde in chiaro senza
login:

    GET /tendering-api/tenders/tenderType/<id>            -> {"type": "..."}
    GET /tendering-api/tenders/<type>/<id>/basic-info      -> expirationDate (epoch ms)

<id> e' il protocolId della lista con "/" sostituito da "-" (038140/2026 ->
038140-2026). <type> cambia per procedura ("market_survey" per una Richiesta
di preventivi, "open_procedure" per una gara Aperta, ecc.): va risolto bando
per bando con la prima chiamata, non si puo' indovinare dal solo link della
lista. Sono quindi DUE richieste in piu' per bando — accettabile: e' l'unico
modo per avere la scadenza, ed e' il campo su cui si ordina tutto il resto
del cruscotto.

LA LISTA HA BISOGNO DI UN COOKIE DI SESSIONE (niente CSRF, ma senza
visitare prima l'homepage il server risponde con la pagina vuota, verificato
dal vivo): stessa CookieJar di suam.py, un giro in piu' prima di cercare.

Il filtro "status" tiene solo le gare non ancora chiuse (verificato: 150
bandi il 2026-09-25 contro 25.191 nell'archivio storico completo, che e' il
default senza filtro — non va mai lanciato senza).

CORTESIA DEL ROBOTS.TXT: il portale chiede "Visit-time: 2300-0400" (visitare
solo tra le 23 e le 4). Non e' un blocco tecnico ma una richiesta esplicita:
questo script va schedulato di notte, non lanciato a mano durante il giorno
per test ripetuti.

Uso:
    python start_toscana.py --gate      # vale la pena? volume e aggancio
    python start_toscana.py --ingest    # carica start_avviso
    python start_toscana.py --aperti    # i bandi ancora aperti, dal piu' vicino

Solo libreria standard.
"""

import argparse
import html as html_mod
import http.cookiejar
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import date, datetime

QUI = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(QUI, "radar.db")

BASE = "https://start.toscana.it"
HOMEPAGE = BASE + "/homepage/"
LISTA_URL = BASE + "/initiatives/list/page/{pagina}/?status={stato}"
STATO_APERTI = "10,70,80,100,130,150,200,250,300,350,400"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

PER_PAGINA = 10
TETTO_PAGINE = 60  # freno di sicurezza (60*10=600 bandi aperti, oggi sono ~150)

RUMORE = re.compile(
    r"\b(s\.?p\.?a\.?|s\.?r\.?l\.?|societa'?|spa|srl|scarl|"
    r"azienda|ente|istituto|comune di|citta' di|provincia di|regione)\b", re.I)

RIGA = re.compile(r"<tr>(?:(?!</tr>).)*?</tr>", re.S)


def sessione():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.open(urllib.request.Request(HOMEPAGE, headers={"User-Agent": UA}),
                timeout=60).read()
    return opener


def richiesta(opener, url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with opener.open(req, timeout=60) as r:
        return r.read().decode("utf-8", errors="replace")


def richiesta_json(url):
    # Verificato dal vivo: le due chiamate tendering-api non hanno bisogno
    # del cookie di sessione, funzionano anche completamente anonime.
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def campo(blocco, classe):
    m = re.search(rf'class="{classe}">(.*?)</span>', blocco, re.S)
    return html_mod.unescape(m.group(1)).strip() if m else None


def cella(blocco, classe):
    m = re.search(rf'<td class="{classe}">(.*?)</td>', blocco, re.S)
    return html_mod.unescape(m.group(1)).strip() if m else None


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


def data_it(v):
    if not v:
        return None
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", v.strip())
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def normalizza(blocco):
    protocol_id = campo(blocco, "protocolId")
    if not protocol_id:
        return None
    m_link = re.search(r'<a href="([^"]+)"[^>]*>(.*?)</a>', blocco, re.S)
    link = BASE + m_link.group(1) if m_link else None
    titolo = html_mod.unescape(m_link.group(2)).strip() if m_link else None
    return dict(
        codice=protocol_id.replace("/", "-"),
        ente=campo(blocco, "organizationUnit"),
        titolo=titolo,
        tipo=cella(blocco, "contractType"),
        procedura=campo(blocco, "process-type"),
        importo=importo_it(cella(blocco, "amount")),
        pubblicato=data_it(cella(blocco, "publishedAt")),
        cig=(lambda c: None if not c or c.lower() == "n/a" else c)(cella(blocco, "cig")),
        stato=cella(blocco, "status"),
        link=link,
    )


def pagina_totale(html):
    m = re.search(r'<strong>[\d-]+</strong>\s*di\s*<strong[^>]*>(\d+)</strong>', html)
    return int(m.group(1)) if m else None


def dettagli_di(codice):
    """Due chiamate: prima il tipo di procedura, poi la scheda con la
    scadenza. Se una fallisce (bando ritirato, formato id inatteso) si
    rinuncia ai dettagli per questo bando soltanto — non deve bloccare
    tutti gli altri, stesso principio del resto dell'ingestion.

    La scheda ("basic-info") porta anche "description" e gli "attachments"
    (avviso, capitolato...): il "titolo" della lista e' spesso il campo
    oggetto compilato dall'ente, che puo' essere generico ("Senza Titolo",
    un codice interno) — la description e' il testo lungo dell'avviso, e
    gli allegati permettono la verifica approfondita sul documento vero
    (verdetto_documento.py). Tutto dalla stessa chiamata gia' fatta per la
    scadenza: zero richieste in piu'. "tipo" (market_survey/open_procedure/
    ...) va salvato anche lui: serve per ricostruire l'URL di download di
    un allegato piu' avanti, e non si puo' riderivare dal solo codice."""
    try:
        tipo = richiesta_json(f"{BASE}/tendering-api/tenders/tenderType/{codice}")["type"]
        info = richiesta_json(f"{BASE}/tendering-api/tenders/{tipo}/{codice}/basic-info")
        ms = info.get("expirationDate")
        scadenza = datetime.fromtimestamp(ms / 1000).date() if ms else None
        descrizione = ((info.get("description") or {}).get("it_IT") or "").strip()[:600] or None
        allegati = [
            dict(id=a.get("id"),
                 descrizione=(a.get("description") or {}).get("it_IT"),
                 nome=(a.get("file") or {}).get("fileName"))
            for a in (info.get("attachments") or [])
            if a.get("whoCanDownload") == "anyone" and (a.get("file") or {}).get("fileName")
        ]
        return scadenza, descrizione, tipo, allegati
    except (urllib.error.URLError, KeyError, ValueError, OSError):
        return None, None, None, []


def cerca_tutti(opener):
    righe, pagina, totale = [], 1, None
    while totale is None or pagina <= -(-totale // PER_PAGINA):
        url = LISTA_URL.format(pagina=pagina, stato=STATO_APERTI)
        html = richiesta(opener, url)
        if totale is None:
            totale = pagina_totale(html) or 0
        for blocco in RIGA.findall(html):
            r = normalizza(blocco)
            if r:
                righe.append(r)
        pagina += 1
        if pagina > TETTO_PAGINE:
            print(f"  ATTENZIONE: raggiunto il tetto di sicurezza "
                  f"({TETTO_PAGINE} pagine) — potrebbero mancarne.")
            break
    for r in righe:
        scadenza, descrizione, tipo_api, allegati = dettagli_di(r["codice"])
        r["scadenza"], r["descrizione"], r["tipo_api"] = scadenza, descrizione, tipo_api
        r["allegati"] = json.dumps(allegati, ensure_ascii=False) if allegati else None
    return righe


def norm(nome):
    if not nome:
        return None
    # Solo qui e non in suam.py/intercenter.py: "organizationUnit" su START
    # e' spesso l'ufficio che ha pubblicato, non l'ente ("COMUNE DI FIRENZE -
    # DIREZIONE PATRIMONIO IMMOBILIARE"), mentre in ente_contatti c'e' solo
    # l'ente ("Comune di Firenze") — senza tagliare al primo " - " l'aggancio
    # resta a zero anche quando l'ente e' notissimo (verificato dal vivo: 0
    # su 150 prima di questo taglio, 84 enti toscani gia' in indice).
    nome = nome.split(" - ")[0]
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
CREATE TABLE IF NOT EXISTS start_avviso (
    codice        TEXT PRIMARY KEY,
    ente          TEXT,
    cf_ente       TEXT,
    titolo        TEXT,
    descrizione   TEXT,
    tipo          TEXT,
    procedura     TEXT,
    importo       REAL,
    pubblicato    TEXT,
    scadenza      TEXT,
    cig           TEXT,
    stato         TEXT,
    link          TEXT,
    tipo_api      TEXT,
    allegati      TEXT,
    ingerito_il   TEXT DEFAULT CURRENT_TIMESTAMP,
    verdetto        TEXT,
    verdetto_motivo TEXT,
    verificato_doc_il TEXT,
    notificato_il   TEXT
);
CREATE INDEX IF NOT EXISTS ix_start_scadenza ON start_avviso (scadenza);
CREATE INDEX IF NOT EXISTS ix_start_cf ON start_avviso (cf_ente);
"""


def gate():
    righe = cerca_tutti(sessione())
    cx = sqlite3.connect(DB) if os.path.exists(DB) else None
    agganciati = 0
    if cx:
        agganciati = aggancia(righe, indice_enti(cx))
        cx.close()

    con_cig = sum(1 for r in righe if r["cig"])
    con_scad = sum(1 for r in righe if r["scadenza"])
    con_valore = [r for r in righe if r["importo"]]
    print(f"\n  bandi aperti/non iniziati adesso su START Toscana  {len(righe):>7,}")
    print(f"  ...agganciati a un nostro ente           {agganciati:>7,}")
    print(f"  ...con CIG                               {con_cig:>7,}")
    print(f"  ...con scadenza risolta dall'API         {con_scad:>7,}")
    if con_valore:
        tot = sum(r["importo"] for r in con_valore)
        print(f"  ...con importo                           {len(con_valore):>7,}"
              f"  per {tot/1e6:,.1f} M€")

    oggi = date.today()
    aperte = [r for r in righe if r["scadenza"] and r["scadenza"] >= oggi]
    if aperte:
        print("\n  le prossime scadenze:")
        for r in sorted(aperte, key=lambda x: x["scadenza"])[:10]:
            gg = (r["scadenza"] - oggi).days
            v = f"{r['importo']/1000:,.0f} k€" if r["importo"] else "—"
            print(f"    {r['scadenza']}  fra {gg} gg  {v:>12}  "
                  f"{(r['ente'] or '?')[:38]:40s} {(r['titolo'] or '')[:44]}")

    print("\n  " + "-" * 66)
    print(f"  Volume medio (~150 aperti oggi), CIG gia' in lista — meglio di "
          f"SUAM su\n  questo punto. Due chiamate extra a bando per la "
          f"scadenza: piu' lento di\n  SUAM/Intercenter, ma l'unico modo per "
          f"avere quel campo.")
    print("  " + "-" * 66 + "\n")
    return 0


def allinea_schema(cx):
    # Stesso motivo di intercenter.py: CREATE TABLE IF NOT EXISTS non
    # aggiunge colonne a una tabella gia' esistente.
    for stmt in ("ALTER TABLE start_avviso ADD COLUMN descrizione TEXT",
                 "ALTER TABLE start_avviso ADD COLUMN tipo_api TEXT",
                 "ALTER TABLE start_avviso ADD COLUMN allegati TEXT",
                 "ALTER TABLE start_avviso ADD COLUMN verificato_doc_il TEXT"):
        try:
            cx.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e):
                raise
    cx.commit()


def ingest():
    righe = cerca_tutti(sessione())
    cx = sqlite3.connect(DB)
    cx.executescript(DDL)
    allinea_schema(cx)
    n_agg = aggancia(righe, indice_enti(cx))
    col = ["codice", "ente", "cf_ente", "titolo", "descrizione", "tipo", "procedura", "importo",
           "pubblicato", "scadenza", "cig", "stato", "link", "tipo_api", "allegati"]
    cx.executemany(
        f"INSERT INTO start_avviso ({', '.join(col)}) "
        f"VALUES ({', '.join('?' * len(col))}) "
        f"ON CONFLICT (codice) DO UPDATE SET "
        + ", ".join(f"{c}=excluded.{c}" for c in col[1:])
        + ", ingerito_il=CURRENT_TIMESTAMP",
        [tuple(str(r[c]) if isinstance(r[c], date) else r[c] for c in col)
         for r in righe])
    cx.commit()
    n = cx.execute("SELECT count(*) FROM start_avviso").fetchone()[0]
    cx.close()
    print(f"start_avviso: {n:,} righe in tutto, {len(righe):,} viste ora, "
          f"{n_agg:,} agganciate a un ente noto")
    return 0


def aperti():
    if not os.path.exists(DB):
        sys.exit("radar.db non trovato: lancia prima --ingest")
    cx = sqlite3.connect(DB)
    cx.row_factory = sqlite3.Row
    try:
        righe = cx.execute(
            "SELECT * FROM start_avviso WHERE scadenza >= date('now') "
            "ORDER BY scadenza LIMIT 40").fetchall()
    except sqlite3.OperationalError:
        sys.exit("tabella start_avviso assente: lancia prima --ingest")
    if not righe:
        print("\n  nessun bando aperto in archivio. Rilancia --ingest.\n")
        return 0
    print(f"\n=== {len(righe)} bandi START Toscana ancora aperti ===\n")
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
