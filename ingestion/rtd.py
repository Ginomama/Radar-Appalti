"""Responsabile della Transizione al Digitale (RTD), per nominarlo nella PEC
senza conservarne il recapito (R28 parte 2).

LA DECISIONE (docs/liceita.md §7, presa il 2026-09-17 con Leonardo). Tre
domande erano aperte: base giuridica, informativa, minimizzazione. La via
scelta e' quella che liceita.md indicava come "probabilmente la risposta
giusta": nominare la persona nel testo della PEC, ma scriverla comunque
alla PEC ISTITUZIONALE dell'ente — mai alla mail personale del RTD, che
qui non viene nemmeno letta a valle del parsing. Minimizzazione al
massimo: solo nome e cognome arrivano al chiamante, e solo per finire nel
testo di una PEC — non in una colonna di radar.db o di Supabase. Stesso
trattamento gia' riservato al dataset 'ou' da uffici.py: il file scaricato
resta in cache locale, gitignored (vedi CACHE), mai nello schema.

Base giuridica: legittimo interesse (art. 6.1.f GDPR) — il RTD e'
pubblicato da IndicePA proprio perche' raggiungibile su questi temi (CAD
art. 17). Informativa: la riga finale della PEC (fonte IndicePA, licenza,
diritto di cancellazione) gia' presente in ogni messaggio di genera_pec.py
copre anche questo dato, non serve un paragrafo a parte.

IL FORMATO. IndicePA non pubblica questo dataset in TXT/CSV come 'ou':
solo XLSX. Un parser minimo a libreria standard (zipfile + xml.etree)
legge righe e colonne dai file interni del formato Office Open XML — non
serve openpyxl per leggere un file solo, con queste dieci colonne.

Uso:
    python rtd.py --gate          # copertura misurata sui nostri enti
    python rtd.py --nome <cf>     # nome e cognome del RTD per un ente

Solo libreria standard.
"""

import argparse
import io
import os
import re
import sqlite3
import sys
import zipfile
from urllib.request import Request
from xml.etree import ElementTree as ET

import anac_http as h

QUI = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(QUI, "radar.db")
CACHE = os.path.join(QUI, "recon_out", "ipa")
API = "https://indicepa.gov.it/ipa-dati/api/3/action"
DATASET = "responsabili-della-transizione-al-digitale"

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
Q = lambda tag: f"{{{NS}}}{tag}"

# Le colonne che si tengono. Le altre — Mail_responsabile,
# Telefono_responsabile, Mail1/2/3 (l'ufficio) — non vengono lette: non e'
# un filtro applicato ai dati, e' che questa funzione non le chiede mai al
# file. Vedi docs/liceita.md §7.
TENUTE = ("Codice_fiscale_ente", "Nome_responsabile", "Cognome_responsabile")


def risorsa_xlsx():
    req = Request(f"{API}/package_show?id={DATASET}",
                  headers={"User-Agent": h.UA, "Accept": "application/json"})
    with h.apri(req, timeout=60) as r:
        import json
        d = json.loads(r.read())["result"]
    for x in d.get("resources", []):
        if (x.get("format") or "").upper() == "XLSX":
            return x["url"]
    raise SystemExit(f"nessuna risorsa XLSX nel dataset '{DATASET}'")


def scarica():
    os.makedirs(CACHE, exist_ok=True)
    dest = os.path.join(CACHE, "responsabili.xlsx")
    if os.path.exists(dest) and os.path.getsize(dest) > 500_000:
        return dest
    url = risorsa_xlsx()
    print("scarico responsabili.xlsx ...", end=" ", flush=True)
    tot = 0
    with h.apri(Request(url, headers={"User-Agent": h.UA}), timeout=300) as r, \
            open(dest, "wb") as f:
        while True:
            p = r.read(262144)
            if not p:
                break
            f.write(p)
            tot += len(p)
    print(f"{tot/1e6:.1f} MB")
    return dest


