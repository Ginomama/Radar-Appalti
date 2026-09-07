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
    python invii.py --chiusa 7 --note "gia' rinnovato"
    python invii.py --non-consegnata 9

Dipendenza: psycopg.
"""

import argparse
import sys
from datetime import date

import psycopg
from push_supabase import leggi_dsn, maschera

STATI = {
    "da_inviare":       "da inviare",
    "inviata":          "inviata, in attesa",
    "risposta":         "HA RISPOSTO",
    "nessuna_risposta": "nessuna risposta",
    "non_consegnata":   "consegna fallita",
    "chiusa":           "chiusa",
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


def aggiorna(cur, lotto, progressivi, stato, note, data_campo=None):
    campi = ["stato = %s"]
    par = [stato]
    if data_campo:
        campi.append(f"{data_campo} = %s")
        par.append(date.today())
    if note:
        campi.append("note = %s")
        par.append(note)
    par += [lotto, progressivi]
    cur.execute(
        f"UPDATE radar.invio SET {', '.join(campi)} "
        f"WHERE lotto = %s AND progressivo = ANY(%s) "
        f"RETURNING progressivo, ente", par)
    return cur.fetchall()


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
            for spec, st, campo in (
                    (a.inviata, "inviata", "inviata_il"),
                    (a.risposta, "risposta", "risposta_il"),
                    (a.non_consegnata, "non_consegnata", None),
                    (a.chiusa, "chiusa", None)):
                if spec:
                    tocc = aggiorna(cur, a.lotto, numeri(spec), st, a.note, campo)
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
            stato(cur, a.lotto)
    except Exception as e:
        sys.exit(maschera(f"{type(e).__name__}: {e}", dsn))


if __name__ == "__main__":
    main()
