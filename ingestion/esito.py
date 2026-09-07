#!/usr/bin/env python3
"""
Che fine ha fatto la scadenza (task R17).

La domanda che dice se il prodotto vale: **quante delle scadenze che segnaliamo
diventano davvero una porta aperta?** Senza risposta non sappiamo nemmeno quali
segnalare per prime, e stiamo ordinando i lead per data invece che per valore.

Come si risponde: dopo la scadenza, l'ente ripubblica un CIG sullo stesso
oggetto. Si cerca quel seguito e si guarda chi l'ha vinto e con che procedura.

  nessun_seguito        il servizio e' cessato, o e' passato in convenzione
                        Consip, o l'ente non ha ancora pubblicato
  rinnovato             stesso fornitore. Era una porta chiusa
  nuovo_fornitore       la porta era aperta e l'ha presa qualcun altro
  seguito_senza_esito   il seguito c'e' ma ANAC non ha ancora l'aggiudicatario

E, su un asse separato, `contendibile`: se il seguito e' andato a gara vera o
per affidamento diretto. Sono due domande diverse e la roadmap le confondeva in
una sola lista: un rinnovo allo stesso fornitore *dopo una gara aperta* e' un
mercato contendibile in cui abbiamo perso, non una porta chiusa. Tenerle
separate costa una colonna e cambia la conclusione.

--------------------------------------------------------------------- il match

Trovare il seguito e' il punto difficile. Tre filtri in cascata, ognuno nato da
un falso positivo visto nei dati:

1. **stesso ente, finestra -6/+12 mesi** dalla scadenza. Prima, perche' i
   rinnovi si pubblicano spesso qualche mese prima della scadenza vera.

2. **peso IDF invece del conteggio dei token.** Una lista di stopword non
   converge mai: dopo tre giri restavano fuori 'procedura negoziata senza
   previa pubblicazione', il preambolo PNRR, gli articoli del Codice. Pesare
   ogni parola per quanto e' rara nei 236.891 oggetti li annulla da soli
   ('servizio' vale 1,4, 'symantec' vale 9,1) e non richiede manutenzione.

3. **boilerplate per ente.** Restava un ultimo buco: parole rare in Italia ma
   banali per quell'ente. 'giannina gaslini' e' rarissimo nel dataset e appare
   in ogni bando dell'ospedale Gaslini, quindi due contratti scollegati si
   somigliavano per il nome del committente. Si scartano le parole che
   compaiono in oltre il 30% degli oggetti dello stesso ente.

Serve infine una **massa minima di parole distintive in comune** (MIN_RARO), non
solo un rapporto: due oggetti fatti al 100% di burocrazia identica hanno
somiglianza 1,0 e non vogliono dire niente.

Precisione non perfetta e dichiarata: restano dentro gli acquisti fratelli dello
stesso progetto ('PNRR Scuola 4.0 Azione 2 - laboratori' contro '... - software
gestionale'), che sono lo stesso progetto ma non lo stesso contratto. Per questo
ogni riga porta il proprio `punteggio`: sopra 0,60 il match e' solido, sotto e'
da guardare prima di crederci.

Uso:
    python esito.py --misura              # calcola tutto, scrive la tabella
    python esito.py --misura --da 2024-01-01
    python esito.py --stato               # la risposta: conviene continuare?
    python esito.py --cig Z1A2B3C4D5      # perche' quel CIG e' finito li'
    python esito.py --dubbi               # i match piu' deboli, da controllare

Dipendenze: solo stdlib. Legge e scrive radar.db.
"""

import argparse
import collections
import math
import os
import re
import sqlite3
import sys
from datetime import datetime

QUI = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(QUI, "radar.db")

# 23 oggetti su 283.929 contengono byte cp1252 grezzi finiti in database come
# caratteri di controllo C1 (U+0092 e simili). Sono irrilevanti come dato, ma
# bastano a far morire una stampa sulla console di Windows, che e' cp1252 e non
# sa cosa farsene. Meglio un punto interrogativo che un traceback a meta' report.
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

