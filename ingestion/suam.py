"""Bandi aperti sul SUAM (Sistema Unico Appalti Marche) — task R40.

STESSA LOGICA DI TED (R5), UN'ALTRA FONTE. TED copre sopra soglia comunitaria
(~2,2% del mercato ANAC); il SUAM e' il marketplace regionale condiviso da
centinaia di enti marchigiani (Regione, Province, ASUR, decine di Comuni —
lista completa nel dropdown "Stazione appaltante" del portale) per gli
appalti sotto soglia, dove un'agenzia da 4 persone e' piu' competitiva.

NON HA UN'API. E' verificato che `robots.txt` autorizza esplicitamente
Googlebot/Bingbot a leggere le pagine bandi/avvisi/esiti (stesso schema
software di altri portali regionali, verificato anche su quello di Regione
Veneto): non e' un motivo per NON automatizzarlo, ma vuol dire scraping di
HTML invece della query strutturata di TED, quindi piu' fragile a un
redesign del sito.

COME FUNZIONA LA RICERCA. E' un form POST protetto da CSRF, non una GET con
query string:
  1. GET sulla pagina lista -> prende il token _csrf (campo nascosto) e il
     cookie di sessione (gestito da CookieJar).
  2. POST allo stesso URL con azione 'listAllBandi.action' + _csrf + i
     filtri (stato=1 e' "In corso", stazioneAppaltante vuoto = tutti gli
     enti) -> torna l'HTML dei risultati, un <div class="list-item"> per
     bando, campi in coppie <label>Nome : </label>Valore.

Verificato dal vivo il 2026-09-24: 877 bandi in archivio su tutte le
stazioni appaltanti (storico), ma solo 3 "In corso" in quel momento — un
volume reale ma piccolo, come TED ("utile come segnale, non come motore
principale", stessa nota li'). iDisplayLength=100 basta abbondantemente
oggi; se un giorno superasse 100 bandi aperti contemporaneamente andrebbe
aggiunta la paginazione (per ora solo un avviso se il conteggio tocca il
tetto).

Niente CIG nella lista (solo un "Riferimento procedura" interno tipo
G12345, non il CIG ANAC): quando l'ente lo scrive nel titolo ("... - CIG :
XXXXXXXXXX") lo si recupera con una regex, altrimenti resta vuoto — non si
va a scaricare la scheda di dettaglio per ognuno solo per quello, sarebbe
una richiesta in piu' a bando per un campo opzionale.

Uso:
    python suam.py --gate      # vale la pena? volume e aggancio agli enti noti
    python suam.py --ingest    # carica suam_avviso
    python suam.py --aperti    # i bandi ancora aperti, dal piu' vicino

Solo libreria standard.
"""

import argparse
import html as html_mod
import http.cookiejar
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime

QUI = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(QUI, "radar.db")

BASE = "https://appaltisuam.regione.marche.it"
LISTA_URL = BASE + "/PortaleAppalti/it/ppgare_bandi_lista.wp"
AZIONE_URL = (LISTA_URL + "?actionPath=/ExtStr2/do/FrontEnd/Bandi/"
              "listAllBandi.action&currentFrame=7")
DETTAGLIO_URL = (LISTA_URL + "?actionPath=/ExtStr2/do/FrontEnd/Bandi/"
                 "view.action&currentFrame=7&codice={}")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

TETTO_PAGINA = 100

# Stesso rumore di ted.py/esito.py: ogni fonte ha la sua copia apposta,
# sono tre righe, non vale un modulo condiviso per cosi' poco.
RUMORE = re.compile(
    r"\b(s\.?p\.?a\.?|s\.?r\.?l\.?|societa'?|spa|srl|scarl|"
    r"azienda|ente|istituto|comune di|citta' di|provincia di|regione)\b", re.I)


def sessione():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def richiesta(opener, url, dati=None):
    req = urllib.request.Request(
        url, data=dati,
        headers={"User-Agent": UA,
                 "Content-Type": "application/x-www-form-urlencoded"} if dati else
                {"User-Agent": UA})
    with opener.open(req, timeout=60) as r:
        return r.read().decode("utf-8", errors="replace")


