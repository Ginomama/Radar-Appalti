#!/usr/bin/env python3
"""
Punteggio dei lead (task R18).

Ordinare per importo e' grezzo: un contratto da 900 M€ di Sogei non e' un lead,
e' rumore. Ordinare per data e' peggio: tratta uguale una scadenza che diventera'
una gara e una che verra' rinnovata in silenzio allo stesso fornitore.

Questo file combina tre cose diverse, e le tiene diverse di proposito:

  probabilita'   quanto e' probabile che quella scadenza diventi una porta
                 aperta. **Misurata** sui 30.301 contratti gia' scaduti di R17,
                 non decisa a tavolino.

  servibilita'   se quel contratto lo possiamo servire noi. Non e' una
                 probabilita': un contratto da 50 M€ ha ottime probabilita' di
                 andare a gara e resta comunque irraggiungibile. Questa e'
                 un'assunzione commerciale, ed e' dichiarata come tale.

  urgenza        se siamo nella finestra utile. Troppo presto e l'ente non ha
                 ancora pensato al rinnovo; troppo tardi e ha gia' deciso.
                 Anche questa e' un'euristica: R17 guarda contratti gia'
                 scaduti, quindi sui giorni di anticipo non ha niente da dire.

Confondere le tre in un peso unico e' il modo classico di ottenere un numero
che nessuno sa piu' spiegare. Qui `--perche` le rende tutte e tre.

--------------------------------------------------------------- i moltiplicatori

Non sono costanti scritte a mano: `--calibra` le rimisura da `esito` e le
scrive in tabella. Cosi' non invecchiano, e crescendo i dati il punteggio
migliora da solo. Quello che si misurava il 7 settembre 2026 (base 4,72%):

  ente con storico oltre il 25% di aperture x2,80    ente mai aperto      x0,45
  categoria Cybersecurity                   x1,87    Software verticale   x0,58
  importo 140k-1M                           x1,59    sotto 40k            x0,69
  fornitore uscente micro (1-2 contratti)   x1,27    oltre 200 contratti  x0,70

Due precauzioni che cambiano parecchio questi numeri:

**Smorzamento.** Un ente con tre scadenze e zero aperture non ha davvero
probabilita' zero, ha solo pochi dati. Ogni tasso e' tirato verso la base di
PSEUDO osservazioni fantasma prima di essere creduto.

**L'ente non vede se stesso.** Calcolando la storia di un ente si toglie la
riga che si sta valutando: senza, una scadenza aperta si spiega da sola. Con la
correzione il fattore ente scende da x6,87 a x2,80 — resta il piu' forte dei
quattro, ma la meta' di quello che sembrava.

Una cosa che i numeri dicono e che conviene sapere: le probabilita' migliori
stanno in **Cybersecurity** (x1,92) e **Licenze** (x1,28), che non sono fra le
categorie che FlowLine serve. Le nostre cinque stanno fra x0,74 e x1,12.

Uso:
    python punteggio.py --calibra        # rimisura i moltiplicatori da esito
    python punteggio.py --pesi           # mostra la calibrazione in vigore
    python punteggio.py --applica        # assegna il punteggio alle scadenze
    python punteggio.py --top 25         # i lead migliori adesso
    python punteggio.py --perche <CIG>   # da dove viene quel punteggio

Solo libreria standard. Legge e scrive radar.db.
"""

import argparse
import os
import sqlite3
import sys
from datetime import date, datetime

import categorie

QUI = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(QUI, "radar.db")

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

# Quanto vale il tasso base come "osservazione fantasma" prima di credere ai
# dati veri. Con 30 pseudo-osservazioni, un ente con 3 scadenze resta quasi
# incollato alla base; uno con 300 si muove liberamente.
PSEUDO = 30.0

# Nessun fattore puo' spostare la probabilita' oltre questi limiti. Serve
# perche' i moltiplicatori si compongono: quattro fattori favorevoli senza
# tetto darebbero probabilita' sopra 1, che non vuol dire niente.
MOLT_MIN, MOLT_MAX = 0.35, 3.0

# A cosa corrisponde 100. Non a una probabilita' teorica: normalizzare su un
# massimo che non si raggiunge mai schiaccia tutto in basso (il primo tentativo
# metteva il 97% dei lead sotto 20 e rendeva il punteggio inutile per
# ordinare). 100 = quattro volte il tasso base con servibilita' e urgenza
# piene, che sui dati veri e' circa il 99esimo percentile.
RIF_MULT = 4.0

