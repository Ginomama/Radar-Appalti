#!/usr/bin/env python3
"""
Genera le PEC di outreach gia' compilate (task R7).

Un file .txt per ente, pronto da incollare nel client PEC. Lo script esiste
invece di scrivere le email a mano perche' i lotti successivi si generano
rilanciandolo: cambia il filtro, non il lavoro.

Firma a nome della persona: la PEC parte da un domicilio digitale personale.
La P.IVA resta obbligatoria comunque — senza, l'ufficio non ha un operatore
economico da inserire in elenco.

Uso:
    python genera_pec.py                      # 20 destinatari, in docs/pec/
    python genera_pec.py --limite 40
    python genera_pec.py --provincia PD,VE,TV

Dipendenza: psycopg.
"""

import argparse
import os
import re
import unicodedata
from datetime import datetime

import psycopg
from push_supabase import leggi_dsn, maschera

QUI = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(QUI, "..", "docs", "pec")

MITTENTE = {
    "nome": "Leonardo Foschi",
    "insegna": "FlowLine",          # nome commerciale; togliere se non usato
    "piva": "IT00000000000",
    "telefono": "000 0000000",
    "email": "contatto@esempio.it",
}

# Categorie che FlowLine sa davvero servire. Fuori licenze (rivendita),
# connettivita' (telco) e datacenter (infrastruttura).
RILEVANTI = ("Sviluppo software", "Dati e analytics", "Gestione documentale",
             "Manutenzione e assistenza", "Consulenza IT")

# Paragrafo "cosa facciamo", uno per categoria.
COSA_FACCIAMO = {
    "Dati e analytics":
        "Costruiamo flussi che raccolgono dati da fonti diverse, li normalizzano\n"
        "e li rendono consultabili senza estrazioni manuali ricorrenti.",
    "Manutenzione e assistenza":
        "Affianchiamo i gestionali gia' in uso automatizzando le attivita'\n"
        "ripetitive che oggi richiedono intervento manuale: caricamenti,\n"
        "riconciliazioni, notifiche e controlli di completezza.",
    "Sviluppo software":
        "Sviluppiamo applicativi su misura e integrazioni fra sistemi esistenti,\n"
        "consegnando codice e documentazione all'ente.",
    "Gestione documentale":
        "Automatizziamo i passaggi fra protocollo, conservazione e gestionali:\n"
        "acquisizione, smistamento, notifiche e controlli di completezza.",
    "Consulenza IT":
        "Analizziamo i processi esistenti e individuiamo dove l'automazione riduce\n"
        "tempi ed errori, con una stima delle ore recuperate prima di qualsiasi\n"
        "sviluppo.",
}
GENERICO = ("Costruiamo automazioni che collegano fra loro i sistemi gia' in uso —\n"
            "gestionali, protocollo, banche dati, posta — eliminando i passaggi\n"
            "manuali ripetitivi fra un applicativo e l'altro.")

MESI = ["", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
        "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]

# ANAC usa valori segnaposto al posto del nome del fornitore quando il record
# non si risolve. "IMPRESA INESISTENTE" compare 21 volte fra le scadenze.
# Scriverlo in una PEC sarebbe imbarazzante: si filtra, e se non resta nessun
# nome si omette del tutto la frase sull'affidatario.
SEGNAPOSTO = ("IMPRESA INESISTENTE", "NON PRESENTE IN ANAGRAFE",
              "NON DISPONIBILE", "NON INDICATO")


def fornitori_puliti(grezzo):
    """Toglie i segnaposto ANAC. Ritorna None se non resta niente di dicibile."""
    if not grezzo:
        return None
    nomi = [n.strip() for n in grezzo.split(",") if n.strip()]
    veri = [n for n in nomi
            if not any(s in n.upper() for s in SEGNAPOSTO)]
    return ", ".join(veri)[:90] if veri else None


def data_it(d):
    return f"{d.day} {MESI[d.month]} {d.year}" if d else "data non indicata"


def slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:52]


