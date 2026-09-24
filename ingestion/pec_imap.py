#!/usr/bin/env python3
"""
Lettura delle ricevute PEC (task R15).

IL BUCO CHE CHIUDE. pec_smtp.py sa spedire, ma nessuno leggeva le ricevute: se
una casella era piena, l'indirizzo dismesso o il dominio rifiutava, la riga
restava 'inviata' per sempre e si aspettava una risposta che non poteva
arrivare. Lo stato 'non_consegnata' esisteva in radar.invio e non lo popolava
nessuno.

COME FUNZIONA UNA PEC. Ogni invio genera messaggi automatici che il gestore
marca con intestazioni previste dal DM 2/11/2005:

    X-Ricevuta: accettazione            il TUO gestore l'ha presa in carico
    X-Ricevuta: avvenuta-consegna       e' entrata nella casella del destinatario
    X-Ricevuta: errore-consegna         NON e' arrivata, ed e' definitivo
    X-Ricevuta: preavviso-errore-consegna   ci sta provando ancora
    X-Ricevuta: non-accettazione        respinta in partenza dal tuo gestore

    X-Riferimento-Message-ID: <...>     il Message-ID del messaggio originale

E' quest'ultima che fa tutto il lavoro: e' la stessa stringa che pec_smtp.py
salva in radar.invio.message_id. Nessun bisogno di aprire il postacert.eml.

La ricevuta di avvenuta consegna vale anche come prova legale che l'ente ha
ricevuto: e' il motivo per cui la casella non va svuotata.

SOLA LETTURA. Non cancella, non sposta, non marca come letto (usa BODY.PEEK).
Una casella PEC e' un archivio con valore probatorio, non una coda da smaltire.

R37 — LA POSTA VERA. leggi_ricevute() legge solo le ricevute tecniche (il
gestore che conferma consegna/errore): la vera risposta di un funzionario, in
arrivo come email normale, veniva letta solo se qualcuno la inoltrava a mano.
risposte_da_leggere() incrocia la posta senza X-Ricevuta con gli invii ancora
'inviata'/'accettata'/'consegnata' (via In-Reply-To o, in mancanza, mittente =
PEC a cui avevamo scritto) e la elenca — non scrive nulla, la lettura e la
decisione SI/NO/nota restano umane, solo il "controllare se e' arrivato
qualcosa" si automatizza.

Uso:
    python pec_imap.py --verifica          # prova la connessione
    python pec_imap.py --leggi             # ricevute degli ultimi 30 giorni
    python pec_imap.py --leggi --giorni 90
    python pec_imap.py --leggi --prova     # mostra cosa farebbe, non scrive
    python pec_imap.py --risposte          # posta vera da leggere (R37)
    python pec_imap.py --risposte --telegram   # idem, ma avvisa via Telegram
    python pec_imap.py --stato             # esito tecnico degli invii

Dipendenza: psycopg. IMAP ed email sono libreria standard.
"""

import argparse
import email
import imaplib
import re
import ssl
import sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header

import psycopg
from notifica import conf, maschera, invia_telegram
from pec_smtp import credenziali
from push_supabase import leggi_dsn

GIORNI_DEFAULT = 30

# Cosa dice ciascun tipo di ricevuta, e cosa ne facciamo.
#   colonna       dove si scrive la data
#   stato         a quale stato porta la riga (None = lascia com'e')
#   definitivo    se e' l'ultima parola su questo invio
RICEVUTE = {
    "accettazione":             dict(colonna="accettata_il",  stato=None,
                                     etichetta="accettata dal gestore"),
    "presa-in-carico":          dict(colonna=None,            stato=None,
                                     etichetta="presa in carico"),
    "avvenuta-consegna":        dict(colonna="consegnata_il", stato=None,
                                     etichetta="CONSEGNATA"),
    "errore-consegna":          dict(colonna=None, stato="non_consegnata",
                                     etichetta="NON CONSEGNATA"),
    "non-accettazione":         dict(colonna=None, stato="non_consegnata",
                                     etichetta="RESPINTA in partenza"),
    "rilevazione-virus":        dict(colonna=None, stato="non_consegnata",
                                     etichetta="RESPINTA (virus rilevato)"),
    "preavviso-errore-consegna": dict(colonna=None, stato=None,
                                      etichetta="consegna in ritardo"),
}