# Finestra in cui cercare il seguito, in mesi rispetto alla scadenza.
# Il -6 non e' simmetria: i rinnovi si pubblicano prima che il contratto scada,
# altrimenti il servizio si interrompe.
PRIMA_MESI, DOPO_MESI = 6, 12

# Un token conta come "distintivo" sopra questo IDF: idf 4,5 su 236.891 oggetti
# vuol dire che la parola compare in meno di 2.700 bandi, l'1,1%.
RARO = 4.5

# Massa IDF minima di parole distintive in comune. Dodici sono circa due parole
# davvero specifiche ('symantec' + 'broadcom'), o tre mediamente specifiche.
MIN_RARO = 12.0

# ...ma una soglia fissa e' sbagliata per gli oggetti corti. 'CANONE ANNUALE
# DARKTRACE' e' specifico quanto basta e ha una parola rara sola: chiedergliene
# due lo rende inconfrontabile per costruzione. Con la soglia fissa il 26,9%
# dei contratti non veniva nemmeno cercato. Si chiede quindi il minore fra
# MIN_RARO e quasi tutta la massa rara che l'oggetto possiede.
QUOTA_CORTA = 0.80

# Sotto questo, pero', non si guarda: e' il peso di una parola mediamente rara.
PAVIMENTO_RARO = 6.0

# E comunque mai su una parola sola. Abbassando la soglia per gli oggetti corti
# era passato un match sul solo token 'quota' fra due manutenzioni scollegate:
# una parola in comune non ha mai fatto un contratto uguale a un altro.
MIN_COMUNI = 2

# Somiglianza minima (Jaccard pesato + bonus CPV) per accettare un seguito.
SOGLIA = 0.42

# Sopra questa soglia il match e' solido; sotto va guardato.
SOLIDO = 0.60

# Una parola che compare in oltre il 30% degli oggetti dello stesso ente non
# distingue niente per quell'ente: e' il suo nome, o il suo modo di scrivere.
QUOTA_BANALE = 0.30
MIN_OGGETTI_ENTE = 3

