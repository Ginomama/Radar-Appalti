"""Piano di copertura territoriale a rotazione (task R27).

IL PROBLEMA. Finora due lotti scelti a mano — 'pec' e 'pec-marche'. Va bene
per provare, non per lavorare: senza un ordine si finisce a rifare le
province comode, a saltare quelle grandi perche' "poi le faccio con calma", e
a non sapere mai quanto manca. Un piano scritto trasforma "ho mandato due
lotti" in un processo che si puo' misurare.

COME SI ORDINANO LE PROVINCE, e perche' non per numero di lead. La provincia
con piu' scadenze e' quasi sempre quella con gli enti piu' grandi, che sono
anche quelli con i fornitori piu' radicati: tanti lead e nessuna possibilita'
di sostituire nessuno. L'ordine giusto tiene insieme tre cose:

  quanti lead ci sono          il volume: sotto una certa soglia non vale
                               il giro
  quanto sono buoni            il punteggio di R18, che gia' misura la
                               probabilita' che si apra una porta
  quanto e' piccolo l'uscente  un fornitore con due contratti in tutta Italia
                               si sostituisce; uno con duecento no, per
                               quanto la scadenza sia vicina

Il terzo e' quello che nessuno guarda mai ed e' il piu' predittivo, perche'
distingue un mercato contendibile da uno presidiato.

IL RITMO. Il tetto vero non e' la voglia di lavorare: e' PEC_MAX_GIORNO (20 al
giorno, per non farsi marcare come spam dal gestore) e il fatto che una
risposta va lavorata a mano. 18 enti a settimana e' il numero che regge senza
accumulare arretrato.

Uso:
    python territorio.py                  # il piano, regione per regione
    python territorio.py --province       # il dettaglio per provincia
    python territorio.py --prossimo       # il comando da lanciare adesso
    python territorio.py --settimane 12   # quanto lontano guardare

Dipendenza: psycopg.
"""

import argparse
import sys
from collections import defaultdict

import psycopg

from push_supabase import leggi_dsn, maschera

# Il ritmo. Non e' un'ambizione: e' quanto se ne riesce a seguire.
PER_SETTIMANA = 18

# Sotto questa soglia una provincia non vale un lotto a se': si accorpa alla
# regione, se no si mandano tre PEC e si perde mezza giornata a prepararle.
MIN_ENTI = 4

# Un uscente con piu' di tanti contratti non lo si sostituisce con una PEC.
# La soglia viene da R18: sopra i 200 contratti il moltiplicatore scende a
# 0,70, sotto i 3 sale a 1,27.
USCENTE_PICCOLO = 8

# Le categorie che sappiamo servire. Stessa lista di genera_pec: se qui fosse
# piu' larga, il piano prometterebbe lead che poi il generatore scarta.
RILEVANTI = ("Sviluppo software", "Dati e analytics", "Gestione documentale",
             "Manutenzione e assistenza", "Consulenza IT")

REGIONI = {
    "ANCONA": "Marche", "ASCOLI PICENO": "Marche", "FERMO": "Marche",
    "MACERATA": "Marche", "PESARO E URBINO": "Marche",
}   # il resto si ricava dal database, questa e' solo la mappa di riserva