def host_imap():
    """Host IMAP: esplicito, oppure dedotto da quello SMTP."""
    esplicito = conf("PEC_IMAP_HOST", False)
    if esplicito:
        return esplicito
    smtp = credenziali()["PEC_HOST"]
    # Aruba: smtps.pec.aruba.it -> imaps.pec.aruba.it. Vale per diversi
    # gestori, ma se non torna si mette PEC_IMAP_HOST e non se ne parla piu'.
    if smtp.startswith("smtps."):
        return "imaps." + smtp[6:]
    if smtp.startswith("smtp."):
        return "imap." + smtp[5:]
    if smtp.startswith("sendm."):          # Legalmail
        return "imapmail." + smtp[6:]
    raise RuntimeError(
        f"non so dedurre l'host IMAP da '{smtp}': aggiungi PEC_IMAP_HOST in "
        f"ingestion/.env.local (Aruba: imaps.pec.aruba.it)")


def apri_imap():
    c = credenziali()
    host = host_imap()
    porta = int(conf("PEC_IMAP_PORT", False) or 993)
    try:
        conn = imaplib.IMAP4_SSL(host, porta,
                                 ssl_context=ssl.create_default_context())
        conn.login(c["PEC_USER"], c["PEC_PASSWORD"])
    except imaplib.IMAP4.error as e:
        raise RuntimeError(
            f"{host} ha rifiutato l'accesso ({e}). E' la stessa credenziale "
            f"dell'invio: se lo SMTP funziona e questo no, controlla che la "
            f"casella abbia l'accesso IMAP attivo.") from None
    except ssl.SSLCertVerificationError as e:
        from pec_smtp import spiega_tls
        raise RuntimeError(spiega_tls(host, porta,
                                      e.verify_message or str(e))) from None
    except Exception as e:
        raise RuntimeError(maschera(f"{type(e).__name__}: {e}",
                                    c["PEC_PASSWORD"], c["PEC_USER"])) from None
    return conn, host


def testo(v):
    """Intestazione decodificata, anche se codificata MIME."""
    if not v:
        return ""
    try:
        return str(make_header(decode_header(v))).strip()
    except Exception:
        return str(v).strip()


def normalizza_id(v):
    """I Message-ID viaggiano con le parentesi angolari e a volte senza."""
    if not v:
        return None
    v = testo(v).strip()
    m = re.search(r"<[^>]+>", v)
    return (m.group(0) if m else v).strip()


def leggi_ricevute(conn, giorni):
    """Le ricevute PEC nella finestra, come (tipo, riferimento, data, oggetto).

    Si filtra su SINCE lato server: scaricare l'intera casella per trovarne
    dieci sarebbe lento e inutile.
    """
    conn.select("INBOX", readonly=True)
    da = (datetime.now() - timedelta(days=giorni)).strftime("%d-%b-%Y")
    tipo, dati = conn.search(None, f'(SINCE {da})')
    if tipo != "OK":
        raise RuntimeError(f"ricerca IMAP fallita: {tipo}")

    ids = dati[0].split()
    trovate = []
    for i in ids:
        # BODY.PEEK non tocca il flag \Seen: la casella resta come l'hai
        # lasciata, che su un archivio con valore probatorio conta.
        tipo, pezzi = conn.fetch(i, "(BODY.PEEK[HEADER])")
        if tipo != "OK" or not pezzi or not isinstance(pezzi[0], tuple):
            continue
        msg = email.message_from_bytes(pezzi[0][1])
        ric = testo(msg.get("X-Ricevuta")).lower()
        if not ric:
            continue                      # non e' una ricevuta: e' posta vera
        rif = normalizza_id(msg.get("X-Riferimento-Message-ID"))
        if not rif:
            continue
        try:
            quando = email.utils.parsedate_to_datetime(msg.get("Date"))
        except Exception:
            quando = None
        if quando and quando.tzinfo is None:
            quando = quando.replace(tzinfo=timezone.utc)
        trovate.append((ric, rif, quando, testo(msg.get("Subject"))))
    return trovate, len(ids)


