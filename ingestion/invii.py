#!/usr/bin/env python3
"""
Tracciamento degli invii PEC (task R7).

Il problema che risolve: fra tre settimane, guardando la lista, non si
distingue "non hanno risposto" da "non l'ho mandata". Sono due conclusioni
opposte — la prima dice che il messaggio non funziona, la seconda che il
lavoro non e' finito — e senza tracciamento si confondono.

Si lavora sui numeri progressivi dei file generati da genera_pec.py, che
sono gli stessi dell'INDICE.md. Nessun ID da copiare.

Uso:
    python invii.py                          # stato del lotto 'pec'
    python invii.py --lotto pec-marche       # stato di un altro lotto
    python invii.py --inviata 1,2,3
    python invii.py --risposta 5 --note "chiedono presentazione"
    python invii.py --discovery-fissata 5
    python invii.py --offerta 5 --valore 18000
    python invii.py --vendita 5 --valore 16500
    python invii.py --persa 7 --motivo gia-fornitore
    python invii.py --funnel                 # il funnel del lotto
    python invii.py --funnel --tutti         # tutti i lotti insieme
    python invii.py --non-consegnata 9

Dipendenza: psycopg.
"""

import argparse
import sys
from datetime import date

import psycopg
from push_supabase import leggi_dsn, maschera

# Gli stati, in ordine di avanzamento. I nomi dopo "risposta" sono quelli
# degli stage GoHighLevel gia' in uso sui clienti: se un domani questo funnel
# passa nel CRM, l'import e' un copia-incolla invece che una mappatura.
STATI = {
    "da_inviare":         "da inviare",
    "inviata":            "inviata, in attesa",
    "risposta":           "HA RISPOSTO",
    "discovery_fissata":  "Discovery Call Fissata",
    "discovery_fatta":    "Discovery Call Fatta",
    "offerta":            "Offerta",
    "vendita":            "VENDITA",
    "persa":              "persa",
    "nessuna_risposta":   "nessuna risposta",
    "non_consegnata":     "consegna fallita",
    "chiusa":             "chiusa",
}

# stato -> colonna data. Le date servono per misurare quanto si resta fermi in
# uno stadio, che e' l'unico modo per capire dove il funnel perde.
DATE_STATO = {
    "inviata":           "inviata_il",
    "risposta":          "risposta_il",
    "discovery_fissata": "discovery_fissata_il",
    "discovery_fatta":   "discovery_fatta_il",
    "offerta":           "offerta_il",
    "vendita":           "vendita_il",
    "persa":             "persa_il",
}

# Gli stadi del funnel, in ordine, con la colonna che li marca. Cumulativi:
# chi e' arrivato a Vendita e' passato anche da Offerta.
FUNNEL = [
    ("destinatari",   None),
    ("inviate",       "inviata_il"),
    ("consegnate",    "consegnata_il"),
    ("risposte",      "risposta_il"),
    ("Discovery Call Fissata", "discovery_fissata_il"),
    ("Discovery Call Fatta",   "discovery_fatta_il"),
    ("Offerta",       "offerta_il"),
    ("Vendita",       "vendita_il"),
]

# Lista chiusa invece di testo libero: con il testo libero, fra sei mesi
# "gia' fornito" e "hanno gia' un fornitore" sono due righe diverse in un
# conteggio e il motivo piu' frequente non si vede.
MOTIVI = {
    "gia-fornitore": "hanno gia' un fornitore e lo tengono",
    "no-budget":     "niente budget / capitolo esaurito",
    "prezzo":        "prezzo fuori mercato",
    "requisiti":     "requisiti o referenze che non abbiamo",
    "tempi":         "tempi incompatibili",
    "silenzio":      "sparito dopo il primo contatto",
    "altro":         "altro",
}

# Dopo quanti giorni senza risposta si considera chiusa la partita.
# Tre settimane: sotto e' presto, sopra non torna piu' nessuno.
GIORNI_ATTESA = 21


def numeri(spec):
    """'1,3,5' o '1-4' -> [1,3,5] / [1,2,3,4]"""
    out = []
    for pezzo in spec.split(","):
        pezzo = pezzo.strip()
        if "-" in pezzo:
            a, b = pezzo.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif pezzo:
            out.append(int(pezzo))
    return out


def aggiorna(cur, lotto, progressivi, stato, note, data_campo=None,
             motivo=None, valore=None):
    campi = ["stato = %s"]
    par = [stato]
    if data_campo:
        campi.append(f"{data_campo} = %s")
        par.append(date.today())
    if motivo:
        campi.append("motivo_perdita = %s")
        par.append(motivo)
    if valore is not None:
        # L'importo va nella colonna dello stadio raggiunto: quello offerto e
        # quello vinto sono numeri diversi e confonderli falserebbe il tasso
        # di conversione a valore.
        campi.append("valore_vendita = %s" if stato == "vendita"
                     else "valore_offerta = %s")
        par.append(valore)
    if note:
        campi.append("note = %s")
        par.append(note)
    par += [lotto, progressivi]
    cur.execute(
        f"UPDATE radar.invio SET {', '.join(campi)} "
        f"WHERE lotto = %s AND progressivo = ANY(%s) "
        f"RETURNING progressivo, ente", par)
    return cur.fetchall()