def dati(cur, giorni_max):
    """Una riga per provincia, con quello che serve a decidere."""
    cur.execute("""
        WITH base AS (
            SELECT s.provincia, s.cf_ente, s.ente, s.pec, s.punteggio,
                   s.cf_fornitore_uscente,
                   (SELECT count(*) FROM radar.scadenze x
                     WHERE x.cf_fornitore_uscente = s.cf_fornitore_uscente) AS peso_uscente
            FROM radar.v_scadenze s
            WHERE s.fornitore_persona_fisica = 0
              AND s.tipologia_amm = 'Pubbliche Amministrazioni'
              AND s.pec IS NOT NULL
              AND s.categoria = ANY(%s)
              AND s.giorni_alla_scadenza BETWEEN 20 AND %s
        )
        SELECT provincia,
               count(DISTINCT cf_ente)                        AS enti,
               count(*)                                       AS lead,
               round(avg(punteggio)::numeric, 1)              AS punti_medi,
               count(*) FILTER (WHERE peso_uscente <= %s)     AS uscenti_piccoli,
               count(DISTINCT cf_ente) FILTER (
                   WHERE lower(pec) IN (SELECT lower(pec) FROM radar.invio)
               )                                              AS gia_fatti,
               min(cf_ente)                                   AS campione
        FROM base
        WHERE provincia IS NOT NULL
        GROUP BY provincia
        ORDER BY 2 DESC
    """, (list(RILEVANTI), giorni_max, USCENTE_PICCOLO))
    righe = []
    for prov, enti, lead, punti, piccoli, fatti, campione in cur.fetchall():
        rimasti = enti - (fatti or 0)
        if rimasti <= 0:
            continue
        q_piccoli = (piccoli or 0) / max(lead, 1)
        # Il valore della provincia: quanti enti restano, pesati per quanto
        # sono buoni i lead e per quanto e' sostituibile chi c'e' adesso.
        # Il volume entra con radice, non lineare: una provincia col doppio
        # degli enti non vale il doppio, vale la radice di due — se no le
        # metropoli monopolizzerebbero il piano.
        valore = (rimasti ** 0.5) * (float(punti or 0) / 50) * (0.4 + q_piccoli)
        righe.append(dict(prov=prov, enti=enti, rimasti=rimasti, lead=lead,
                          punti=float(punti or 0), q_piccoli=q_piccoli,
                          fatti=fatti or 0, valore=valore,
                          campione=campione))
    righe.sort(key=lambda x: -x["valore"])
    return righe


def regione_di(righe):
    """provincia -> regione.

    La regione sta in SQLite (su Supabase vanno solo i derivati), ma le due
    fonti scrivono la provincia in modi diversi: ANAC per esteso ("ROMA"),
    IndicePA con la sigla ("RM"). Unirle sul nome non funziona — e non da'
    errore, restituisce semplicemente zero corrispondenze, che e' il modo
    peggiore di sbagliare.

    Quindi si passa dal codice fiscale, che e' la stessa chiave di sempre:
    per ogni provincia la query ha gia' portato un CF di esempio.
    """
    import os
    import sqlite3
    db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "radar.db")
    if not os.path.exists(db):
        return {}
    campioni = {r["campione"]: r["prov"] for r in righe if r.get("campione")}
    if not campioni:
        return {}
    try:
        cx = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        segni = ",".join("?" * len(campioni))
        m = {}
        for cf, reg in cx.execute(
                f"SELECT cf_ente, regione FROM ente_contatti "
                f"WHERE cf_ente IN ({segni}) AND regione IS NOT NULL",
                list(campioni)):
            m[campioni[cf]] = reg
        cx.close()
        return m
    except sqlite3.Error:
        return {}


def piano(righe, mappa, settimane):
    """Le province in ordine, spezzate in settimane da PER_SETTIMANA enti."""
    calendario, sett, quanti = [], [], 0
    for r in righe:
        resto = r["rimasti"]
        while resto > 0 and len(calendario) < settimane:
            spazio = PER_SETTIMANA - quanti
            preso = min(resto, spazio)
            sett.append((r["prov"], preso))
            quanti += preso
            resto -= preso
            if quanti >= PER_SETTIMANA:
                calendario.append(sett)
                sett, quanti = [], 0
    if sett and len(calendario) < settimane:
        calendario.append(sett)
    return calendario


def stampa_province(righe, mappa):
    print("\n  provincia            enti  gia'  restano  punti  uscenti  valore")
    print("                                fatti          medi  piccoli")
    for r in righe[:30]:
        print(f"  {r['prov'][:20]:20s} {r['enti']:>5} {r['fatti']:>5} "
              f"{r['rimasti']:>8} {r['punti']:>6.1f} "
              f"{r['q_piccoli']*100:>7.0f}% {r['valore']:>7.2f}")
    print(f"\n  'uscenti piccoli' = quota di lead il cui fornitore attuale ha")
    print(f"  al massimo {USCENTE_PICCOLO} contratti in tutta Italia: e' li' che")
    print(f"  si puo' davvero sostituire qualcuno.\n")