def applica(cur, ricevute, prova):
    """Scrive gli esiti su radar.invio. Ritorna il riepilogo per tipo."""
    cur.execute("SELECT message_id, lotto, progressivo, ente FROM radar.invio "
                "WHERE message_id IS NOT NULL")
    per_id = {normalizza_id(m): (lot, n, ente)
              for m, lot, n, ente in cur.fetchall()}

    conteggi, orfane, tocchi = {}, 0, 0
    for tipo, rif, quando, _ogg in ricevute:
        conteggi[tipo] = conteggi.get(tipo, 0) + 1
        riga = per_id.get(rif)
        if not riga:
            orfane += 1
            continue
        lotto, n, ente = riga
        regola = RICEVUTE.get(tipo)
        if not regola:
            continue

        campi, par = [], []
        if regola["colonna"] and quando:
            campi.append(f"{regola['colonna']} = %s")
            par.append(quando)
        if regola["stato"]:
            campi.append("stato = %s")
            par.append(regola["stato"])
            campi.append("errore_consegna = %s")
            par.append(regola["etichetta"])
        if not campi:
            continue

        print(f"  {lotto}#{n:<3d} {(ente or '?')[:38]:40s} {regola['etichetta']}")
        if prova:
            continue
        par += [lotto, n]
        cur.execute(f"UPDATE radar.invio SET {', '.join(campi)} "
                    f"WHERE lotto = %s AND progressivo = %s", par)
        tocchi += cur.rowcount
    return conteggi, orfane, tocchi


def testo_semplice(msg):
    """Le prime righe del corpo testuale, se c'e' — le PEC spesso hanno il
    contenuto vero in un PDF allegato e il corpo vuoto: in quel caso si
    ritorna stringa vuota, non e' un errore."""
    corpo = ""
    if msg.is_multipart():
        for parte in msg.walk():
            if parte.get_content_type() == "text/plain" and not parte.get_filename():
                try:
                    corpo = parte.get_payload(decode=True).decode(
                        parte.get_content_charset() or "utf-8", errors="replace")
                except Exception:
                    corpo = ""
                break
    elif msg.get_content_type() == "text/plain":
        try:
            corpo = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            corpo = ""
    return corpo.strip()


def allegati(msg):
    nomi = []
    if msg.is_multipart():
        for parte in msg.walk():
            n = parte.get_filename()
            if n:
                nomi.append(testo(n))
    return nomi


def leggi_posta_vera(conn, giorni):
    """Le email nella finestra che NON sono ricevute PEC automatiche — posta
    scritta da una persona. E' l'opposto di leggi_ricevute(): qui e'
    X-Ricevuta ad essere ASSENTE."""
    conn.select("INBOX", readonly=True)
    da = (datetime.now() - timedelta(days=giorni)).strftime("%d-%b-%Y")
    tipo, dati = conn.search(None, f"(SINCE {da})")
    if tipo != "OK":
        raise RuntimeError(f"ricerca IMAP fallita: {tipo}")

    trovate = []
    for i in dati[0].split():
        tipo, pezzi = conn.fetch(i, "(BODY.PEEK[])")
        if tipo != "OK" or not pezzi or not isinstance(pezzi[0], tuple):
            continue
        msg = email.message_from_bytes(pezzi[0][1])
        if testo(msg.get("X-Ricevuta")):
            continue                      # ricevuta tecnica, non posta vera
        try:
            quando = email.utils.parsedate_to_datetime(msg.get("Date"))
        except Exception:
            quando = None
        mittente = email.utils.parseaddr(msg.get("From", ""))[1].lower()
        rif = normalizza_id(msg.get("In-Reply-To")) or normalizza_id(msg.get("References"))
        trovate.append(dict(mittente=mittente, oggetto=testo(msg.get("Subject")),
                            quando=quando, rif=rif, corpo=testo_semplice(msg),
                            allegati=allegati(msg)))
    return trovate