ESITI = {
    "nessun_seguito":      "nessun seguito trovato",
    "rinnovato":           "rinnovato allo stesso fornitore",
    "nuovo_fornitore":     "vinto da un altro fornitore",
    "seguito_senza_esito": "seguito trovato, aggiudicatario non ancora noto",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS esito (
    cig                    TEXT PRIMARY KEY,
    cf_ente                TEXT,
    ente                   TEXT,
    provincia              TEXT,
    oggetto                TEXT,
    cod_cpv                TEXT,
    data_termine           TEXT,
    importo                REAL,
    cf_uscente             TEXT,
    fornitore_uscente      TEXT,
    cig_seguito            TEXT,
    oggetto_seguito        TEXT,
    data_pubblicazione_seguito TEXT,
    tipo_scelta_seguito    TEXT,
    importo_seguito        REAL,
    cf_subentrante         TEXT,
    fornitore_subentrante  TEXT,
    esito                  TEXT NOT NULL,
    contendibile           INTEGER,
    punteggio              REAL,
    parole_comuni          TEXT,
    calcolato_il           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_esito_esito ON esito (esito);
CREATE INDEX IF NOT EXISTS ix_esito_ente  ON esito (cf_ente);

DROP VIEW IF EXISTS v_esito;
CREATE VIEW v_esito AS
SELECT cig, cf_ente, ente, provincia, oggetto, cod_cpv, data_termine, importo,
       fornitore_uscente, cig_seguito, tipo_scelta_seguito,
       fornitore_subentrante, esito, contendibile, punteggio
FROM esito;
"""


# ------------------------------------------------------------------ testo
def tok(s):
    """Parole di almeno 4 lettere, niente numeri puri.

    I numeri sono quasi sempre protocolli e capitoli di bilancio: due contratti
    diversi con lo stesso numero di determina non c'entrano niente."""
    s = re.sub(r"[^a-z0-9]+", " ", (s or "").lower())
    return {w for w in s.split() if len(w) >= 4 and not w.isdigit()}


class Vocabolario:
    """IDF globale + boilerplate per ente. Costruito una volta, ~10 secondi."""

    def __init__(self, cx):
        df, n = collections.Counter(), 0
        for (o,) in cx.execute("SELECT oggetto_lotto FROM cig"):
            n += 1
            df.update(tok(o))
        # +1 al denominatore: una parola vista una volta sola non deve dividere
        # per zero ne' valere quanto l'infinito.
        self.idf = {w: math.log(n / (1 + c)) for w, c in df.items()}
        self.n = n
        self.cx = cx
        self._banali = {}

    def peso(self, ts):
        # 8.0 per una parola mai vista: e' l'IDF di chi compare ~80 volte,
        # cioe' "rara ma non unica". Sopravvalutarla creerebbe match dal nulla.
        return sum(self.idf.get(w, 8.0) for w in ts)

    def peso_raro(self, ts):
        return sum(i for i in (self.idf.get(w, 8.0) for w in ts) if i >= RARO)

    def distintive(self, ts):
        return sorted((w for w in ts if self.idf.get(w, 8.0) >= RARO),
                      key=lambda w: -self.idf.get(w, 8.0))

    def banali(self, cf):
        """Parole che per questo ente non distinguono niente."""
        if cf in self._banali:
            return self._banali[cf]
        ogg = [r[0] for r in self.cx.execute(
            "SELECT oggetto_lotto FROM cig "
            "WHERE cf_amministrazione_appaltante = ?", (cf,))]
        if len(ogg) < MIN_OGGETTI_ENTE:
            self._banali[cf] = frozenset()
            return self._banali[cf]
        df = collections.Counter()
        for o in ogg:
            df.update(tok(o))
        self._banali[cf] = frozenset(
            w for w, c in df.items() if c > QUOTA_BANALE * len(ogg))
        return self._banali[cf]


def contendibile(tipo):
    """La procedura del seguito lasciava spazio a un concorrente?

    None quando la procedura non e' dichiarata: e' diverso da 'no'."""
    t = (tipo or "").upper()
    if not t:
        return None
    if "AFFIDAMENTO DIRETTO" in t or "SENZA PREVIA" in t or "IN HOUSE" in t:
        return 0
    if "PROCEDURA" in t or "CONFRONTO COMPETITIVO" in t or "GARA" in t:
        return 1
    return None


# ------------------------------------------------------------------ match
# Forme giuridiche e abbreviazioni: due grafie della stessa azienda non devono
# sembrare due aziende. 'DROMEDIAN SRL' e 'DROMEDIAN S.R.L.' sono la stessa.
FORME = ("societaperazioni", "societaaresponsabilitalimitata",
         "societacooperativa", "societaconsortile", "srlsemplificata",
         "srls", "srl", "spa", "sapa", "snc", "sas", "scarl", "scpa",
         "scrl", "soccoop", "cooperativa", "coop", "consorzio", "gmbh",
         "sarl", "bvba", "ltd", "limited", "plc", "inc", "corp", "llc")


def norm_cf(c):
    """CF/P.IVA confrontabile.

    Lo zero-padding non e' pedanteria: 827 aggiudicatari hanno una P.IVA di
    10 cifre, cioe' la stessa dell'altro record ma senza lo zero iniziale."""
    c = re.sub(r"[^A-Z0-9]", "", (c or "").upper())
    if c.isdigit() and len(c) < 11:
        c = c.zfill(11)
    return c or None


def norm_nome(d):
    """Ragione sociale confrontabile: via punteggiatura e forma giuridica."""
    s = re.sub(r"[^a-z0-9]", "", (d or "").lower())
    for f in FORME:                       # solo in coda: 'srl' dentro un nome
        if s.endswith(f) and len(s) > len(f) + 3:   # proprio non va tolto
            s = s[:-len(f)]
            break
    return s if len(s) >= 4 else None


def fornitori(cx, cig):
    """CF, nomi normalizzati e nomi da mostrare, per un CIG.

    Si tengono anche mandanti e consorziate: in un RTI il rinnovo puo' andare
    a un membro diverso dello stesso raggruppamento, ed e' comunque un rinnovo
    per chi c'era gia' dentro."""
    cf, nomi, mostra = set(), set(), []
    for c, d in cx.execute(
            "SELECT codice_fiscale, denominazione FROM aggiudicatari "
            "WHERE cig = ?", (cig,)):
        n = norm_cf(c)
        if n:
            cf.add(n)
        n = norm_nome(d)
        if n:
            nomi.add(n)
        if d and d.strip() and d.strip() not in mostra:
            mostra.append(d.strip())
    return cf, nomi, " + ".join(mostra[:3])


def soglia_raro(voc, t0):
    """Quanta massa rara deve avere in comune un candidato con questo oggetto.

    Adattiva, non fissa: vedi QUOTA_CORTA."""
    return min(MIN_RARO, QUOTA_CORTA * voc.peso_raro(t0))


def cerca_seguito(cx, voc, s):
    """Il miglior candidato sopra soglia, o None."""
    ban = voc.banali(s["cf"])
    t0 = tok(s["oggetto"]) - ban
    minimo = soglia_raro(voc, t0)
    if minimo < PAVIMENTO_RARO:
        return None, "oggetto senza parole distintive"

    migliore = None
    for c in cx.execute(
            "SELECT cig, oggetto_lotto, cod_cpv, data_pubblicazione, "
            "       tipo_scelta_contraente, importo_lotto "
            "FROM cig WHERE cf_amministrazione_appaltante = ? AND cig <> ? "
            "  AND data_pubblicazione BETWEEN date(?, ?) AND date(?, ?)",
            (s["cf"], s["cig"], s["fine"], f"-{PRIMA_MESI} months",
             s["fine"], f"+{DOPO_MESI} months")):
        t1 = tok(c[1]) - ban
        if not t1:
            continue
        com = t0 & t1
        if len(com) < MIN_COMUNI or voc.peso_raro(com) < minimo:
            continue
        j = voc.peso(com) / voc.peso(t0 | t1)
        # Il bonus CPV e' piccolo di proposito: e' un indizio, non una prova.
        # A 0,25 faceva passare da solo coppie con due parole in comune.
        punti = j + (0.10 if (c[2] or "")[:5] == (s["cpv"] or "")[:5] else 0)
        if migliore is None or punti > migliore[0]:
            migliore = (punti, c, com)

    if migliore is None or migliore[0] < SOGLIA:
        return None, "nessun candidato sopra soglia"
    return migliore, None


def classifica(cx, s, trovato):
    """Dal seguito all'esito."""
    if trovato is None:
        return dict(esito="nessun_seguito")
    punti, c, com = trovato
    cf_new, nomi_new, mostra_new = fornitori(cx, c[0])
    cf_old, nomi_old = s["cf_uscente"], s["nomi_uscente"]

    # Il confronto e' su due chiavi, non una. In ANAC la stessa azienda compare
    # con CF diversi: 'DROMEDIAN SRL' risulta 02147390690 su un CIG e
    # 0003015146 sul rinnovo successivo dello stesso Comune. Fidarsi del solo
    # CF gonfiava 'nuovo_fornitore', cioe' proprio la categoria su cui si
    # decide se il prodotto vale.
    if not (cf_new or nomi_new):
        esito = "seguito_senza_esito"
    elif (cf_old & cf_new) or (nomi_old & nomi_new):
        esito = "rinnovato"
    else:
        esito = "nuovo_fornitore"

    return dict(
        esito=esito, cig_seguito=c[0], oggetto_seguito=c[1],
        data_pubblicazione_seguito=c[3], tipo_scelta_seguito=c[4],
        importo_seguito=c[5], cf_subentrante=";".join(sorted(cf_new)) or None,
        fornitore_subentrante=mostra_new or None,
        contendibile=contendibile(c[4]), punteggio=round(punti, 3),
        parole_comuni=" ".join(sorted(com)[:20]) or None)


# ------------------------------------------------------------------ misura
def scaduti(cx, da, a):
    """I contratti gia' scaduti, uno per CIG.

    MIN() sulla data di termine: se un CIG ha piu' aggiudicazioni, la prima
    scadenza e' quella che apre la finestra commerciale."""
    return cx.execute("""
        SELECT a.cig                              AS cig,
               c.cf_amministrazione_appaltante    AS cf,
               c.denominazione_amministrazione_appaltante AS ente,
               c.provincia                        AS provincia,
               c.oggetto_lotto                    AS oggetto,
               c.cod_cpv                          AS cpv,
               MIN(a.data_termine_contrattuale)   AS fine,
               c.importo_lotto                    AS importo
        FROM avvio_contratto a
        JOIN cig c ON c.cig = a.cig
        WHERE a.data_termine_contrattuale BETWEEN ? AND ?
          AND c.cf_amministrazione_appaltante IS NOT NULL
          AND trim(c.cf_amministrazione_appaltante) <> ''
        GROUP BY a.cig
        ORDER BY fine""", (da, a)).fetchall()


def misura(cx, da, a):
    cx.executescript(SCHEMA)
    print(f"contratti scaduti fra {da} e {a}...")
    righe = scaduti(cx, da, a)
    print(f"  {len(righe):,} CIG da esaminare\n")

    print("costruisco il vocabolario (IDF su tutti gli oggetti)...")
    voc = Vocabolario(cx)
    print(f"  {voc.n:,} oggetti, {len(voc.idf):,} parole distinte\n")

    ora = datetime.now().isoformat(timespec="seconds")
    conta = collections.Counter()
    fatte = 0
    cx.execute("DELETE FROM esito")

    for s in righe:
        s = dict(s)
        cf_old, nomi_old, mostra_old = fornitori(cx, s["cig"])
        s["cf_uscente"], s["nomi_uscente"] = cf_old, nomi_old
        trovato, _perche = cerca_seguito(cx, voc, s)
        r = classifica(cx, s, trovato)
        conta[r["esito"]] += 1

        cx.execute(
            "INSERT OR REPLACE INTO esito (cig, cf_ente, ente, provincia, "
            " oggetto, cod_cpv, data_termine, importo, cf_uscente, "
            " fornitore_uscente, cig_seguito, oggetto_seguito, "
            " data_pubblicazione_seguito, tipo_scelta_seguito, importo_seguito,"
            " cf_subentrante, fornitore_subentrante, esito, contendibile, "
            " punteggio, parole_comuni, calcolato_il) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (s["cig"], s["cf"], s["ente"], s["provincia"], s["oggetto"],
             s["cpv"], s["fine"], s["importo"],
             ";".join(sorted(cf_old)) or None, mostra_old or None,
             r.get("cig_seguito"), r.get("oggetto_seguito"),
             r.get("data_pubblicazione_seguito"), r.get("tipo_scelta_seguito"),
             r.get("importo_seguito"), r.get("cf_subentrante"),
             r.get("fornitore_subentrante"), r["esito"], r.get("contendibile"),
             r.get("punteggio"), r.get("parole_comuni"), ora))

        fatte += 1
        if fatte % 2000 == 0:
            cx.commit()
            print(f"  {fatte:,}/{len(righe):,}")
    cx.commit()
    print(f"\nscritte {fatte:,} righe in 'esito'.\n")
    return conta


# ------------------------------------------------------------------- stato
def eur(v):
    if v is None:
        return "—"
    if v >= 1e6:
        return f"{v / 1e6:.1f} M€"
    return f"{v / 1e3:.0f} k€"


def stato(cx):
    tot = cx.execute("SELECT count(*) FROM esito").fetchone()[0]
    if not tot:
        print("tabella 'esito' vuota. Prima: python esito.py --misura")
        return
    quando = cx.execute("SELECT max(calcolato_il) FROM esito").fetchone()[0]
    periodo = cx.execute("SELECT min(data_termine), max(data_termine) "
                         "FROM esito").fetchone()
    print(f"=== che fine hanno fatto {tot:,} contratti scaduti ===")
    print(f"    scadenze da {periodo[0]} a {periodo[1]} — "
          f"calcolato il {quando[:10]}\n")

    for e, n in cx.execute(
            "SELECT esito, count(*) c FROM esito GROUP BY 1 ORDER BY c DESC"):
        print(f"  {ESITI.get(e, e):48s} {n:>7,}  {n / tot * 100:5.1f}%")

    con_seguito = cx.execute(
        "SELECT count(*) FROM esito WHERE cig_seguito IS NOT NULL").fetchone()[0]
    if not con_seguito:
        print("\nnessun seguito trovato: non c'e' abbastanza storico.")
        return

    print(f"\n--- dei {con_seguito:,} con un seguito ---\n")
    for c, n in cx.execute(
            "SELECT contendibile, count(*) FROM esito "
            "WHERE cig_seguito IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"):
        et = {1: "andato a gara (contendibile)",
              0: "affidamento diretto (porta chiusa)"}.get(c, "procedura non dichiarata")
        print(f"  {et:48s} {n:>7,}  {n / con_seguito * 100:5.1f}%")

    solidi = cx.execute("SELECT count(*) FROM esito WHERE punteggio >= ?",
                        (SOLIDO,)).fetchone()[0]
    print(f"\n  match solidi (punteggio >= {SOLIDO}): {solidi:,} su "
          f"{con_seguito:,} ({solidi / con_seguito * 100:.0f}%)")

    # La riga che decide se il prodotto vale.
    aperti = cx.execute(
        "SELECT count(*) FROM esito "
        "WHERE esito = 'nuovo_fornitore' OR contendibile = 1").fetchone()[0]
    print(f"\n=== la risposta ===\n")
    print(f"  su {tot:,} scadenze, {aperti:,} ({aperti / tot * 100:.1f}%) sono "
          f"finite in una porta aperta:\n  cambio di fornitore, oppure una "
          f"procedura competitiva.")
    rinn = cx.execute("SELECT count(*) FROM esito WHERE esito='rinnovato' "
                      "AND contendibile = 0").fetchone()[0]
    print(f"\n  {rinn:,} ({rinn / tot * 100:.1f}%) sono state rinnovate allo "
          f"stesso fornitore per\n  affidamento diretto: quelle non erano "
          f"contendibili, e segnalarle e' stato\n  tempo speso male.")

    print("\n--- top fornitori uscenti sostituiti (dove si entra davvero) ---\n")
    for f, n in cx.execute(
            "SELECT fornitore_uscente, count(*) c FROM esito "
            "WHERE esito = 'nuovo_fornitore' AND fornitore_uscente IS NOT NULL "
            "GROUP BY 1 ORDER BY c DESC LIMIT 8"):
        print(f"  {n:>4}  {f[:66]}")

    print("\n--- per fascia di importo ---\n")
    for et, filtro in (("sotto 40 k€", "importo < 40000"),
                       ("40–140 k€", "importo >= 40000 AND importo < 140000"),
                       ("140 k€–1 M€", "importo >= 140000 AND importo < 1000000"),
                       ("oltre 1 M€", "importo >= 1000000")):
        r = cx.execute(
            f"SELECT count(*), sum(esito='nuovo_fornitore' OR contendibile=1) "
            f"FROM esito WHERE {filtro}").fetchone()
        if r[0]:
            print(f"  {et:14s} {r[0]:>7,} scadenze, "
                  f"{(r[1] or 0) / r[0] * 100:5.1f}% porta aperta")


def dubbi(cx, quanti=15):
    righe = cx.execute(
        "SELECT punteggio, cig, oggetto, oggetto_seguito, esito, parole_comuni "
        "FROM esito WHERE cig_seguito IS NOT NULL AND punteggio < ? "
        "ORDER BY punteggio LIMIT ?", (SOLIDO, quanti)).fetchall()
    if not righe:
        print("nessun match sotto la soglia di solidita'.")
        return
    print(f"=== i {len(righe)} match piu' deboli — da guardare a occhio ===\n")
    for p, cig, o1, o2, e, com in righe:
        print(f"  {p:.2f}  {cig}  -> {ESITI.get(e, e)}")
        print(f"        parole in comune: {com}")
        print(f"        PRIMA {(o1 or '')[:78]}")
        print(f"        DOPO  {(o2 or '')[:78]}\n")


def spiega(cx, cig):
    r = cx.execute("SELECT * FROM esito WHERE cig = ?", (cig,)).fetchone()
    if not r:
        print(f"{cig} non e' in 'esito'. O non e' scaduto, o non e' del "
              f"verticale, o serve --misura.")
        return
    d = dict(r)
    print(f"=== {cig} ===\n")
    print(f"  ente        {d['ente']} ({d['provincia']})")
    print(f"  oggetto     {(d['oggetto'] or '')[:80]}")
    print(f"  scaduto il  {d['data_termine']}   importo {eur(d['importo'])}")
    print(f"  uscente     {d['fornitore_uscente'] or '—'}")
    print(f"\n  esito       {ESITI.get(d['esito'], d['esito'])}")
    if not d["cig_seguito"]:
        print("\n  Nessun CIG dello stesso ente, nella finestra "
              f"-{PRIMA_MESI}/+{DOPO_MESI} mesi, somiglia\n  abbastanza a "
              "questo oggetto. Puo' voler dire che il servizio e' cessato,\n"
              "  che e' passato in convenzione Consip, o solo che l'ente non "
              "ha\n  ancora pubblicato.")
        return
    print(f"  seguito     {d['cig_seguito']}  pubblicato il "
          f"{d['data_pubblicazione_seguito']}")
    print(f"              {(d['oggetto_seguito'] or '')[:78]}")
    print(f"  procedura   {d['tipo_scelta_seguito'] or '—'}"
          f"   {'CONTENDIBILE' if d['contendibile'] == 1 else ''}")
    print(f"  vinto da    {d['fornitore_subentrante'] or 'non ancora noto'}")
    print(f"\n  punteggio   {d['punteggio']}  "
          f"({'solido' if (d['punteggio'] or 0) >= SOLIDO else 'debole, da verificare'})")
    print(f"  match sulle parole: {d['parole_comuni']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--misura", action="store_true")
    ap.add_argument("--da", default="2023-01-01",
                    help="prima data di scadenza da esaminare")
    ap.add_argument("--a", default=None,
                    help="ultima (default: 3 mesi fa, serve tempo perche' il "
                         "seguito venga pubblicato)")
    ap.add_argument("--stato", action="store_true")
    ap.add_argument("--dubbi", action="store_true")
    ap.add_argument("--cig")
    a = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit(f"{DB} non esiste. Prima: python ingest.py --init")
    cx = sqlite3.connect(DB)
    cx.row_factory = sqlite3.Row

    if a.misura:
        fine = a.a or cx.execute("SELECT date('now','-3 months')").fetchone()[0]
        misura(cx, a.da, fine)
        stato(cx)
    elif a.dubbi:
        dubbi(cx)
    elif a.cig:
        spiega(cx, a.cig.strip().upper())
    elif a.stato:
        stato(cx)
    else:
        ap.print_help()
    cx.close()


if __name__ == "__main__":
    main()