# --------------------------------------------------------- servibilita'
#
# Assunzione commerciale, non misura. FlowLine e' una struttura piccola:
# sotto una certa soglia il contratto non ripaga il lavoro di acquisirlo,
# sopra un'altra non e' aggredibile senza struttura e referenze.
# Le categorie che FlowLine sa davvero servire (le stesse di genera_pec.py).
# Fuori licenze e abbonamenti (rivendita), connettivita' (telco), datacenter
# (infrastruttura), cybersecurity (competenza specialistica).
#
# Serve tenerlo separato dalla probabilita': misurando, le probabilita' migliori
# stanno proprio in Cybersecurity (x1,87) e Abbonamenti (x2,08), che non
# sappiamo servire. Senza questo filtro il punteggio metteva in cima quattro
# lead perfetti e irraggiungibili.
RILEVANTI = ("Sviluppo software", "Dati e analytics", "Gestione documentale",
             "Manutenzione e assistenza", "Consulenza IT")

# Non zero: un contratto di categoria adiacente ogni tanto si prende lo stesso,
# e azzerarlo lo toglierebbe per sempre dalla lista invece che metterlo in coda.
SERV_FUORI_CATEGORIA = 0.25

SERV = [
    (0,        10_000,   0.25),   # briciole: si prendono solo se capitano
    (10_000,   40_000,   0.70),
    (40_000,   800_000,  1.00),   # la fascia dove si compete davvero
    (800_000,  3_000_000, 0.55),
    (3_000_000, 10_000_000, 0.20),
    (10_000_000, None,   0.05),   # Sogei: rumore, non lead
]