def euro(v):
    return f"{v:,.0f}".replace(",", ".") if v else "n.d."


# Dopo quanti mesi un ente gia' contattato torna disponibile. Sotto e'
# insistenza sullo stesso protocollo, sopra e' un contatto nuovo e legittimo.
MESI_SILENZIO = 6


def gia_contattati(cur, lotto_corrente):
    """Le PEC gia' presenti in un ALTRO lotto, con lotto e stato.

    La chiave e' la casella, non l'ente: e' li' che arriva il messaggio, e due
    lotti diversi possono chiamare lo stesso ente con nomi diversi. Il lotto
    corrente si esclude perche' rigenerarlo deve poter riscrivere le sue righe.
    """
    cur.execute("""
        SELECT lower(pec), lotto, stato, inviata_il
        FROM radar.invio WHERE lotto <> %s""", (lotto_corrente,))
    return {p: (lot, st, quando) for p, lot, st, quando in cur.fetchall()}


def destinatari(cur, limite, province, gg_min, gg_max, imp_min, imp_max,
                escludi=None):
    # Si legge da v_scadenze e non da v_lead_90gg: su un territorio ristretto
    # la finestra dei 90 giorni lascia troppo poco (nelle Marche 5 lead contro
    # 116 su 12 mesi), e per un comune scrivere con sei mesi di anticipo va
    # benissimo — anzi meglio, la decisione sul rinnovo non e' ancora presa.
    q = """
        SELECT ente, provincia, pec, categoria, cig,
               data_termine_contrattuale, giorni_alla_scadenza,
               importo_aggiudicazione, fornitore_uscente, oggetto_lotto
        FROM radar.v_scadenze
        WHERE fornitore_persona_fisica = 0
          AND tipologia_amm = 'Pubbliche Amministrazioni'
          AND pec IS NOT NULL
          AND giorni_alla_scadenza BETWEEN %s AND %s
          AND importo_aggiudicazione BETWEEN %s AND %s
          AND categoria = ANY(%s)
    """
    par = [gg_min, gg_max, imp_min, imp_max, list(RILEVANTI)]
    if province:
        q += " AND provincia = ANY(%s)"
        par.append(province)
    q += " ORDER BY importo_aggiudicazione DESC NULLS LAST"
    cur.execute(q, par)

    # Si scrive a un ENTE, non a un contratto: piu' contratti dello stesso
    # ente finiscono in una PEC sola, ed e' anzi l'argomento migliore.
    per_ente = {}
    for r in cur.fetchall():
        per_ente.setdefault(r[2], []).append(r)

    # Un ente gia' presente in un altro lotto riceverebbe due PEC diverse dalla
    # stessa persona: non c'era nulla che lo impedisse, e finora non e'
    # successo per fortuna, non per costruzione.
    saltati = []
    if escludi:
        for pec in list(per_ente):
            voce = escludi.get((pec or "").lower())
            if voce:
                saltati.append((per_ente[pec][0][0], pec, voce))
                del per_ente[pec]

    ordinati = sorted(per_ente.values(),
                      key=lambda v: (-len(v), -sum(x[7] or 0 for x in v)))
    return ordinati[:limite], saltati