def risposte_da_leggere(cur, giorni, conn):
    """Incrocia la posta vera con gli invii ancora senza esito: e' il
    sostituto di aspettare che qualcuno inoltri la PEC a mano."""
    cur.execute("""SELECT lotto, progressivo, ente, pec, message_id FROM radar.invio
                   WHERE stato IN ('inviata','accettata','consegnata')""")
    per_msgid, per_pec = {}, {}
    for lot, n, ente, pec, mid in cur.fetchall():
        riga = (lot, n, ente, pec)
        if mid:
            per_msgid[normalizza_id(mid)] = riga
        if pec:
            per_pec.setdefault(pec.lower(), []).append(riga)

    posta = leggi_posta_vera(conn, giorni)
    trovate = []
    for m in posta:
        riga = per_msgid.get(m["rif"]) if m["rif"] else None
        via = "risposta (In-Reply-To)"
        if not riga:
            candidati = per_pec.get(m["mittente"], [])
            if len(candidati) == 1:
                riga = candidati[0]
                via = "mittente = PEC a cui avevamo scritto"
        if riga:
            trovate.append((riga, via, m))
    return trovate


def pulisci_md(v):
    """Toglie i caratteri speciali del Markdown legacy di Telegram da un
    testo dinamico (oggetto, ente...): senza, un ente con un '_' o un '*'
    nel nome manda in errore l'intero messaggio (stesso bug gia' visto
    su promemoria_enti.py, qui evitato a monte)."""
    return re.sub(r"[_*`\[\]]", " ", v or "")


def notifica_risposte(trovate):
    righe = [f"{len(trovate)} risposta/e PEC da leggere:\n"]
    for (lot, n, ente, pec), via, m in trovate:
        righe.append(f"{lot}#{n} — {pulisci_md((ente or '?')[:50])}")
        righe.append(f"da {pulisci_md(m['mittente'])}")
    righe.append("\nDettagli: python pec_imap.py --risposte")
    invia_telegram("\n".join(righe))