def eur(v):
    if not v:
        return "—"
    return f"{v / 1000:,.0f} k€" if v >= 1000 else f"{v:,.0f} €"


def funnel(cur, lotto=None):
    """Il funnel completo. Risponde a 'il canale e' redditizio?', che il solo
    tasso di risposta non poteva dire."""
    dove = "WHERE lotto = %s" if lotto else ""
    par = (lotto,) if lotto else ()
    sel = ", ".join(
        "count(*)" if col is None
        else f"count(*) FILTER (WHERE {col} IS NOT NULL)"
        for _, col in FUNNEL)
    cur.execute(
        f"SELECT {sel}, "
        f"       count(*) FILTER (WHERE persa_il IS NOT NULL), "
        f"       sum(valore_offerta) FILTER (WHERE offerta_il IS NOT NULL), "
        f"       sum(valore_vendita) FILTER (WHERE vendita_il IS NOT NULL) "
        f"FROM radar.invio {dove}", par)
    r = cur.fetchone()
    n = list(r[:len(FUNNEL)])
    perse, offerto, vinto = r[len(FUNNEL):]

    titolo = f"lotto '{lotto}'" if lotto else "tutti i lotti"
    print(f"\n=== funnel — {titolo} ===\n")
    partiti = n[1] or 0
    for i, (etichetta, _) in enumerate(FUNNEL):
        v = n[i]
        # Due percentuali: sul totale (dove sono finiti) e sullo stadio
        # precedente (dove si perde). La seconda e' quella che indica il buco.
        su_tot = f"{v / n[0] * 100:5.1f}% del totale" if n[0] else ""
        prec = n[i - 1] if i else 0
        passo = f"  ({v / prec * 100:.0f}% del passo prima)" if i and prec else ""
        barra = "#" * int(v / max(n[0], 1) * 26)
        print(f"  {etichetta:24s} {v:>5,}  {su_tot}{passo}")
        if barra:
            print(f"  {'':24s} {barra}")
    if perse:
        print(f"\n  perse                    {perse:>5,}")

    if not partiti:
        print("\n  nessuna PEC ancora partita: il funnel e' vuoto per ora.")
        return
    risposte = n[3] or 0
    vendite = n[7] or 0
    print(f"\n  tasso di risposta   {risposte}/{partiti} = "
          f"{risposte / partiti * 100:.1f}%")
    print(f"  tasso di chiusura   {vendite}/{partiti} = "
          f"{vendite / partiti * 100:.1f}%")
    if offerto:
        print(f"\n  offerto  {eur(float(offerto))}")
    if vinto:
        print(f"  vinto    {eur(float(vinto))}")
        print(f"  valore medio per PEC inviata: "
              f"{eur(float(vinto) / partiti)}")
    elif risposte:
        print("\n  nessuna vendita ancora: il canale risponde ma non ha "
              "ancora prodotto\n  fatturato. E' presto per dire se e' "
              "redditizio.")

    cur.execute(
        f"SELECT coalesce(motivo_perdita,'non registrato'), count(*) "
        f"FROM radar.invio {('WHERE lotto = %s AND' if lotto else 'WHERE')} "
        f"persa_il IS NOT NULL GROUP BY 1 ORDER BY 2 DESC", par)
    righe = cur.fetchall()
    if righe:
        print("\n  perche' si perde:")
        for m, c in righe:
            print(f"    {c:>3}  {MOTIVI.get(m, m)}")