def componi(contratti):
    ente, pec = contratti[0][0], contratti[0][2]
    # La provincia piu' frequente del gruppo, non quella del contratto piu'
    # grosso: un ente regionale ha contratti sparsi su piu' province, e
    # prendere il primo dava "COMUNE DI FERMO - ANCONA".
    province = [c[1] for c in contratti if c[1]]
    prov = max(set(province), key=province.count) if province else None
    n = len(contratti)
    categorie = sorted({c[3] for c in contratti if c[3]})
    prima = min(c[5] for c in contratti if c[5])

    if n == 1:
        c = contratti[0]
        oggetto = (f"Richiesta iscrizione elenco operatori economici — "
                   f"servizi informatici (CIG {c[4]}, scadenza {data_it(c[5])})")
        forn = fornitori_puliti(c[8])
        affido = f",\nattualmente affidato a {forn}" if forn else ""
        rilievo = (
            f"La richiesta nasce da una verifica sui dati aperti ANAC, da cui\n"
            f"risulta in scadenza il {data_it(c[5])} il contratto CIG {c[4]},\n"
            f"relativo a \"{(c[9] or '').strip()[:150]}\"{affido}.")
    else:
        oggetto = (f"Richiesta iscrizione elenco operatori economici — "
                   f"servizi informatici ({n} contratti in scadenza)")
        voci = []
        for c in sorted(contratti, key=lambda x: x[5]):
            forn = fornitori_puliti(c[8])
            voci.append(f"  - CIG {c[4]}, scadenza {data_it(c[5])}"
                        + (f", affidatario {forn[:52]}" if forn else ""))
        rilievo = (
            f"La richiesta nasce da una verifica sui dati aperti ANAC, da cui\n"
            f"risultano in scadenza nei prossimi mesi {n} contratti nell'area dei\n"
            f"servizi informativi:\n\n" + "\n".join(voci))

    # elenco leggibile: "a", "b" e "c" — non "a" e "b" e "c"
    voci = [f'"{c.lower()}"' for c in categorie] or ['"servizi informatici"']
    cat_txt = voci[0] if len(voci) == 1 else ", ".join(voci[:-1]) + " e " + voci[-1]
    cosa = COSA_FACCIAMO.get(categorie[0], GENERICO) if categorie else GENERICO
    insegna = (f"\nche opera con il nome commerciale {MITTENTE['insegna']},"
               if MITTENTE.get("insegna") else "")

    corpo = f"""Spett.le {ente}

il sottoscritto {MITTENTE['nome']},{insegna}
operatore economico attivo nei servizi di automazione dei processi e
integrazione di sistemi informativi, chiede di essere inserito fra gli
operatori economici da consultare per le categorie {cat_txt}.

{rilievo}

Cosa facciamo, in concreto:
{cosa}

Non vendiamo licenze ne' abbonamenti: il risultato resta di proprieta'
dell'ente, che puo' farlo mantenere anche da altri fornitori.

Se ritenete utile un approfondimento, possiamo trasmettere una presentazione
delle competenze e delle referenze, oppure una proposta tecnica senza impegno
sull'ambito sopra indicato.

Il recapito PEC e' stato reperito dall'Indice dei domicili digitali della
pubblica amministrazione (IndicePA); i dati sui contratti provengono dagli
open data ANAC (licenza CC BY-SA 4.0). Restiamo a disposizione per ogni
chiarimento e per l'eventuale cancellazione dai nostri contatti.

Cordiali saluti

{MITTENTE['nome']}
P.IVA {MITTENTE['piva']}
tel. {MITTENTE['telefono']} — {MITTENTE['email']}
"""
    return pec, oggetto, corpo, ente, prov, n, prima


# -------------------------------------------------------------- solleciti
# Dopo quanti giorni dalla consegna si richiama. Dodici: sotto e' fretta —
# una PEC entra in protocollo e viene smistata in qualche giorno — sopra si
# esce dalla memoria di chi l'ha letta.
GIORNI_SOLLECITO = 12


def da_sollecitare(cur, lotto, giorni):
    """Chi ha ricevuto davvero, non ha risposto, e non e' gia' stato richiamato.

    Il filtro su consegnata_il e' il punto: sollecitare una PEC che non e' mai
    arrivata e' rumore, e finche' non gira pec_imap.py quella colonna resta
    vuota. Meglio zero solleciti che solleciti alla cieca.
    """
    cur.execute("""
        SELECT progressivo, ente, pec, cig_inclusi, inviata_il, consegnata_il
        FROM radar.invio
        WHERE lotto = %s
          AND stato = 'inviata'
          AND consegnata_il IS NOT NULL
          AND sollecitata_il IS NULL
          AND inviata_il <= current_date - %s
        ORDER BY progressivo""", (lotto, giorni))
    return cur.fetchall()


