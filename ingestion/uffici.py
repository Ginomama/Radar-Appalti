"""Uffici degli enti, per scrivere a chi decide invece che al protocollo (R28).

IL PROBLEMA MISURATO. 18 PEC partite, 18 consegnate, 0 risposte. Le consegne
sono confermate dalle ricevute, quindi il messaggio arriva: si ferma dopo. La
spiegazione piu' probabile e' che arriva al protocollo generale, che smista
per competenza e non ha nessun motivo di dare priorita' a un'offerta non
richiesta. Non e' il testo a non funzionare: e' il destinatario.

LA STRADA SCELTA, E PERCHE' NON L'ALTRA. R28 era scritto come "trovare il nome
del RUP e del responsabile transizione digitale". Quei nomi ci sono, nei
dataset 'ou' e 'aoo' di IndicePA (nome_resp, cogn_resp, mail_resp, tel_resp).
Sono pero' dati personali di persone fisiche, e docs/liceita.md dice
esplicitamente che non li memorizziamo finche' quella verifica non e' chiusa.

Ma il nome della persona non e' quello che serve. Serve **l'ufficio giusto**:
scrivere a 'sistemi.informativi@pec.comune.x.it' invece che
'protocollo@pec.comune.x.it' risolve lo stesso problema — il messaggio arriva
a chi si occupa di informatica invece che a uno smistamento — e la PEC di un
ufficio e' un recapito organizzativo, esattamente della stessa natura di
quella dell'ente che gia' usiamo. Nessun dato personale, nessun gate aperto.

Quindi questo file carica di 'ou' SOLO le colonne organizzative. Le quattro
colonne personali sono elencate in ESCLUSE e scartate esplicitamente: non e'
una dimenticanza, ed e' scritto qui perche' fra un anno nessuno lo indovini.

Uso:
    python uffici.py --gate       # quanti enti hanno un ufficio IT con PEC sua
    python uffici.py --ingest     # carica la tabella ufficio
    python uffici.py --ente <cf>  # gli uffici di un ente, con la scelta fatta

Solo libreria standard.
"""

import argparse
import csv
import io
import json
import os
import re
import sqlite3
import sys
from urllib.request import Request

import anac_http as h

QUI = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(QUI, "radar.db")
CACHE = os.path.join(QUI, "recon_out", "ipa")
API = "https://indicepa.gov.it/ipa-dati/api/3/action"

csv.field_size_limit(10 ** 7)

# Le colonne personali di 'ou'. Si nominano per scartarle: un domani qualcuno
# aggiungera' una colonna alla tabella e deve trovare scritto perche' queste
# quattro non ci sono. Vedi docs/liceita.md §1.
ESCLUSE = ("nome_resp", "cogn_resp", "mail_resp", "tel_resp")

# Un ufficio e' interessante se si occupa di informatica. L'ordine conta: il
# punteggio piu' alto vince, perche' un ente grande ha sia "Sistemi
# Informativi" sia "Ufficio Acquisti" e il primo e' quello giusto.
#
# I pesi non sono un'opinione sul valore dell'ufficio: dicono quanto il nome
# e' specifico. "Sistemi informativi" e' inequivocabile; "innovazione" da sola
# in un comune puo' essere lo sportello per le imprese.
RILEVANZA = [
    (95, r"sistem\w*\s+informat|servizi\w*\s+informat|c\.?e\.?d\.?\b"),
    (90, r"\bict\b|informatica|informatici|tecnologie\s+informat"),
    (85, r"transizione\s+(al\s+)?digital|\brtd\b"),
    (75, r"agenda\s+digitale|digitalizzazion|innovazione\s+(tecnolog|digital)"),
    (60, r"\bdigital\w*\b|telematic"),
    (45, r"innovazion|tecnolog"),
    (30, r"statistic\w*\s+e\s+informat|programmazione\s+e\s+informat"),
]
RILEVANZA = [(p, re.compile(r, re.I)) for p, r in RILEVANZA]

# Uffici che il nome fa sembrare informatici e non lo sono. Senza questo,
# "Ufficio Anagrafe - servizi demografici informatizzati" entrava a pieni voti.
FALSI_AMICI = re.compile(
    r"anagraf|demografic|tribut|ragioneri|protocoll|personale|"
    r"biblioteca|scolastic|cimiter|tecnico\s+manutent", re.I)

MIN_PUNTI = 45


def risorsa_ou():
    req = Request(f"{API}/package_show?id=ou",
                  headers={"User-Agent": h.UA, "Accept": "application/json"})
    with h.apri(req, timeout=60) as r:
        d = json.loads(r.read())["result"]
    for x in d.get("resources", []):
        if (x.get("format") or "").upper() == "TXT":
            return x["url"]
    raise SystemExit("nessuna risorsa TXT nel dataset 'ou'")