# ------------------------------------------------------------- urgenza
#
# Euristica dichiarata. Il picco e' fra i due e i sette mesi: prima l'ente non
# ha ancora aperto il fascicolo, dopo ha gia' deciso con chi rinnovare.
URGENZA = [
    (-9999, 0,   0.00),   # gia' scaduto
    (0,     30,  0.30),   # tardi: la decisione e' presa
    (30,    60,  0.70),
    (60,    210, 1.00),   # la finestra
    (210,   365, 0.65),
    (365,   9999, 0.25),  # troppo presto per essere credibili
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS punteggio_pesi (
    fattore        TEXT NOT NULL,
    valore         TEXT NOT NULL,
    osservazioni   INTEGER NOT NULL,
    aperture       INTEGER NOT NULL,
    tasso          REAL NOT NULL,
    moltiplicatore REAL NOT NULL,
    calcolato_il   TEXT NOT NULL,
    PRIMARY KEY (fattore, valore)
);

CREATE TABLE IF NOT EXISTS punteggio (
    cig           TEXT PRIMARY KEY,
    punteggio     INTEGER NOT NULL,
    probabilita   REAL NOT NULL,
    servibilita   REAL NOT NULL,
    urgenza       REAL NOT NULL,
    valore_atteso REAL,
    spiegazione   TEXT,
    calcolato_il  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_punteggio_p ON punteggio (punteggio DESC);
"""

APERTA = "(esito = 'nuovo_fornitore' OR COALESCE(contendibile, 0) = 1)"


# ------------------------------------------------------------- fasce
def fascia_importo(v):
    if v is None:
        return "ignoto"
    for lim, et in ((40_000, "sotto 40k"), (140_000, "40-140k"),
                    (1_000_000, "140k-1M"), (10_000_000, "1-10M")):
        if v < lim:
            return et
    return "oltre 10M"


def fascia_fornitore(n):
    """n = quanti contratti ha vinto in tutto il verticale l'uscente."""
    if n is None:
        return "ignoto"
    for lim, et in ((3, "micro 1-2"), (11, "piccolo 3-10"),
                    (51, "medio 11-50"), (201, "grande 51-200")):
        if n < lim:
            return et
    return "big oltre 200"


def fascia_ente(n, aperture):
    """Storia dell'ente. Sotto le 3 osservazioni non si dice niente: e' la
    differenza fra 'questo ente non apre mai' e 'di questo ente non sappiamo'."""
    if not n or n < 3:
        return "poco noto"
    q = aperture / n
    if q == 0:
        return "mai aperta"
    if q < 0.10:
        return "sotto 10%"
    if q < 0.25:
        return "10-25%"
    return "oltre 25%"


def categoria_di(cpv, oggetto):
    c = categorie.classifica((cpv or "").split("-")[0], oggetto or "")
    if isinstance(c, tuple):
        c = c[0]
    return c or "Non classificato"


def interpola(tabelle, x):
    for lo, hi, v in tabelle:
        if x >= lo and (hi is None or x < hi):
            return v
    return tabelle[-1][2]


# ---------------------------------------------------------- calibrazione
def calibra(cx):
    """Rimisura i moltiplicatori sui contratti gia' scaduti."""
    cx.executescript(SCHEMA)
    tot = cx.execute("SELECT count(*) FROM esito").fetchone()[0]
    if tot < 500:
        sys.exit("tabella 'esito' quasi vuota. Prima: python esito.py --misura")
    base = cx.execute(f"SELECT avg({APERTA}) FROM esito").fetchone()[0]
    print(f"base: {base * 100:.2f}% di porta aperta su {tot:,} contratti scaduti\n")

    # uscente -> quanti contratti ha in tutto il verticale
    dim = dict(cx.execute(
        "SELECT codice_fiscale, count(DISTINCT cig) FROM aggiudicatari "
        "WHERE codice_fiscale IS NOT NULL GROUP BY codice_fiscale"))
    primo = dict(cx.execute(
        "SELECT cig, min(codice_fiscale) FROM aggiudicatari GROUP BY cig"))

    # storia per ente, dalla stessa tabella
    storia = {cf: (n, a) for cf, n, a in cx.execute(
        f"SELECT cf_ente, count(*), sum({APERTA}) FROM esito GROUP BY cf_ente")}

    conta = {}   # (fattore, valore) -> [osservazioni, aperture]
    for cig, cf_ente, cpv, ogg, imp, es, con in cx.execute(
            "SELECT cig, cf_ente, cod_cpv, oggetto, importo, esito, "
            "contendibile FROM esito"):
        aperta = 1 if (es == "nuovo_fornitore" or con == 1) else 0
        n_ente, a_ente = storia.get(cf_ente, (0, 0))
        # L'ente non deve "vedere se stesso": si toglie la riga corrente, se no
        # una scadenza aperta si spiega da sola e il fattore sembra piu' forte.
        chiavi = [
            ("importo",   fascia_importo(imp)),
            ("fornitore", fascia_fornitore(dim.get(primo.get(cig)))),
            ("categoria", categoria_di(cpv, ogg)),
            ("ente",      fascia_ente(n_ente - 1, a_ente - aperta)),
        ]
        for k in chiavi:
            r = conta.setdefault(k, [0, 0])
            r[0] += 1
            r[1] += aperta

    ora = datetime.now().isoformat(timespec="seconds")
    cx.execute("DELETE FROM punteggio_pesi")
    for (fattore, valore), (n, a) in sorted(conta.items()):
        if n < 100:                     # troppo pochi per dire qualcosa
            continue
        # Smorzamento verso la base: un ente con 3 scadenze non ha davvero
        # probabilita' zero, ha solo pochi dati.
        tasso = (a + PSEUDO * base) / (n + PSEUDO)
        # 'ignoto' resta neutro per principio, qualunque cosa dica il campione.
        # Non sapere chi fosse l'uscente non e' un buon segno ne' cattivo: e'
        # assenza di informazione, e trattarla come segnale premia le righe
        # peggio compilate. Con 111 osservazioni usciva x3,00, il massimo.
        if valore == "ignoto":
            molt = 1.0
        else:
            molt = max(MOLT_MIN, min(MOLT_MAX, tasso / base))
        cx.execute(
            "INSERT OR REPLACE INTO punteggio_pesi VALUES (?,?,?,?,?,?,?)",
            (fattore, valore, n, a, round(tasso, 5), round(molt, 4), ora))
    cx.execute("INSERT OR REPLACE INTO punteggio_pesi VALUES "
               "('_base', '_base', ?, ?, ?, 1.0, ?)",
               (tot, int(base * tot), round(base, 5), ora))
    cx.commit()
    pesi(cx)


def leggi_pesi(cx):
    p, base = {}, None
    for f, v, n, a, t, m, _ in cx.execute("SELECT * FROM punteggio_pesi"):
        if f == "_base":
            base = t
        else:
            p[(f, v)] = (m, n, t)
    if base is None:
        sys.exit("nessuna calibrazione. Prima: python punteggio.py --calibra")
    return p, base


def pesi(cx):
    p, base = leggi_pesi(cx)
    print(f"=== calibrazione in vigore — base {base * 100:.2f}% ===\n")
    for fattore in ("importo", "fornitore", "categoria", "ente"):
        righe = [(v, m, n, t) for (f, v), (m, n, t) in p.items() if f == fattore]
        if not righe:
            continue
        print(f"  {fattore.upper()}")
        for v, m, n, t in sorted(righe, key=lambda r: -r[1]):
            barra = "#" * int(m * 12)
            print(f"    {v:22s} {n:>7,} oss  {t * 100:5.2f}%  x{m:4.2f} {barra}")
        print()


# ------------------------------------------------------------ punteggio
def valuta(p, base, *, importo, fascia_forn, categoria, fascia_ent, giorni):
    """Ritorna (punteggio 0-100, probabilita, servibilita, urgenza, dettaglio)."""
    prob, dett = base, []
    for fattore, valore in (("importo", fascia_importo(importo)),
                            ("fornitore", fascia_forn),
                            ("categoria", categoria),
                            ("ente", fascia_ent)):
        m = p.get((fattore, valore), (1.0, 0, 0))[0]
        prob *= m
        dett.append(f"{fattore}={valore} x{m:.2f}")
    prob = min(prob, 1.0)

    serv = interpola([(a, b, v) for a, b, v in SERV], importo or 0)
    if categoria not in RILEVANTI:
        serv *= SERV_FUORI_CATEGORIA
    urg = interpola([(a, b, v) for a, b, v in URGENZA],
                    giorni if giorni is not None else 9999)

    # Il punteggio e' un prodotto, non una somma: se una delle tre e' zero il
    # lead non vale niente, e una somma pesata lo terrebbe comunque a galla.
    punti = min(100, round(100 * (prob * serv * urg) / (RIF_MULT * base)))
    dett.append(f"servibilita {serv:.2f}"
                + ("" if categoria in RILEVANTI else " (fuori categoria)"))
    dett.append(f"urgenza {urg:.2f} ({giorni}gg)")
    return punti, prob, serv, urg, " · ".join(dett)


def applica(cx):
    cx.executescript(SCHEMA)
    p, base = leggi_pesi(cx)
    dim = dict(cx.execute(
        "SELECT codice_fiscale, count(DISTINCT cig) FROM aggiudicatari "
        "WHERE codice_fiscale IS NOT NULL GROUP BY codice_fiscale"))
    primo = dict(cx.execute(
        "SELECT cig, min(codice_fiscale) FROM aggiudicatari GROUP BY cig"))
    storia = {cf: (n, a) for cf, n, a in cx.execute(
        f"SELECT cf_ente, count(*), sum({APERTA}) FROM esito GROUP BY cf_ente")}

    oggi = date.today()
    ora = datetime.now().isoformat(timespec="seconds")
    cx.execute("DELETE FROM punteggio")
    n = 0
    for (cig, cf, cpv, ogg, imp, fine) in cx.execute(
            "SELECT cig, cf_amministrazione_appaltante, cpv_norm, oggetto_lotto,"
            "       importo_aggiudicazione, data_termine_contrattuale "
            "FROM v_scadenze_contattabili"):
        try:
            giorni = (date.fromisoformat(fine[:10]) - oggi).days
        except (TypeError, ValueError):
            giorni = None
        ne, ae = storia.get(cf, (0, 0))
        punti, prob, serv, urg, dett = valuta(
            p, base, importo=imp,
            fascia_forn=fascia_fornitore(dim.get(primo.get(cig))),
            categoria=categoria_di(cpv, ogg),
            fascia_ent=fascia_ente(ne, ae), giorni=giorni)
        cx.execute("INSERT OR REPLACE INTO punteggio VALUES (?,?,?,?,?,?,?,?)",
                   (cig, punti, round(prob, 5), serv, urg,
                    round((imp or 0) * prob, 2), dett, ora))
        n += 1
        if n % 3000 == 0:
            cx.commit()
    cx.commit()
    print(f"punteggio assegnato a {n:,} scadenze.\n")
    distribuzione(cx)


def distribuzione(cx):
    print("=== distribuzione ===\n")
    # Le soglie sono i percentili veri della distribuzione, non multipli di 20
    # scelti a occhio: un lead da 45 e' gia' nel 5% migliore.
    for et, filtro in (("60-100  eccezionali (top 3%)", "punteggio >= 60"),
                       ("35-59   ottimi (top 8%)", "punteggio BETWEEN 35 AND 59"),
                       ("18-34   buoni (top 20%)", "punteggio BETWEEN 18 AND 34"),
                       ("8-17    marginali", "punteggio BETWEEN 8 AND 17"),
                       ("0-7     non vale la pena", "punteggio < 8")):
        n = cx.execute(f"SELECT count(*) FROM punteggio WHERE {filtro}").fetchone()[0]
        print(f"  {et:28s} {n:>6,}")
    tot = cx.execute("SELECT count(*) FROM punteggio").fetchone()[0]
    alti = cx.execute("SELECT count(*) FROM punteggio WHERE punteggio >= 60").fetchone()[0]
    print(f"\n  {alti:,} lead sopra 60 su {tot:,}: e' la lista su cui lavorare.")


def top(cx, quanti):
    righe = cx.execute("""
        SELECT p.punteggio, p.probabilita, s.ente, s.provincia, s.categoria,
               s.importo_aggiudicazione, s.data_termine_contrattuale,
               s.fornitore_uscente, s.oggetto_lotto, p.cig
        FROM punteggio p JOIN v_scadenze_contattabili s ON s.cig = p.cig
        ORDER BY p.punteggio DESC, s.importo_aggiudicazione DESC
        LIMIT ?""", (quanti,)).fetchall()
    if not righe:
        print("nessun punteggio. Prima: python punteggio.py --applica")
        return
    print(f"=== i {len(righe)} lead migliori ===\n")
    for pu, pr, ente, prov, cat, imp, fine, forn, ogg, cig in righe:
        eur = f"{imp / 1000:,.0f} k€" if imp else "—"
        print(f"  {pu:3d}  {(ente or '?')[:44]:46s} {(prov or '')[:12]:13s} {eur:>10s}")
        print(f"       scade {fine}  ·  {(cat or '?')[:26]:28s} p={pr * 100:.1f}%")
        print(f"       uscente: {(forn or '?')[:60]}")
        print(f"       {(ogg or '')[:74]}")
        print(f"       {cig}\n")


def perche(cx, cig):
    r = cx.execute(
        "SELECT punteggio, probabilita, servibilita, urgenza, valore_atteso, "
        "spiegazione FROM punteggio WHERE cig = ?", (cig,)).fetchone()
    if not r:
        print(f"{cig} non ha un punteggio: o non e' fra le scadenze "
              f"contattabili, o serve --applica.")
        return
    pu, pr, se, ur, va, dett = r
    s = cx.execute("SELECT ente, provincia, importo_aggiudicazione, "
                   "data_termine_contrattuale, fornitore_uscente, oggetto_lotto "
                   "FROM v_scadenze_contattabili WHERE cig = ?", (cig,)).fetchone()
    print(f"=== {cig} — punteggio {pu}/100 ===\n")
    if s:
        print(f"  {s[0]} ({s[1]})")
        print(f"  {(s[5] or '')[:74]}")
        print(f"  scade {s[3]}  ·  {s[2] or 0:,.0f} €  ·  uscente: {s[4]}\n")
    print(f"  probabilita' di porta aperta   {pr * 100:5.2f}%")
    print(f"  servibilita'                   {se:5.2f}")
    print(f"  urgenza                        {ur:5.2f}")
    print(f"  valore atteso                  {va or 0:,.0f} €\n")
    print("  da dove viene:")
    for pezzo in dett.split(" · "):
        print(f"    {pezzo}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibra", action="store_true")
    ap.add_argument("--pesi", action="store_true")
    ap.add_argument("--applica", action="store_true")
    ap.add_argument("--top", type=int, nargs="?", const=20)
    ap.add_argument("--perche", metavar="CIG")
    a = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit(f"{DB} non esiste.")
    cx = sqlite3.connect(DB)

    if a.calibra:
        calibra(cx)
    elif a.pesi:
        pesi(cx)
    elif a.applica:
        applica(cx)
    elif a.perche:
        perche(cx, a.perche.strip().upper())
    elif a.top:
        top(cx, a.top)
    else:
        ap.print_help()
    cx.close()


if __name__ == "__main__":
    main()