def _colonna(rif):
    """'H2' -> 7 (indice 0-based della colonna). Le lettere sole, niente cifre."""
    lettere = re.match(r"[A-Z]+", rif).group()
    n = 0
    for c in lettere:
        n = n * 26 + (ord(c) - ord("A") + 1)
    return n - 1


def leggi(path, colonne):
    """Righe del foglio come dizionari {colonna: valore}, solo per le
    colonne richieste. Gli indici delle celle si leggono dal riferimento
    (r="H2"), non dalla posizione nella riga: un file XLSX omette le celle
    vuote, e contare per posizione disallineerebbe tutto dopo la prima."""
    with zipfile.ZipFile(path) as z:
        sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
        stringhe = ["".join(t.text or "" for t in si.iter(Q("t")))
                    for si in sst.findall(Q("si"))]
        foglio = ET.parse(io.BytesIO(z.read("xl/worksheets/sheet1.xml")))

    def valore(c):
        v = c.find(Q("v"))
        if v is None or v.text is None:
            return None
        return stringhe[int(v.text)] if c.get("t") == "s" else v.text

    righe = foglio.getroot().find(Q("sheetData")).findall(Q("row"))
    intestazione = {}
    for c in righe[0].findall(Q("c")):
        nome = valore(c)
        if nome in colonne:
            intestazione[_colonna(c.get("r"))] = nome

    for riga in righe[1:]:
        d = {}
        for c in riga.findall(Q("c")):
            idx = _colonna(c.get("r"))
            if idx in intestazione:
                d[intestazione[idx]] = valore(c)
        if d:
            yield d


def indice():
    """cf_ente -> 'Nome Cognome', solo in memoria per questo processo."""
    path = scarica()
    d = {}
    for r in leggi(path, TENUTE):
        cf = (r.get("Codice_fiscale_ente") or "").strip()
        nome = (r.get("Nome_responsabile") or "").strip()
        cognome = (r.get("Cognome_responsabile") or "").strip()
        if cf and (nome or cognome):
            d[cf] = " ".join(p.title() for p in (nome, cognome) if p)
    return d


def nome_di(cf_ente, cache=None):
    """Nome e cognome del RTD per un ente, o None. `cache` e' l'indice()
    gia' costruito, per chi ne chiede tanti di fila (genera_pec.py su un
    lotto intero) senza riscaricare/riparsare a ogni riga."""
    d = cache if cache is not None else indice()
    return d.get(cf_ente)


# ------------------------------------------------------------------ gate
def gate():
    """Quanti dei NOSTRI enti hanno un RTD nominato. Rimisura la copertura
    del 92,7% di liceita.md §7 sui dati di oggi, non su quelli di settembre."""
    d = indice()
    cx = sqlite3.connect(DB)
    righe = cx.execute("""
        SELECT DISTINCT cf_ente FROM ente_contatti
        WHERE cf_ente IN (SELECT DISTINCT cf_ente FROM punteggio)
    """).fetchall()
    if not righe:
        righe = cx.execute("SELECT DISTINCT cf_ente FROM ente_contatti").fetchall()
        print("(tabella punteggio non disponibile: misurato su tutti gli enti)")
    tot = len(righe)
    con_rtd = sum(1 for (cf,) in righe if cf in d)
    print(f"\n  RTD nel dataset IndicePA        {len(d):>8,}")
    print(f"  enti nel nostro bacino           {tot:>8,}")
    print(f"  ...con un RTD nominato           {con_rtd:>8,}"
          f"  {con_rtd/max(tot,1)*100:5.1f}%")
    cx.close()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--nome", metavar="CF")
    a = ap.parse_args()
    if a.nome:
        n = nome_di(a.nome)
        print(n or "nessun RTD nominato per questo ente in IndicePA.")
        return 0
    return gate()


if __name__ == "__main__":
    sys.exit(main())