def scarica():
    os.makedirs(CACHE, exist_ok=True)
    dest = os.path.join(CACHE, "ou.txt")
    if os.path.exists(dest) and os.path.getsize(dest) > 1_000_000:
        return dest
    url = risorsa_ou()
    print("scarico ou.txt ...", end=" ", flush=True)
    tot = 0
    with h.apri(Request(url, headers={"User-Agent": h.UA}), timeout=600) as r, \
            open(dest, "wb") as f:
        while True:
            p = r.read(262144)
            if not p:
                break
            f.write(p)
            tot += len(p)
    print(f"{tot/1e6:.1f} MB")
    return dest


def pec_ufficio(r):
    """La PEC dell'ufficio, se ne dichiara una.

    IndicePA mette fino a tre recapiti con il tipo accanto; solo quelli
    marcati 'Pec' valgono, gli altri sono caselle ordinarie che un ente
    pubblico non e' tenuto a leggere.
    """
    for i in (1, 2, 3):
        tipo = (r.get(f"tipo_mail{i}") or "").strip().lower()
        val = (r.get(f"mail{i}") or "").strip().lower()
        if tipo == "pec" and val and val != "null" and "@" in val:
            return val
    return None


def punteggio(des):
    """Quanto quell'ufficio somiglia a chi compra informatica."""
    if not des:
        return 0
    if FALSI_AMICI.search(des):
        return 0
    for p, rx in RILEVANZA:
        if rx.search(des):
            return p
    return 0


def leggi(path):
    with io.open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            yield {k: v for k, v in r.items() if k not in ESCLUSE}


def raccogli(path):
    """cod_amm -> lista di uffici rilevanti, dal migliore."""
    per_ente = {}
    letti = tot_pec = 0
    for r in leggi(path):
        letti += 1
        p = punteggio(r.get("des_ou"))
        if p < MIN_PUNTI:
            continue
        pec = pec_ufficio(r)
        if pec:
            tot_pec += 1
        per_ente.setdefault(r.get("cod_amm"), []).append(dict(
            cod_ou=r.get("cod_ou"), des_ou=(r.get("des_ou") or "").strip(),
            pec=pec, comune=r.get("comune"), provincia=r.get("provincia"),
            punti=p))
    for v in per_ente.values():
        # Con la PEC prima: un ufficio giustissimo senza recapito non serve.
        v.sort(key=lambda x: (x["pec"] is not None, x["punti"]), reverse=True)
    return per_ente, letti, tot_pec


# ------------------------------------------------------------------ gate
def gate():
    """R28.1 — vale la pena? Si misura prima di costruire.

    La domanda non e' "quanti uffici informatici esistono in Italia" ma
    "quanti dei NOSTRI lead hanno un ufficio informatico con una PEC diversa
    da quella del protocollo". Se sono pochi, questa strada non risolve il
    problema delle zero risposte e conviene saperlo adesso.
    """
    path = scarica()
    per_ente, letti, con_pec = raccogli(path)
    print(f"\n  uffici letti          {letti:>8,}")
    print(f"  rilevanti (>= {MIN_PUNTI})    {sum(len(v) for v in per_ente.values()):>8,}"
          f"  in {len(per_ente):,} enti")
    print(f"  di questi con PEC     {con_pec:>8,}")

    cx = sqlite3.connect(DB)
    cx.row_factory = sqlite3.Row

    # I nostri lead veri: enti con una scadenza a 12 mesi e una PEC d'ente.
    righe = cx.execute("""
        SELECT DISTINCT e.cf_ente, e.cod_amm, e.denominazione_ipa, e.pec
        FROM ente_contatti e
        WHERE e.cod_amm IS NOT NULL AND e.pec IS NOT NULL
          AND e.cf_ente IN (SELECT DISTINCT cf_ente FROM punteggio)
    """).fetchall()
    if not righe:
        righe = cx.execute("""
            SELECT DISTINCT e.cf_ente, e.cod_amm, e.denominazione_ipa, e.pec
            FROM ente_contatti e WHERE e.cod_amm IS NOT NULL AND e.pec IS NOT NULL
        """).fetchall()
        print("  (tabella punteggio non disponibile: misurato su tutti gli enti)")

    tot = len(righe)
    con_ufficio = diversa = 0
    esempi = []
    for r in righe:
        uf = per_ente.get(r["cod_amm"]) or []
        migliore = next((u for u in uf if u["pec"]), None)
        if not migliore:
            continue
        con_ufficio += 1
        if migliore["pec"] != (r["pec"] or "").strip().lower():
            diversa += 1
            if len(esempi) < 12:
                esempi.append((r["denominazione_ipa"], migliore["des_ou"],
                               migliore["pec"], r["pec"]))

    print(f"\n  enti nel nostro bacino          {tot:>8,}")
    print(f"  con un ufficio IT che ha PEC    {con_ufficio:>8,}"
          f"  {con_ufficio/max(tot,1)*100:5.1f}%")
    print(f"  ...e la PEC e' DIVERSA da oggi  {diversa:>8,}"
          f"  {diversa/max(tot,1)*100:5.1f}%")

    if esempi:
        print("\n  esempi (ente / ufficio / PEC nuova / PEC usata finora):")
        for den, des, pn, pv in esempi:
            print(f"    {(den or '?')[:44]}")
            print(f"      -> {des[:52]}")
            print(f"         {pn}")
            print(f"         invece di {pv}")

    q = diversa / max(tot, 1)
    print("\n  " + "-" * 66)
    if q >= 0.25:
        print(f"  {q*100:.0f}% dei lead si possono indirizzare a un ufficio")
        print("  informatico invece che al protocollo. Vale la pena: e' la")
        print("  spiegazione piu' probabile delle zero risposte, e si prova")
        print("  su un lotto solo per capire se il tasso cambia.")
    elif q >= 0.10:
        print(f"  Solo il {q*100:.0f}%. Utile ma parziale: si puo' usare dove c'e',")
        print("  senza aspettarsi che sposti il tasso complessivo.")
    else:
        print(f"  Solo il {q*100:.0f}%: questa strada non risolve le zero risposte.")
        print("  Se il problema resta, la domanda torna a essere il nome della")
        print("  persona — e quello passa da docs/liceita.md.")
    print("  " + "-" * 66 + "\n")
    return 0