def componi_sollecito(ente, pec, cig_inclusi, inviata_il):
    """Corto di proposito. Un secondo messaggio lungo quanto il primo si legge
    come un rinvio automatico; questo deve leggersi in venti secondi."""
    cigs = [c.strip() for c in (cig_inclusi or "").split(",") if c.strip()]
    rif = (f"CIG {cigs[0]}" if len(cigs) == 1
           else f"{len(cigs)} contratti (CIG {', '.join(cigs[:3])}"
                + (", …)" if len(cigs) > 3 else ")"))

    oggetto = (f"Sollecito — richiesta iscrizione elenco operatori economici "
               f"(PEC del {data_it(inviata_il)})")
    corpo = f"""Spett.le {ente}

il {data_it(inviata_il)} vi ho trasmesso via PEC una richiesta di inserimento
fra gli operatori economici da consultare per i servizi informatici, con
riferimento a {rif} in scadenza.

Non avendo ricevuto riscontro, chiedo cortesemente conferma della presa in
carico e, se utile, l'indicazione dell'ufficio competente a cui indirizzare
la richiesta.

Resto a disposizione per ogni chiarimento e per l'eventuale cancellazione dai
nostri contatti.

Cordiali saluti

{MITTENTE['nome']}
P.IVA {MITTENTE['piva']}
tel. {MITTENTE['telefono']} — {MITTENTE['email']}
"""
    return oggetto, corpo