def stampa_piano(calendario, mappa, righe):
    per_prov = {r["prov"]: r for r in righe}
    tot_enti = sum(r["rimasti"] for r in righe)
    print(f"\n=== piano di copertura — {PER_SETTIMANA} enti a settimana ===\n")
    print(f"  da coprire: {tot_enti:,} enti in {len(righe)} province")
    print(f"  a questo ritmo servono {tot_enti // PER_SETTIMANA + 1} settimane\n")
    for i, sett in enumerate(calendario, 1):
        voci = []
        for prov, n in sett:
            reg = mappa.get(prov, "")
            voci.append(f"{prov.title()} ({n})" + (f" · {reg}" if reg else ""))
        n_tot = sum(n for _, n in sett)
        print(f"  settimana {i:>2}  [{n_tot:>2} enti]  " + ",  ".join(voci))
    print()


def prossimo(righe, mappa):
    """Il comando esatto da lanciare adesso. Senza questo il piano resta una
    tabella che nessuno traduce in azione."""
    if not righe:
        print("\n  niente da coprire: tutti gli enti servibili sono stati contattati.\n")
        return
    scelte, quanti = [], 0
    for r in righe:
        if quanti >= PER_SETTIMANA:
            break
        scelte.append(r)
        quanti += r["rimasti"]
    province = ",".join(s["prov"] for s in scelte)
    reg = {mappa.get(s["prov"], "?") for s in scelte}
    lotto = "pec-" + ("-".join(sorted(reg))[:24].lower().replace(" ", "-")
                      if reg != {"?"} else scelte[0]["prov"][:12].lower())
    print(f"\n=== il prossimo lotto ===\n")
    for s in scelte:
        print(f"  {s['prov'].title():22s} {s['rimasti']:>3} enti da fare, "
              f"punti medi {s['punti']:.0f}, "
              f"{s['q_piccoli']*100:.0f}% con uscente piccolo")
    print(f"\n  python genera_pec.py --cartella {lotto} \\")
    print(f"      --provincia \"{province}\" \\")
    print(f"      --limite {PER_SETTIMANA} --giorni-min 30 --giorni-max 365")
    print(f"\n  Poi si inviano dalla console, o con:")
    print(f"  python pec_smtp.py --lotto {lotto} --invia 1-{PER_SETTIMANA}")
    print(f"\n  L'anti-duplicato di R20 salta da solo gli enti gia' contattati")
    print(f"  in un altro lotto: non serve tenerne conto a mano.\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--province", action="store_true",
                    help="il dettaglio per provincia")
    ap.add_argument("--prossimo", action="store_true",
                    help="il comando per il lotto successivo")
    ap.add_argument("--settimane", type=int, default=10)
    ap.add_argument("--giorni-max", type=int, default=365)
    a = ap.parse_args()

    dsn = leggi_dsn()
    if not dsn:
        sys.exit("manca la stringa di connessione (ingestion/.env.local)")
    try:
        with psycopg.connect(dsn, connect_timeout=30) as pg, pg.cursor() as cur:
            righe = dati(cur, a.giorni_max)
            mappa = regione_di(righe)
    except Exception as e:
        sys.exit("errore: " + maschera(str(e), dsn)[:300])

    grosse = [r for r in righe if r["rimasti"] >= MIN_ENTI]
    if a.prossimo:
        prossimo(grosse, mappa)
    elif a.province:
        stampa_province(righe, mappa)
    else:
        stampa_piano(piano(grosse, mappa, a.settimane), mappa, grosse)
        piccole = len(righe) - len(grosse)
        if piccole:
            print(f"  {piccole} province con meno di {MIN_ENTI} enti non sono in")
            print(f"  calendario: si accodano a una regione vicina quando si passa.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