# ---------------------------------------------------------------- ingest
DDL = """
CREATE TABLE IF NOT EXISTS ufficio (
    cod_amm      TEXT NOT NULL,
    cod_ou       TEXT NOT NULL,
    des_ou       TEXT,
    pec          TEXT,
    comune       TEXT,
    provincia    TEXT,
    punti        INTEGER,
    aggiornato_il TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (cod_amm, cod_ou)
);
CREATE INDEX IF NOT EXISTS ix_ufficio_amm ON ufficio (cod_amm, punti DESC);
"""


def ingest():
    path = scarica()
    per_ente, letti, con_pec = raccogli(path)
    cx = sqlite3.connect(DB)
    cx.executescript(DDL)
    righe = [(a, u["cod_ou"], u["des_ou"], u["pec"], u["comune"],
              u["provincia"], u["punti"])
             for a, v in per_ente.items() for u in v if a]
    cx.executemany(
        "INSERT INTO ufficio (cod_amm, cod_ou, des_ou, pec, comune, provincia, punti) "
        "VALUES (?,?,?,?,?,?,?) ON CONFLICT (cod_amm, cod_ou) DO UPDATE SET "
        "des_ou=excluded.des_ou, pec=excluded.pec, punti=excluded.punti, "
        "aggiornato_il=CURRENT_TIMESTAMP", righe)
    cx.commit()
    n = cx.execute("SELECT count(*) FROM ufficio").fetchone()[0]
    npec = cx.execute("SELECT count(*) FROM ufficio WHERE pec IS NOT NULL").fetchone()[0]
    print(f"ufficio: {n:,} righe ({npec:,} con PEC), da {letti:,} uffici letti")
    cx.close()
    return 0


def per_ente_cf(cf):
    cx = sqlite3.connect(DB)
    cx.row_factory = sqlite3.Row
    e = cx.execute("SELECT * FROM ente_contatti WHERE cf_ente = ?", (cf,)).fetchone()
    if not e:
        sys.exit(f"ente {cf} non trovato in ente_contatti")
    print(f"\n  {e['denominazione_ipa']}")
    print(f"  PEC usata finora: {e['pec']}\n")
    uf = cx.execute(
        "SELECT * FROM ufficio WHERE cod_amm = ? ORDER BY (pec IS NULL), punti DESC",
        (e["cod_amm"],)).fetchall()
    if not uf:
        print("  nessun ufficio informatico dichiarato in IndicePA.")
        print("  Si continua a scrivere al protocollo: non e' un errore, e'")
        print("  che l'ente non ha pubblicato un ufficio dedicato.\n")
        return 0
    for u in uf:
        segno = "->" if u["pec"] else "  "
        print(f"  {segno} [{u['punti']:2d}] {u['des_ou'][:52]}")
        print(f"        {u['pec'] or '(nessuna PEC dichiarata)'}")
    print()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--ingest", action="store_true")
    ap.add_argument("--ente", metavar="CF")
    a = ap.parse_args()
    if a.ente:
        return per_ente_cf(a.ente)
    if a.ingest:
        return ingest()
    return gate()


if __name__ == "__main__":
    sys.exit(main())
