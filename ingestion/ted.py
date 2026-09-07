"""Gare europee aperte, da TED (task R5).

PERCHE' E' UN'ALTRA COSA RISPETTO AD ANAC. Il motore scadenze guarda contratti
gia' finiti e indovina quando l'ente ricomprera': e' una previsione, e il 96,6%
delle gare ANAC risulta gia' scaduto quando il file arriva. TED invece pubblica
l'avviso **mentre la gara e' aperta**, con la scadenza per presentare offerta
ancora nel futuro. Non e' un lead da coltivare per sei mesi: e' una cosa a cui
si puo' partecipare adesso.

Il prezzo e' il volume. Sopra soglia comunitaria ci finisce poca roba: circa
1.900 avvisi l'anno sul verticale IT italiano, il ~2,2% del mercato ANAC. Non
e' un difetto del canale — e' tutto il mercato realmente contendibile per
gara, e va tenuto onesto invece che gonfiato.

L'API v3 e' anonima, nessuna chiave. Due cose non ovvie, che costano un
pomeriggio se non si sanno:

  - 'fields' e' OBBLIGATORIO e i nomi ammessi sono 1.826: sbagliarne uno da'
    400 con l'elenco intero in risposta, che e' anche l'unica documentazione
    davvero aggiornata
  - i CPV vanno a otto cifre. 'classification-cpv IN (72)' e' rifiutato,
    '(72000000)' invece prende tutto il ramo

  - quasi ogni campo testuale e' un dizionario per lingua ({"ita": [...]}) e
    quasi ogni campo e' una lista, una voce per lotto. La scadenza in
    particolare e' un array: un avviso con otto lotti ha otto date diverse.

Uso:
    python ted.py --gate           # quanti avvisi, e quanti si agganciano ai nostri enti
    python ted.py --ingest         # carica ted_avviso
    python ted.py --aperti         # le gare ancora aperte, dalla piu' vicina
    python ted.py --giorni 30      # finestra di pubblicazione

Solo libreria standard.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from urllib.request import Request

import anac_http as h

QUI = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(QUI, "radar.db")
API = "https://api.ted.europa.eu/v3/notices/search"

# I due rami CPV del verticale. A otto cifre: l'API rifiuta i prefissi corti.
CPV = ("72000000", "48000000")

# I campi che servono. Chiesti per nome perche' l'API li vuole espliciti; ogni
# nome qui e' stato verificato contro l'elenco che l'API restituisce sbagliando.
CAMPI = [
    "publication-number", "notice-title", "buyer-name", "buyer-city",
    "buyer-country", "deadline-receipt-tender-date-lot", "classification-cpv",
    "publication-date", "notice-type", "procedure-type", "contract-nature",
    "estimated-value-lot", "estimated-value-cur-lot", "description-lot",
    "links", "place-of-performance", "notice-identifier",
]

PAGINA = 100

# Le forme societarie e gli abbellimenti che impediscono a due scritture dello
# stesso ente di somigliarsi. Stesso problema di esito.norm_nome, altra fonte.
RUMORE = re.compile(
    r"\b(s\.?p\.?a\.?|s\.?r\.?l\.?|societa'?|spa|srl|scarl|"
    r"azienda|ente|istituto|comune di|citta' di|provincia di|regione)\b", re.I)


def testo(v, lingua="ita"):
    """Il valore, qualunque forma abbia.

    TED restituisce stringhe, liste, dizionari per lingua e dizionari per
    lingua di liste, a seconda del campo e a volte dello stesso campo. Farlo
    in un posto solo evita venti isinstance sparsi.
    """
    if v is None:
        return None
    if isinstance(v, dict):
        for k in (lingua, "mul", "eng"):
            if k in v:
                return testo(v[k], lingua)
        return testo(next(iter(v.values()), None), lingua) if v else None
    if isinstance(v, list):
        return testo(v[0], lingua) if v else None
    return str(v).strip() or None


def tutte(v):
    """Tutte le voci di un campo per-lotto, appiattite."""
    if v is None:
        return []
    if isinstance(v, dict):
        for k in ("ita", "mul", "eng"):
            if k in v:
                return tutte(v[k])
        return tutte(next(iter(v.values()), None)) if v else []
    if isinstance(v, list):
        out = []
        for x in v:
            out.extend(tutte(x))
        return out
    return [str(v).strip()] if str(v).strip() else []


def data_iso(v):
    """'2026-09-14+02:00' -> date(2026, 9, 14)."""
    if not v:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(v))
    return date(*map(int, m.groups())) if m else None


def scadenza(n):
    """La scadenza utile, fra tutte quelle dei lotti.

    Un avviso con otto lotti ha otto date. Serve la piu' vicina fra quelle
    ancora nel futuro: e' quella che dice entro quando bisogna muoversi. Se
    sono tutte passate si prende l'ultima, per sapere di quanto si e' persa.
    """
    date_ = sorted(d for d in (data_iso(x) for x in
                               tutte(n.get("deadline-receipt-tender-date-lot"))) if d)
    if not date_:
        return None, 0
    oggi = date.today()
    future = [d for d in date_ if d >= oggi]
    return (future[0] if future else date_[-1]), len(date_)


def valore(n):
    v = tutte(n.get("estimated-value-lot"))
    tot = 0.0
    for x in v:
        try:
            tot += float(str(x).replace(",", "."))
        except (TypeError, ValueError):
            pass
    return tot or None


def norm(nome):
    if not nome:
        return None
    s = re.sub(r"[^\w\s]", " ", nome.lower())
    s = RUMORE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip() or None


def interroga(dal, al=None, limite=None):
    """Tutti gli avvisi del verticale nella finestra, con paginazione.

    TED impagina con un token, non con un numero di pagina: chiedere 'pagina
    3' su un risultato che nel frattempo e' cambiato darebbe righe saltate o
    doppie, e non lo direbbe nessuno.
    """
    q = (f"classification-cpv IN ({' '.join(CPV)}) "
         f"AND buyer-country IN (ITA) "
         f"AND publication-date >= {dal:%Y%m%d}")
    if al:
        q += f" AND publication-date <= {al:%Y%m%d}"

    fuori, token, totale = [], None, None
    while True:
        # paginationMode ITERATION, non 'page': senza, l'API risponde ma
        # iterationNextToken torna None e ci si ferma alla prima pagina
        # credendo di aver finito. Non da' nessun errore — il totale dichiarato
        # dice 296 e in mano se ne hanno 100.
        corpo = {"query": q, "limit": PAGINA, "fields": CAMPI,
                 "paginationMode": "ITERATION"}
        if token:
            corpo["iterationNextToken"] = token
        req = Request(API, data=json.dumps(corpo).encode(),
                      headers={"Content-Type": "application/json",
                               "Accept": "application/json",
                               "User-Agent": h.UA})
        with h.apri(req, timeout=120) as r:
            d = json.loads(r.read())
        if totale is None:
            totale = d.get("totalNoticeCount", 0)
            print(f"  TED: {totale:,} avvisi dal {dal:%d/%m/%Y}")
        fuori.extend(d.get("notices") or [])
        token = d.get("iterationNextToken")
        print(f"    {len(fuori):,}/{totale:,}", end="\r", flush=True)
        if not token or not d.get("notices"):
            break
        if limite and len(fuori) >= limite:
            break
    print(" " * 30, end="\r")
    return fuori, totale or 0


def normalizza(n):
    scad, n_lotti = scadenza(n)
    return dict(
        numero=testo(n.get("publication-number")),
        identificativo=testo(n.get("notice-identifier")),
        titolo=testo(n.get("notice-title")),
        ente=testo(n.get("buyer-name")),
        citta=testo(n.get("buyer-city")),
        pubblicato=data_iso(testo(n.get("publication-date"))),
        scadenza=scad,
        n_lotti=n_lotti,
        cpv=",".join(sorted(set(tutte(n.get("classification-cpv"))))[:6]),
        tipo=testo(n.get("notice-type")),
        procedura=testo(n.get("procedure-type")),
        natura=testo(n.get("contract-nature")),
        valore=valore(n),
        valuta=testo(n.get("estimated-value-cur-lot")),
        oggetto=(testo(n.get("description-lot")) or "")[:2000] or None,
        link=(((n.get("links") or {}).get("xml") or {}).get("MUL")
              or ((n.get("links") or {}).get("pdf") or {}).get("ITA")),
    )


DDL = """
CREATE TABLE IF NOT EXISTS ted_avviso (
    numero        TEXT PRIMARY KEY,
    identificativo TEXT,
    titolo        TEXT,
    ente          TEXT,
    citta         TEXT,
    cf_ente       TEXT,
    pubblicato    TEXT,
    scadenza      TEXT,
    n_lotti       INTEGER,
    cpv           TEXT,
    tipo          TEXT,
    procedura     TEXT,
    natura        TEXT,
    valore        REAL,
    valuta        TEXT,
    oggetto       TEXT,
    link          TEXT,
    ingerito_il   TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_ted_scadenza ON ted_avviso (scadenza);
CREATE INDEX IF NOT EXISTS ix_ted_cf ON ted_avviso (cf_ente);
"""


def indice_enti(cx):
    """nome normalizzato -> cf_ente, per agganciare il compratore TED.

    TED non pubblica il codice fiscale del compratore, quindi il join e' sul
    nome — e il nome e' scritto in modi diversi dalle due fonti. Si accetta
    che una parte non si agganci: un avviso senza CF resta comunque leggibile,
    perde solo lo storico dell'ente.
    """
    m = {}
    for cf, den in cx.execute(
            "SELECT cf_ente, denominazione_ipa FROM ente_contatti "
            "WHERE denominazione_ipa IS NOT NULL"):
        k = norm(den)
        if k and k not in m:
            m[k] = cf
    return m


def aggancia(avvisi, indice):
    n = 0
    for a in avvisi:
        k = norm(a["ente"])
        cf = indice.get(k)
        if not cf and k:
            # Secondo tentativo: il nome TED contiene spesso la citta' in coda
            # ("Comune di X (PR)"). Si prova col solo pezzo iniziale.
            corto = k.split(" di ")[-1] if " di " in k else k
            cf = indice.get(corto)
        a["cf_ente"] = cf
        n += bool(cf)
    return n


def gate(giorni):
    """R5.1 — vale la pena? Volume e aggancio, prima di costruire il resto."""
    dal = date.today() - timedelta(days=giorni)
    avvisi, totale = interroga(dal)
    righe = [normalizza(a) for a in avvisi]
    oggi = date.today()
    aperti = [r for r in righe if r["scadenza"] and r["scadenza"] >= oggi]

    cx = sqlite3.connect(DB) if os.path.exists(DB) else None
    agganciati = 0
    if cx:
        agganciati = aggancia(righe, indice_enti(cx))
        cx.close()

    con_valore = [r for r in righe if r["valore"]]
    print(f"\n  avvisi negli ultimi {giorni} giorni   {len(righe):>7,}")
    print(f"  ...ancora aperti                {len(aperti):>7,}"
          f"  {len(aperti)/max(len(righe),1)*100:5.1f}%")
    print(f"  ...agganciati a un nostro ente  {agganciati:>7,}"
          f"  {agganciati/max(len(righe),1)*100:5.1f}%")
    if con_valore:
        tot = sum(r["valore"] for r in con_valore)
        print(f"  ...con importo stimato          {len(con_valore):>7,}"
              f"  per {tot/1e6:,.1f} M€")
    print(f"\n  ritmo: {len(righe)/max(giorni,1)*365:,.0f} avvisi l'anno sul verticale")

    if aperti:
        print("\n  le prossime scadenze:")
        for r in sorted(aperti, key=lambda x: x["scadenza"])[:10]:
            gg = (r["scadenza"] - oggi).days
            v = f"{r['valore']/1000:,.0f} k€" if r["valore"] else "—"
            print(f"    {r['scadenza']:%d/%m}  fra {gg:>3} gg  {v:>12}  "
                  f"{(r['ente'] or '?')[:38]:40s} {(r['titolo'] or '')[:44]}")

    print("\n  " + "-" * 66)
    if len(aperti) >= 20:
        print(f"  {len(aperti)} gare aperte adesso, con la scadenza ancora nel")
        print("  futuro. E' l'unica fonte che dice 'si puo' partecipare oggi'")
        print("  invece di 'forse ricomprano fra sei mesi'.")
    else:
        print(f"  Solo {len(aperti)} gare aperte in {giorni} giorni: il volume e'")
        print("  quello previsto (sopra soglia comunitaria ci finisce poco).")
        print("  Utile come segnale, non come motore principale.")
    print("  " + "-" * 66 + "\n")
    return 0


def ingest(giorni):
    dal = date.today() - timedelta(days=giorni)
    avvisi, _ = interroga(dal)
    righe = [normalizza(a) for a in avvisi]
    cx = sqlite3.connect(DB)
    cx.executescript(DDL)
    n_agg = aggancia(righe, indice_enti(cx))
    col = ["numero", "identificativo", "titolo", "ente", "citta", "cf_ente",
           "pubblicato", "scadenza", "n_lotti", "cpv", "tipo", "procedura",
           "natura", "valore", "valuta", "oggetto", "link"]
    cx.executemany(
        f"INSERT INTO ted_avviso ({', '.join(col)}) "
        f"VALUES ({', '.join('?' * len(col))}) "
        f"ON CONFLICT (numero) DO UPDATE SET "
        + ", ".join(f"{c}=excluded.{c}" for c in col[1:])
        + ", ingerito_il=CURRENT_TIMESTAMP",
        [tuple(str(r[c]) if isinstance(r[c], date) else r[c] for c in col)
         for r in righe if r["numero"]])
    cx.commit()
    n = cx.execute("SELECT count(*) FROM ted_avviso").fetchone()[0]
    cx.close()
    print(f"ted_avviso: {n:,} righe in tutto, {len(righe):,} viste ora, "
          f"{n_agg:,} agganciate a un ente noto")
    return 0


def aperti():
    if not os.path.exists(DB):
        sys.exit("radar.db non trovato: lancia prima --ingest")
    cx = sqlite3.connect(DB)
    cx.row_factory = sqlite3.Row
    try:
        righe = cx.execute(
            "SELECT * FROM ted_avviso WHERE scadenza >= date('now') "
            "ORDER BY scadenza LIMIT 40").fetchall()
    except sqlite3.OperationalError:
        sys.exit("tabella ted_avviso assente: lancia prima --ingest")
    if not righe:
        print("\n  nessuna gara aperta in archivio. Rilancia --ingest.\n")
        return 0
    print(f"\n=== {len(righe)} gare europee ancora aperte ===\n")
    for r in righe:
        gg = (date.fromisoformat(r["scadenza"]) - date.today()).days
        v = f"{r['valore']/1000:,.0f} k€" if r["valore"] else "—"
        noto = " ●" if r["cf_ente"] else "  "
        print(f"  {r['scadenza']}  fra {gg:>3} gg{noto} {v:>12}  "
              f"{(r['ente'] or '?')[:40]}")
        print(f"      {(r['titolo'] or '')[:74]}")
        if r["link"]:
            print(f"      {r['link']}")
    print("\n  ● = ente che conosciamo gia' dallo storico ANAC: la scheda ente")
    print("      dice se vale la pena, prima di preparare un'offerta.\n")
    cx.close()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--ingest", action="store_true")
    ap.add_argument("--aperti", action="store_true")
    ap.add_argument("--giorni", type=int, default=60,
                    help="finestra di pubblicazione (default 60)")
    a = ap.parse_args()
    if a.aperti:
        return aperti()
    if a.ingest:
        return ingest(a.giorni)
    return gate(a.giorni)


if __name__ == "__main__":
    sys.exit(main())