def csrf_da(pagina):
    m = re.search(r'name="_csrf"\s+value="([^"]+)"', pagina)
    if not m:
        raise RuntimeError("token _csrf non trovato: il portale potrebbe "
                           "aver cambiato pagina di ricerca")
    return m.group(1)


def cerca(opener, stato="1", ente="", giro_iniziale=None):
    """Una ricerca completa: GET per prendere sessione+csrf, poi POST con i
    filtri. giro_iniziale, se passato, evita la GET (riusa un token gia'
    preso in questa sessione — usato solo per test manuali)."""
    if giro_iniziale is None:
        prima = richiesta(opener, LISTA_URL)
        csrf = csrf_da(prima)
    else:
        csrf = giro_iniziale
    corpo = urllib.parse.urlencode({
        "_csrf": csrf,
        "model.stazioneAppaltante": ente,
        "model.stato": stato,
        "model.tipoAppalto": "",
        "model.orderCriteria": "DATA_SCADENZA_ASC",
        "model.iDisplayLength": str(TETTO_PAGINA),
    }).encode()
    return richiesta(opener, AZIONE_URL, corpo)


def data_it(v):
    if not v:
        return None
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", v.strip())
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def importo_it(v):
    if not v:
        return None
    # campo() ha gia' fatto l'unescape: "&euro;" e' diventato "€" e resta
    # in coda al numero ("64.307.893,84 €") — va tolto prima di convertire.
    m = re.search(r"[\d.]+,\d+|\d+", v)
    if not m:
        return None
    v = m.group(0).replace(".", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def campo(blocco, etichetta):
    m = re.search(re.escape(etichetta) + r"\s*:\s*</label>\s*(.+?)\s*<",
                  blocco, re.S)
    return html_mod.unescape(m.group(1)).strip() if m else None


def normalizza(blocco):
    codice_m = re.search(r"codice=([A-Za-z0-9]+)", blocco)
    codice = codice_m.group(1) if codice_m else None
    if not codice:
        return None

    titolo = campo(blocco, "Titolo")
    stato = (campo(blocco, "Stato") or "").split(" - ")[0].strip() or None
    # Il campo "Stazione appaltante" e' spezzato su piu' righe/div vuoti fra
    # label e testo: la regex di campo() sul primo '<' si fermerebbe troppo
    # presto, serve una regola sua.
    m_ente = re.search(r"Stazione appaltante\s*:\s*</label>(.*?)<div",
                       blocco, re.S)
    ente = html_mod.unescape(m_ente.group(1)).strip() if m_ente else None

    cig = None
    if titolo:
        m_cig = re.search(r"CIG\s*:?\s*([A-Z0-9]{10})\b", titolo)
        cig = m_cig.group(1) if m_cig else None

    return dict(
        codice=codice,
        ente=ente,
        titolo=titolo,
        tipo=campo(blocco, "Tipologia appalto"),
        importo=importo_it(campo(blocco, "Importo")),
        pubblicato=data_it(campo(blocco, "Data pubblicazione")),
        scadenza=data_it(campo(blocco, "Data scadenza")),
        cig=cig,
        stato=stato,
        link=DETTAGLIO_URL.format(codice),
    )


def estrai_bandi(pagina_html):
    blocchi = pagina_html.split('<div class="list-item">')[1:]
    righe = [normalizza(b) for b in blocchi]
    return [r for r in righe if r]


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
CREATE TABLE IF NOT EXISTS suam_avviso (
    codice        TEXT PRIMARY KEY,
    ente          TEXT,
    cf_ente       TEXT,
    titolo        TEXT,
    tipo          TEXT,
    importo       REAL,
    pubblicato    TEXT,
    scadenza      TEXT,
    cig           TEXT,
    stato         TEXT,
    link          TEXT,
    ingerito_il   TEXT DEFAULT CURRENT_TIMESTAMP,
    verdetto        TEXT,
    verdetto_motivo TEXT
);
CREATE INDEX IF NOT EXISTS ix_suam_scadenza ON suam_avviso (scadenza);
CREATE INDEX IF NOT EXISTS ix_suam_cf ON suam_avviso (cf_ente);
"""


def gate():
    opener = sessione()
    pagina = cerca(opener, stato="1")
    righe = estrai_bandi(pagina)
    if len(righe) >= TETTO_PAGINA:
        print(f"  ATTENZIONE: {TETTO_PAGINA} risultati (il tetto della pagina) — "
              f"potrebbero essercene di piu', serve la paginazione.")

    cx = sqlite3.connect(DB) if os.path.exists(DB) else None
    agganciati = 0
    if cx:
        agganciati = aggancia(righe, indice_enti(cx))
        cx.close()

    con_cig = sum(1 for r in righe if r["cig"])
    con_valore = [r for r in righe if r["importo"]]
    print(f"\n  bandi 'In corso' adesso su SUAM Marche   {len(righe):>7,}")
    print(f"  ...agganciati a un nostro ente           {agganciati:>7,}")
    print(f"  ...con CIG leggibile dal titolo          {con_cig:>7,}")
    if con_valore:
        tot = sum(r["importo"] for r in con_valore)
        print(f"  ...con importo                           {len(con_valore):>7,}"
              f"  per {tot/1e6:,.1f} M€")

    if righe:
        print("\n  le prossime scadenze:")
        oggi = date.today()
        for r in sorted(righe, key=lambda x: x["scadenza"] or date.max)[:10]:
            gg = (r["scadenza"] - oggi).days if r["scadenza"] else "?"
            v = f"{r['importo']/1000:,.0f} k€" if r["importo"] else "—"
            print(f"    {r['scadenza']}  fra {gg} gg  {v:>12}  "
                  f"{(r['ente'] or '?')[:38]:40s} {(r['titolo'] or '')[:44]}")

    print("\n  " + "-" * 66)
    print(f"  Volume piccolo per costruzione (sotto soglia, una regione sola) — "
          f"utile come\n  segnale aggiuntivo, non come motore principale. "
          f"Stessa nota gia' scritta per TED.")
    print("  " + "-" * 66 + "\n")
    return 0


def ingest():
    opener = sessione()
    pagina = cerca(opener, stato="1")
    righe = estrai_bandi(pagina)
    if len(righe) >= TETTO_PAGINA:
        print(f"  ATTENZIONE: {TETTO_PAGINA} risultati (tetto pagina), "
              f"potrebbero mancarne — serve la paginazione.")

    cx = sqlite3.connect(DB)
    cx.executescript(DDL)
    n_agg = aggancia(righe, indice_enti(cx))
    col = ["codice", "ente", "cf_ente", "titolo", "tipo", "importo",
           "pubblicato", "scadenza", "cig", "stato", "link"]
    cx.executemany(
        f"INSERT INTO suam_avviso ({', '.join(col)}) "
        f"VALUES ({', '.join('?' * len(col))}) "
        f"ON CONFLICT (codice) DO UPDATE SET "
        + ", ".join(f"{c}=excluded.{c}" for c in col[1:])
        + ", ingerito_il=CURRENT_TIMESTAMP",
        [tuple(str(r[c]) if isinstance(r[c], date) else r[c] for c in col)
         for r in righe])
    cx.commit()
    n = cx.execute("SELECT count(*) FROM suam_avviso").fetchone()[0]
    cx.close()
    print(f"suam_avviso: {n:,} righe in tutto, {len(righe):,} viste ora, "
          f"{n_agg:,} agganciate a un ente noto")
    return 0


def aperti():
    if not os.path.exists(DB):
        sys.exit("radar.db non trovato: lancia prima --ingest")
    cx = sqlite3.connect(DB)
    cx.row_factory = sqlite3.Row
    try:
        righe = cx.execute(
            "SELECT * FROM suam_avviso WHERE scadenza >= date('now') "
            "ORDER BY scadenza LIMIT 40").fetchall()
    except sqlite3.OperationalError:
        sys.exit("tabella suam_avviso assente: lancia prima --ingest")
    if not righe:
        print("\n  nessun bando aperto in archivio. Rilancia --ingest.\n")
        return 0
    print(f"\n=== {len(righe)} bandi SUAM Marche ancora aperti ===\n")
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
    # Console Windows in cp1252: senza, il "€" e il "●" mandano in eccezione
    # print() invece di stamparsi male e basta. Stessa riga gia' in
    # esito.py/punteggio.py/scheda.py.
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