def genera_solleciti(dsn, lotto, giorni):
    out = os.path.join(QUI, "..", "docs", lotto)
    os.makedirs(out, exist_ok=True)
    with psycopg.connect(dsn, connect_timeout=30) as pg, pg.cursor() as cur:
        righe = da_sollecitare(cur, lotto, giorni)

    if not righe:
        print(f"Nessuno da sollecitare nel lotto '{lotto}'.\n")
        print("Perche' potrebbe essere vuoto:")
        print(f"  - nessuna PEC risulta CONSEGNATA: gira prima "
              f"`python pec_imap.py --leggi`")
        print(f"  - non sono ancora passati {giorni} giorni dall'invio")
        print( "  - chi ha risposto e chi e' gia' stato sollecitato sono esclusi")
        return 0

    for progressivo, ente, pec, cigs, inviata, consegnata in righe:
        oggetto, corpo = componi_sollecito(ente, pec, cigs, inviata)
        nome = f"sollecito-{progressivo:02d}-{slug(ente)}.txt"
        with open(os.path.join(out, nome), "w", encoding="utf-8") as f:
            f.write(f"A:       {pec}\nOGGETTO: {oggetto}\n\n{'-'*70}\n\n{corpo}")
        gg = (datetime.now().date() - inviata).days
        print(f"  {progressivo:2d}. {(ente or '?')[:44]:46s} "
              f"inviata {gg} giorni fa  -> {nome}")

    print(f"\n{len(righe)} solleciti generati in docs/{lotto}/")
    print(f"Per mandarli: python pec_smtp.py --lotto {lotto} --sollecito "
          f"--invia {','.join(str(r[0]) for r in righe)}")
    return len(righe)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=20)
    ap.add_argument("--provincia",
                    help="nomi separati da virgola, es. ANCONA,MACERATA")
    ap.add_argument("--giorni-min", type=int, default=30)
    ap.add_argument("--giorni-max", type=int, default=90)
    ap.add_argument("--importo-min", type=int, default=20000)
    ap.add_argument("--importo-max", type=int, default=500000)
    ap.add_argument("--cartella", default="pec",
                    help="sottocartella di docs/ dove scrivere")
    ap.add_argument("--consenti-doppioni", action="store_true",
                    help="non escludere gli enti gia' presenti in altri lotti")
    ap.add_argument("--riassegna", action="store_true",
                    help="consenti che righe gia' inviate cambino ente "
                         "(rompe la corrispondenza con cio' che e' partito)")
    ap.add_argument("--solleciti", action="store_true",
                    help="genera i secondi contatti invece di un lotto nuovo")
    ap.add_argument("--giorni-sollecito", type=int, default=GIORNI_SOLLECITO)
    a = ap.parse_args()

    if a.solleciti:
        dsn = leggi_dsn()
        if not dsn:
            raise SystemExit("connessione non configurata: vedi ingestion/.env.local")
        try:
            genera_solleciti(dsn, a.cartella, a.giorni_sollecito)
        except Exception as e:
            raise SystemExit(maschera(f"{type(e).__name__}: {e}", dsn))
        return
    province = [p.strip().upper() for p in a.provincia.split(",")] if a.provincia else None

    global OUT
    OUT = os.path.join(QUI, "..", "docs", a.cartella)
    os.makedirs(OUT, exist_ok=True)
    dsn = leggi_dsn()
    try:
        with psycopg.connect(dsn) as pg, pg.cursor() as cur:
            escludi = None if a.consenti_doppioni else gia_contattati(cur, a.cartella)
            gruppi, saltati = destinatari(cur, a.limite, province,
                                          a.giorni_min, a.giorni_max,
                                          a.importo_min, a.importo_max,
                                          escludi)
    except Exception as e:
        raise SystemExit(maschera(f"{type(e).__name__}: {e}", dsn))

    if saltati:
        print(f"{len(saltati)} enti saltati perche' gia' in un altro lotto:")
        for ente, pec, (lotto, stato, quando) in saltati[:10]:
            coda = f", inviata il {quando:%d/%m/%Y}" if quando else ""
            print(f"  - {(ente or '?')[:44]:46s} lotto '{lotto}' ({stato}{coda})")
        if len(saltati) > 10:
            print(f"  ... e altri {len(saltati)-10}")
        print("  (--consenti-doppioni per includerli comunque)\n")

    # L'avviso segue lo stato reale di MITTENTE: ripetere "compila la P.IVA"
    # quando e' gia' compilata insegna solo a non leggere gli avvisi.
    buchi = [k for k, v in MITTENTE.items() if str(v).startswith("[INSERISCI")]
    avviso = ([f"⚠️ Prima dell'invio: compilare {', '.join(buchi)} in MITTENTE",
               "(ingestion/genera_pec.py) e rigenerare — finche' restano segnaposto",
               "pec_smtp.py rifiuta di spedire. Poi verificare l'abilitazione MEPA."]
              if buchi else
              ["⚠️ Prima dell'invio: verificare l'abilitazione MEPA per le categorie",
               "pertinenti. Si spedisce dalla console o con `pec_smtp.py` —",
               "istruzioni in `docs/setup-pec.md`."])

    indice = ["# PEC da inviare — generate il " + datetime.now().strftime("%d/%m/%Y"),
              "", "Un file per ente. Firma: " + MITTENTE["nome"] + ".", ""]
    indice += avviso
    indice += ["",
              "| # | Ente | Prov | Contratti | Prima scadenza | PEC | File |",
              "|---|---|---|---|---|---|---|"]

    registro = []
    for i, gruppo in enumerate(gruppi, 1):
        pec, oggetto, corpo, ente, prov, n, prima = componi(gruppo)
        nome = f"{i:02d}-{slug(ente)}.txt"
        with open(os.path.join(OUT, nome), "w", encoding="utf-8") as f:
            f.write(f"A:       {pec}\nOGGETTO: {oggetto}\n\n{'-'*70}\n\n{corpo}")
        indice.append(f"| {i} | {ente[:44]} | {prov or '?'} | {n} | "
                      f"{data_it(prima)} | `{pec}` | `{nome}` |")
        registro.append((a.cartella, i, nome, ente, prov, pec,
                         ",".join(c[4] for c in gruppo), n))
        print(f"  {i:2d}. {ente[:46]:48s} {n} contr.  -> {nome}")

    # Rigenerare cambia la numerazione quando cambiano gli enti in finestra, e
    # un ente che si sposta lascia sul disco il file col numero vecchio. Non lo
    # nomina piu' nessuna riga di radar.invio, quindi in produzione non fa
    # danni — ma resta leggibile, col testo superato, e chi apre la cartella
    # non ha modo di distinguerlo da uno buono. Si toglie.
    # Solo i file con la forma prodotta da qui: nient'altro nella cartella.
    scritti = {r[2] for r in registro}
    for vecchio in sorted(os.listdir(OUT)):
        if re.match(r"\d{2}-.+\.txt$", vecchio) and vecchio not in scritti:
            os.remove(os.path.join(OUT, vecchio))
            print(f"  rimosso (numerazione superata): {vecchio}")

    # Registra il lotto per il tracciamento (invii.py). Rigenerare lo stesso
    # lotto NON azzera lo stato di cio' che e' gia' stato inviato.
    try:
        with psycopg.connect(leggi_dsn(), connect_timeout=30) as pg:
            with pg.cursor() as cur:
                # La numerazione si sposta quando cambiano gli enti in
                # finestra. Se una riga gia' inviata finisse a puntare a un
                # ente diverso, "inviata il 4/9" verrebbe attribuita a chi non
                # ha ricevuto niente, e il destinatario vero sparirebbe dai
                # radar. Meglio fermarsi che riscrivere la storia.
                cur.execute(
                    "SELECT progressivo, ente, stato FROM radar.invio "
                    "WHERE lotto = %s AND stato <> 'da_inviare'", (a.cartella,))
                nuovi = {r[1]: r[3] for r in registro}   # progressivo -> ente
                scontri = [(p, vecchio, nuovi.get(p), st)
                           for p, vecchio, st in cur.fetchall()
                           if nuovi.get(p) and nuovi[p] != vecchio]
                if scontri and not a.riassegna:
                    print(f"\n[FERMATO] rigenerando, {len(scontri)} righe gia' "
                          f"lavorate cambierebbero ente:")
                    for p, vecchio, nuovo, st in scontri[:8]:
                        print(f"  #{p}: era '{(vecchio or '?')[:34]}' ({st})")
                        print(f"       ora '{(nuovo or '?')[:34]}'")
                    print("\nI file .txt sono stati riscritti, ma il "
                          "tracciamento NON e' stato toccato.")
                    print("Finisci la tornata di invii prima di rigenerare, "
                          "oppure usa --riassegna se sai cosa stai facendo.")
                    return

                cur.executemany(
                    "INSERT INTO radar.invio (lotto, progressivo, file, ente, "
                    "provincia, pec, cig_inclusi, n_contratti) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (lotto, progressivo) DO UPDATE SET "
                    "file=excluded.file, ente=excluded.ente, pec=excluded.pec, "
                    # provincia mancava: rigenerando, l'ente cambiava e la
                    # provincia restava quella di prima. Risultato visibile:
                    # "COMUNE DI FERMO - ANCONA".
                    "provincia=excluded.provincia, "
                    "cig_inclusi=excluded.cig_inclusi, "
                    "n_contratti=excluded.n_contratti", registro)
            pg.commit()
        print(f"\nlotto '{a.cartella}' registrato per il tracciamento "
              f"(python invii.py --lotto {a.cartella})")
    except Exception as e:
        print(f"\n[avviso] tracciamento non registrato: {type(e).__name__}")

    with open(os.path.join(OUT, "INDICE.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(indice) + "\n")
    print(f"\n{len(gruppi)} PEC generate in docs/{a.cartella}/ — "
          f"indice in INDICE.md")


if __name__ == "__main__":
    main()