def stato(cur):
    cur.execute("""
        SELECT lotto, progressivo, ente, stato, inviata_il,
               accettata_il, consegnata_il, errore_consegna
        FROM radar.invio WHERE inviata_il IS NOT NULL
        ORDER BY lotto, progressivo""")
    righe = cur.fetchall()
    if not righe:
        print("nessuna PEC ancora inviata.")
        return
    print(f"=== esito tecnico di {len(righe)} invii ===\n")
    senza = 0
    for lot, n, ente, st, inv, acc, cons, err in righe:
        if cons:
            esito = f"consegnata {cons:%d/%m %H:%M}"
        elif err:
            esito = err
        elif acc:
            esito = f"accettata {acc:%d/%m %H:%M}, consegna non ancora confermata"
            senza += 1
        else:
            esito = "nessuna ricevuta"
            senza += 1
        print(f"  {lot}#{n:<3d} {(ente or '?')[:40]:42s} {esito}")
    if senza:
        print(f"\n  {senza} senza conferma di consegna. Se sono passate piu' di "
              f"24 ore,\n  esegui `python pec_imap.py --leggi`: le ricevute "
              f"arrivano in minuti.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verifica", action="store_true")
    ap.add_argument("--leggi", action="store_true")
    ap.add_argument("--risposte", action="store_true",
                    help="posta vera in arrivo collegabile a un invio senza esito (R37)")
    ap.add_argument("--telegram", action="store_true",
                    help="con --risposte, avvisa su Telegram invece di stampare")
    ap.add_argument("--stato", action="store_true")
    ap.add_argument("--giorni", type=int, default=GIORNI_DEFAULT)
    ap.add_argument("--prova", action="store_true",
                    help="mostra cosa aggiornerebbe, senza scrivere")
    a = ap.parse_args()

    dsn = leggi_dsn()
    if not dsn:
        sys.exit("connessione non configurata: vedi ingestion/.env.local")

    if a.stato or not (a.verifica or a.leggi or a.risposte):
        with psycopg.connect(dsn, connect_timeout=30) as pg, pg.cursor() as cur:
            stato(cur)
        return

    try:
        conn, host = apri_imap()
    except RuntimeError as e:
        sys.exit(f"accesso IMAP non riuscito.\n\n{e}")

    try:
        if a.verifica:
            tipo, dati = conn.select("INBOX", readonly=True)
            print(f"accesso riuscito a {host}")
            print(f"INBOX: {dati[0].decode()} messaggi. Nessuna modifica.")
            return

        if a.risposte:
            with psycopg.connect(dsn, connect_timeout=30) as pg, pg.cursor() as cur:
                trovate = risposte_da_leggere(cur, a.giorni, conn)
            if not trovate:
                print(f"  nessuna posta in arrivo negli ultimi {a.giorni} giorni "
                      f"collegabile a un invio ancora senza esito.")
                return
            if a.telegram:
                notifica_risposte(trovate)
                print(f"  {len(trovate)} risposta/e — avviso mandato su Telegram.")
                return
            print(f"  {len(trovate)} messaggi da leggere:\n")
            for (lot, n, ente, pec), via, m in trovate:
                quando = m["quando"].strftime("%d/%m %H:%M") if m["quando"] else "?"
                print(f"  {lot}#{n} — {(ente or '?')[:50]}")
                print(f"    da {m['mittente']} il {quando} — {via}")
                print(f"    oggetto: {m['oggetto']}")
                if m["allegati"]:
                    print(f"    allegati: {', '.join(m['allegati'])}")
                if m["corpo"]:
                    anteprima = m["corpo"][:300].replace("\n", " ")
                    print(f"    corpo: {anteprima}{'…' if len(m['corpo']) > 300 else ''}")
                print()
            print("  Nessuna scrittura fatta: leggi il contenuto e registra l'esito "
                  "con `python invii.py --lotto <lotto> --risposta <n> --note \"...\"`.")
            return

        print(f"Ricevute degli ultimi {a.giorni} giorni su {host}\n")
        ricevute, esaminati = leggi_ricevute(conn, a.giorni)
    finally:
        try:
            conn.logout()
        except Exception:
            pass

    if not ricevute:
        print(f"  nessuna ricevuta fra {esaminati} messaggi esaminati.")
        return

    with psycopg.connect(dsn, connect_timeout=30) as pg, pg.cursor() as cur:
        conteggi, orfane, tocchi = applica(cur, ricevute, a.prova)
        if not a.prova:
            pg.commit()

    print(f"\n  {esaminati} messaggi esaminati, {len(ricevute)} ricevute:")
    for t, q in sorted(conteggi.items(), key=lambda x: -x[1]):
        print(f"    {t:28s} {q:4d}")
    if orfane:
        print(f"\n  {orfane} ricevute senza riga corrispondente: sono PEC "
              f"partite\n  prima che il message_id venisse tracciato, o "
              f"mandate a mano.")
    print(f"\n  {tocchi} righe aggiornate."
          if not a.prova else "\n  prova: nulla e' stato scritto.")


if __name__ == "__main__":
    main()