def stato(cur, lotto):
    cur.execute(
        "SELECT progressivo, ente, provincia, pec, n_contratti, stato, "
        "       inviata_il, risposta_il, note, "
        "       consegnata_il, errore_consegna, sollecitata_il "
        "FROM radar.invio WHERE lotto = %s ORDER BY progressivo", (lotto,))
    righe = cur.fetchall()
    if not righe:
        print(f"lotto '{lotto}' non registrato. Genera le PEC con "
              f"genera_pec.py --cartella {lotto}")
        return

    print(f"=== lotto '{lotto}' — {len(righe)} destinatari ===\n")
    oggi = date.today()
    conteggi = {}
    senza_conferma = 0
    for (p, ente, prov, pec, n, st, inv, risp, note,
         consegnata, errore, sollecitata) in righe:
        conteggi[st] = conteggi.get(st, 0) + 1
        marca = {"risposta": "***", "da_inviare": " . ",
                 "non_consegnata": " ! "}.get(st, "   ")
        coda = ""
        if st == "inviata" and inv:
            gg = (oggi - inv).days
            # L'esito tecnico prima dell'attesa: una PEC mai consegnata non e'
            # "in attesa di risposta", e' un recapito da correggere.
            if consegnata:
                esito = "consegnata"
            elif errore:
                esito = errore
            else:
                esito = "consegna non confermata"
                senza_conferma += 1
            coda = f"  ({gg}gg fa, {esito}"
            if sollecitata:
                coda += f", sollecitata il {sollecitata:%d/%m}"
            elif gg > GIORNI_ATTESA:
                coda += ", da chiudere"
            coda += ")"
        elif st == "risposta" and risp:
            coda = f"  (risposta il {risp:%d/%m})"
        print(f" {marca}{p:2d}. {(ente or '?')[:44]:46s} {(prov or '')[:11]:12s} "
              f"{STATI.get(st, st):18s}{coda}")
        if note:
            print(f"        nota: {note[:70]}")

    if senza_conferma:
        print(f"\n  {senza_conferma} senza conferma di consegna — "
              f"`python pec_imap.py --leggi` legge le ricevute.")

    print()
    for st, n in sorted(conteggi.items(), key=lambda x: -x[1]):
        print(f"  {STATI.get(st, st):20s} {n:3d}")

    inviate = sum(n for s, n in conteggi.items()
                  if s in ("inviata", "risposta", "nessuna_risposta", "chiusa"))
    risposte = conteggi.get("risposta", 0)
    if inviate:
        print(f"\n  tasso di risposta: {risposte}/{inviate} = "
              f"{risposte/inviate*100:.0f}%")
        if inviate >= 10:
            if risposte == 0:
                print("  -> zero risposte su un campione decente: il canale "
                      "PEC-protocollo\n     non arriva a un decisore. Vale la "
                      "pena cercare il RUP (R1.4).")
            elif risposte <= 2:
                print("  -> normale per un primo contatto a freddo: il canale "
                      "regge.")
            else:
                print("  -> segnale forte, conviene industrializzare l'invio.")
    else:
        print("\n  nessuna ancora inviata.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lotto", default="pec")
    ap.add_argument("--inviata", metavar="N[,N|N-N]")
    ap.add_argument("--risposta", metavar="N[,N]")
    ap.add_argument("--discovery-fissata", metavar="N[,N]",
                    help="Discovery Call Fissata")
    ap.add_argument("--discovery-fatta", metavar="N[,N]",
                    help="Discovery Call Fatta")
    ap.add_argument("--offerta", metavar="N[,N]")
    ap.add_argument("--vendita", metavar="N[,N]")
    ap.add_argument("--persa", metavar="N[,N]")
    ap.add_argument("--motivo", choices=sorted(MOTIVI),
                    help="con --persa: perche'")
    ap.add_argument("--valore", type=float,
                    help="importo dell'offerta o della vendita, in euro")
    ap.add_argument("--funnel", action="store_true",
                    help="il funnel completo, dai destinatari al fatturato")
    ap.add_argument("--tutti", action="store_true",
                    help="con --funnel: tutti i lotti insieme")
    ap.add_argument("--non-consegnata", metavar="N[,N]")
    ap.add_argument("--chiusa", metavar="N[,N]")
    ap.add_argument("--scadute", action="store_true",
                    help=f"segna 'nessuna risposta' le inviate da oltre "
                         f"{GIORNI_ATTESA} giorni")
    ap.add_argument("--note")
    a = ap.parse_args()

    dsn = leggi_dsn()
    try:
        with psycopg.connect(dsn) as pg, pg.cursor() as cur:
            fatto = False
            for spec, st in (
                    (a.inviata, "inviata"),
                    (a.risposta, "risposta"),
                    (a.discovery_fissata, "discovery_fissata"),
                    (a.discovery_fatta, "discovery_fatta"),
                    (a.offerta, "offerta"),
                    (a.vendita, "vendita"),
                    (a.persa, "persa"),
                    (a.non_consegnata, "non_consegnata"),
                    (a.chiusa, "chiusa")):
                if spec:
                    if st == "persa" and not a.motivo:
                        sys.exit("--persa vuole anche --motivo: "
                                 + ", ".join(sorted(MOTIVI)))
                    tocc = aggiorna(cur, a.lotto, numeri(spec), st, a.note,
                                    DATE_STATO.get(st), a.motivo, a.valore)
                    for p, ente in tocc:
                        print(f"  {p:2d}. {(ente or '?')[:48]:50s} -> {STATI[st]}")
                    if not tocc:
                        print(f"  nessun destinatario con quei numeri nel "
                              f"lotto '{a.lotto}'")
                    fatto = True

            if a.scadute:
                cur.execute(
                    "UPDATE radar.invio SET stato = 'nessuna_risposta' "
                    "WHERE lotto = %s AND stato = 'inviata' "
                    "  AND inviata_il < current_date - %s "
                    "RETURNING progressivo", (a.lotto, GIORNI_ATTESA))
                n = len(cur.fetchall())
                print(f"  {n} invii oltre i {GIORNI_ATTESA} giorni segnati "
                      f"'nessuna risposta'")
                fatto = True

            if fatto:
                pg.commit()
                print()
            if a.funnel:
                funnel(cur, None if a.tutti else a.lotto)
            else:
                stato(cur, a.lotto)
    except Exception as e:
        sys.exit(maschera(f"{type(e).__name__}: {e}", dsn))


if __name__ == "__main__":
    main()
