#!/usr/bin/env python3
"""
Invio delle PEC direttamente dal Radar (task R7b).

Perche' esiste. I file generati da genera_pec.py erano da mandare a mano:
aprire il .txt, copiare destinatario, oggetto e corpo nella webmail, spedire,
e poi ricordarsi di segnare l'invio con invii.py. Due minuti a PEC e un passo
che si dimentica sempre — quello del tracciamento. Qui il messaggio parte e la
riga si segna nella stessa transazione, quindi non si disallineano mai.

Una PEC ha valore legale e non si ritira. I tre controlli qui sotto costano
zero tempo e coprono gli errori che dopo non si correggono:

  1. segnaposto: se nel testo e' rimasto "[INSERISCI P.IVA]" non si parte.
     E' l'errore piu' probabile, perche' MITTENTE in genera_pec.py nasce coi
     campi vuoti.
  2. destinatario: quello scritto nel file deve coincidere con quello
     registrato in radar.invio. Se non coincide qualcosa e' stato rigenerato
     a meta' e il messaggio andrebbe all'ente sbagliato.
  3. doppio invio: una riga gia' inviata non riparte senza --forza. Una PEC
     doppia allo stesso protocollo e' l'errore che si nota di piu'.

Piu' un tetto giornaliero, che non e' prudenza ma un vincolo dei provider:
oltre una certa soglia la casella viene limitata.

Credenziali: SOLO da ingestion/.env.local (git-ignored) o variabili
d'ambiente. Mai nel codice.

Uso:
    python pec_smtp.py --lotto pec-marche --stato
    python pec_smtp.py --lotto pec-marche --invia 1 --prova    # non spedisce
    python pec_smtp.py --lotto pec-marche --invia 1
    python pec_smtp.py --lotto pec-marche --invia 1-15         # chiede conferma

Dipendenza: psycopg. SMTP e' libreria standard.
"""

import argparse
import os
import re
import smtplib
import socket
import ssl
import sys
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid

import psycopg
from invii import numeri
from notifica import conf, maschera
from push_supabase import leggi_dsn

QUI = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(QUI, "..", "docs")

# I provider PEC limitano la casella oltre una certa soglia. Il numero non e'
# pubblicato ed e' diverso fra Aruba, Legalmail e Poste: venti al giorno sta
# sotto la soglia di tutti.
MAX_GIORNO = 20

# Stati che significano "questa e' gia' partita".
GIA_PARTITE = ("inviata", "risposta", "nessuna_risposta", "chiusa")

SEGNAPOSTO = re.compile(r"\[INSERISCI[^\]]*\]")

# Il formato scritto da genera_pec.py: due intestazioni, una riga di
# separazione, poi il corpo.
FORMATO = re.compile(r"A:\s*(\S+)\s*\nOGGETTO:\s*(.+?)\n\n-+\n\n(.*)$", re.S)


def leggi_messaggio(lotto, nomefile):
    """Rilegge il .txt generato. La fonte di verita' del testo resta il file:
    cosi' quello che parte e' esattamente quello che si rilegge dopo."""
    p = os.path.join(DOCS, lotto, nomefile)
    if not os.path.exists(p):
        raise FileNotFoundError(
            f"manca docs/{lotto}/{nomefile} — rigenera con "
            f"genera_pec.py --cartella {lotto}")
    with open(p, encoding="utf-8") as f:
        testo = f.read()
    m = FORMATO.match(testo)
    if not m:
        raise ValueError(f"docs/{lotto}/{nomefile} non ha il formato atteso")
    return m.group(1).strip(), m.group(2).strip(), m.group(3)


def verifica(dest, oggetto, corpo, pec_attesa):
    """I motivi per cui questo messaggio non deve partire."""
    problemi = []
    buchi = sorted(set(SEGNAPOSTO.findall(oggetto + corpo)))
    if buchi:
        problemi.append(
            "nel testo ci sono ancora dei segnaposto (" + ", ".join(buchi)
            + "): compila MITTENTE in genera_pec.py e rigenera le PEC")
    if "@" not in dest:
        problemi.append(f"destinatario non valido: {dest}")
    if pec_attesa and dest.lower() != pec_attesa.lower():
        problemi.append(f"il file scrive a {dest} ma la riga registrata dice "
                        f"{pec_attesa} — qualcosa e' stato rigenerato a meta'")
    return problemi


def riga(cur, lotto, n):
    cur.execute("SELECT file, ente, pec, stato, inviata_il FROM radar.invio "
                "WHERE lotto = %s AND progressivo = %s", (lotto, n))
    r = cur.fetchone()
    if not r:
        raise LookupError(f"{lotto}#{n} non risulta registrato")
    return r


def inviate_oggi(cur):
    cur.execute("SELECT count(*) FROM radar.invio "
                "WHERE inviata_il = current_date")
    return cur.fetchone()[0]


# Dopo quanti mesi la stessa casella torna contattabile. Sotto e' insistenza
# sullo stesso protocollo, sopra e' un contatto nuovo e legittimo.
MESI_SILENZIO = 6


def gia_scritto(cur, pec, lotto, n):
    """Se alla stessa casella e' gia' partita una PEC di recente, dice quando.

    Il controllo e' sulla casella e non sull'ente: lotti diversi scrivono lo
    stesso ente con nomi diversi, ma la posta arriva sempre li'. Esclude la
    riga corrente, altrimenti un reinvio con --forza bloccherebbe se stesso.
    """
    cur.execute("""
        SELECT lotto, progressivo, ente, inviata_il
        FROM radar.invio
        WHERE lower(pec) = lower(%s)
          AND NOT (lotto = %s AND progressivo = %s)
          AND inviata_il IS NOT NULL
          AND inviata_il > current_date - (%s || ' months')::interval
        ORDER BY inviata_il DESC LIMIT 1""",
        (pec, lotto, n, MESI_SILENZIO))
    return cur.fetchone()


def stato_sollecito(cur, lotto, n):
    """Consegna e sollecito di una riga: servono a decidere se richiamare."""
    cur.execute("SELECT consegnata_il, sollecitata_il FROM radar.invio "
                "WHERE lotto = %s AND progressivo = %s", (lotto, n))
    return cur.fetchone() or (None, None)


def spedisci(cur, lotto, n, prova=False, forza=False, sollecito=False):
    """Prepara e — con prova=False — spedisce una PEC. Non fa commit: lo fa il
    chiamante, cosi' invio e marcatura stanno nella stessa transazione.

    Con sollecito=True manda il secondo contatto: file diverso, controlli
    diversi, e scrive sollecitata_il invece di inviata_il.
    """
    nomefile, ente, pec_attesa, stato, inviata_il = riga(cur, lotto, n)
    if sollecito:
        nomefile = "sollecito-" + nomefile
    dest, oggetto, corpo = leggi_messaggio(lotto, nomefile)

    esito = {"lotto": lotto, "n": n, "ente": ente, "dest": dest,
             "oggetto": oggetto, "corpo": corpo, "stato": stato,
             "file": nomefile, "sollecito": sollecito}

    problemi = verifica(dest, oggetto, corpo, pec_attesa)

    if sollecito:
        consegnata, sollecitata = stato_sollecito(cur, lotto, n)
        if sollecitata and not forza:
            problemi.append(f"gia' sollecitata il {sollecitata:%d/%m/%Y} — "
                            f"un secondo richiamo diventa insistenza")
        elif not consegnata and not forza:
            problemi.append(
                "non risulta CONSEGNATA: sollecitare una PEC che non e' mai "
                "arrivata e' rumore. Esegui prima `python pec_imap.py --leggi`.")
        elif stato == "risposta" and not forza:
            problemi.append("ha gia' risposto: non va sollecitata")
    elif stato in GIA_PARTITE and not forza:
        quando = f" il {inviata_il:%d/%m/%Y}" if inviata_il else ""
        problemi.append(f"gia' inviata{quando} — per rimandarla serve --forza")

    # Stessa casella, altro lotto: due PEC dalla stessa persona allo stesso
    # protocollo a poche settimane di distanza sono una figuraccia. Il
    # sollecito e' l'eccezione: e' un richiamo voluto sullo stesso messaggio.
    doppio = (gia_scritto(cur, dest, lotto, n)
              if not forza and not sollecito else None)
    if doppio:
        d_lotto, d_n, d_ente, d_quando = doppio
        problemi.append(
            f"a {dest} e' gia' partita una PEC il {d_quando:%d/%m/%Y} "
            f"({d_lotto}#{d_n}, {(d_ente or '?')[:40]}). Meno di "
            f"{MESI_SILENZIO} mesi: sarebbe insistenza sullo stesso "
            f"protocollo. Con --forza si manda comunque.")
    esito["problemi"] = problemi

    if prova or problemi:
        return esito

    tetto = int(conf("PEC_MAX_GIORNO", False) or MAX_GIORNO)
    gia = inviate_oggi(cur)
    if gia >= tetto:
        esito["problemi"] = [
            f"tetto giornaliero raggiunto ({gia}/{tetto}): i provider PEC "
            f"limitano la casella oltre una certa soglia. Riprendi domani, "
            f"oppure alza PEC_MAX_GIORNO in .env.local se conosci il tuo "
            f"limite reale."]
        return esito

    esito["message_id"] = invia(dest, oggetto, corpo)
    # Il message_id va in una colonna sua, non solo nelle note: e' la chiave
    # con cui pec_imap.py ritrova questa riga partendo dalla ricevuta.
    if sollecito:
        # Lo stato resta 'inviata': il sollecito non e' un invio nuovo, e'
        # lo stesso contatto ripreso. Ma la finestra dei 21 giorni riparte,
        # e questo lo dice sollecitata_il.
        cur.execute(
            "UPDATE radar.invio SET sollecitata_il = current_date, "
            "message_id = %s, note = coalesce(note || ' | ', '') || %s "
            "WHERE lotto = %s AND progressivo = %s",
            (esito["message_id"],
             f"sollecito {esito['message_id']}", lotto, n))
    else:
        cur.execute(
            "UPDATE radar.invio SET stato = 'inviata', inviata_il = current_date, "
            "message_id = %s, note = coalesce(note || ' | ', '') || %s "
            "WHERE lotto = %s AND progressivo = %s",
            (esito["message_id"],
             f"inviata dalla console, {esito['message_id']}", lotto, n))
    esito["inviata"] = True
    return esito


def credenziali():
    """Le chiavi PEC, tutte insieme.

    Non si usa conf(..., obbligatoria=True) perche' quella chiama sys.exit():
    va bene da riga di comando, ma dentro un thread del server della console
    solleverebbe SystemExit, che non e' una Exception e sfuggirebbe al
    gestore degli errori — l'utente vedrebbe una risposta vuota invece del
    motivo. Qui si raccolgono tutte le mancanze e si dicono in una volta.
    """
    letti = {k: conf(k, False) for k in
             ("PEC_HOST", "PEC_PORT", "PEC_USER", "PEC_PASSWORD", "PEC_NOME")}
    mancanti = [k for k in ("PEC_HOST", "PEC_USER", "PEC_PASSWORD")
                if not letti[k]]
    if mancanti:
        raise RuntimeError(
            "casella PEC non configurata: manca " + ", ".join(mancanti)
            + " in ingestion/.env.local (vedi docs/setup-pec.md)")
    return letti


# Antivirus e proxy che si mettono in mezzo alle connessioni TLS. Il nome
# compare in chiaro dentro il certificato che presentano al posto di quello
# vero del server.
INTERCETTATORI = ("Avast", "AVG", "Kaspersky", "ESET", "Bitdefender",
                  "Norton", "Fiddler", "Charles Proxy")


def chi_intercetta(host, porta):
    """Il nome di chi ha firmato il certificato, se non e' il server.

    Serve solo a spiegare un errore, mai a decidere se fidarsi: la
    connessione viene letta senza verificare, quindi il risultato e' un
    indizio da mostrare a una persona, non un dato su cui ragionare.
    """
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((host, porta), timeout=15) as s:
            with ctx.wrap_socket(s, server_hostname=host) as t:
                catena = t.get_unverified_chain() or []
        crudo = b" ".join(bytes(c) for c in catena)
    except Exception:
        return None, False
    for nome in INTERCETTATORI:
        if nome.encode() in crudo:
            # Avast firma con un root diverso quando e' lui a non fidarsi del
            # server: quel root non sta in nessun magazzino, di proposito.
            return nome, b"Untrusted Root" in crudo
    return None, False


def spiega_tls(host, porta, errore):
    """Trasforma 'unable to get local issuer certificate' in qualcosa di utile."""
    nome, sfiduciato = chi_intercetta(host, porta)
    if not nome:
        return (f"verifica del certificato di {host} fallita: {errore}. "
                f"Il server potrebbe non inviare i certificati intermedi.")
    if sfiduciato:
        return (
            f"la connessione a {host} non arriva dal server ma da {nome}, che "
            f"intercetta la posta. {nome} l'ha firmata col proprio root "
            f"\"Untrusted\", cioe' sta dicendo che NON e' riuscito a validare "
            f"il server: quel root non e' in nessun magazzino certificati, "
            f"quindi nessun programma puo' accettarlo. Non e' aggiustabile dal "
            f"codice. Disattiva la scansione SSL/TLS nella Protezione posta di "
            f"{nome} (o aggiungi un'eccezione per {host}), poi riprova.")
    return (
        f"la connessione a {host} passa da {nome}, che la intercetta e la "
        f"rifirma. Il suo certificato non supera la verifica: {errore}. "
        f"Escludi {host} dalla scansione posta di {nome}.")


def apri_smtp():
    """Connessione TLS autenticata alla casella PEC. La chiude chi la apre.

    Sta a parte da invia() perche' e' l'unico pezzo che puo' essere provato
    senza spedire: --verifica lo usa e chiude subito. Utile soprattutto ogni
    sei mesi, quando la password per i programmi di posta scade.
    """
    c = credenziali()
    host = c["PEC_HOST"]
    porta = int(c["PEC_PORT"] or 465)
    utente = c["PEC_USER"]
    password = c["PEC_PASSWORD"]

    ctx = ssl.create_default_context()
    try:
        if porta == 465:
            conn = smtplib.SMTP_SSL(host, porta, context=ctx, timeout=90)
        else:
            conn = smtplib.SMTP(host, porta, timeout=90)
            conn.starttls(context=ctx)
        conn.login(utente, password)
    except smtplib.SMTPAuthenticationError as e:
        # Non si riprova con varianti: dopo alcuni tentativi falliti i provider
        # PEC bloccano la casella, e indovinare una credenziale non e' un
        # rimedio. Si spiega dove guardare e ci si ferma.
        risposta = (e.smtp_error.decode(errors="replace")
                    if isinstance(e.smtp_error, bytes) else str(e.smtp_error))
        raise RuntimeError(
            f"{host} ha rifiutato le credenziali ({e.smtp_code} "
            f"{risposta.strip()}). Non ritento: dopo qualche tentativo la "
            f"casella viene bloccata. Le cause, in ordine di probabilita': "
            f"(1) con la verifica in due passaggi attiva, la password "
            f"principale della PEC NON funziona da programma di posta: serve "
            f"la \"password per programmi di posta\", che si genera nella "
            f"gestione casella sotto Sicurezza e vale 6 mesi — se prima "
            f"funzionava ed e' passato mezzo anno, e' scaduta; "
            f"(2) e' la password del pannello/area clienti invece di quella "
            f"della casella: sono due password diverse; "
            f"(3) PEC_USER non e' esattamente l'indirizzo della casella — "
            f"ora vale \"{utente}\". "
            f"Vedi docs/setup-pec.md."
        ) from None
    except ssl.SSLCertVerificationError as e:
        # Non si ripiega su una verifica piu' permissiva: qui passano le
        # credenziali della casella PEC e un messaggio con valore legale.
        # Se il certificato non torna, la connessione non si fa e basta.
        raise RuntimeError(spiega_tls(host, porta, e.verify_message or str(e))) \
            from None
    except Exception as e:
        # Un traceback SMTP puo' contenere la password in chiaro.
        raise RuntimeError(
            maschera(f"{type(e).__name__}: {e}", password, utente)) from None
    return conn, utente, (c["PEC_NOME"] or "Leonardo Foschi")


def allega(msg):
    """L'allegato di R21, se e solo se e' stato acceso ED e' pronto.

    Due condizioni e non una. PEC_ALLEGATO=1 dice che lo vuoi; presentazione.
    controlla() dice se il file e' allegabile davvero. Un PDF con dentro un
    [DA COMPILARE] spedito a un ente pubblico non e' un allegato incompleto,
    e' l'unica occasione che si aveva con quell'ente, bruciata.

    Si fallisce rumorosamente invece di spedire senza allegato: se hai chiesto
    l'allegato e non parte, devi saperlo prima, non scoprirlo dopo venti PEC.
    """
    if (conf("PEC_ALLEGATO", False) or "").strip() not in ("1", "si", "true"):
        return
    import presentazione
    p = os.path.abspath(os.path.join(presentazione.FUORI, presentazione.NOME))
    problemi = presentazione.controlla(p)
    if problemi:
        raise RuntimeError(
            "PEC_ALLEGATO e' acceso ma la presentazione non e' allegabile:\n  - "
            + "\n  - ".join(problemi))
    with open(p, "rb") as f:
        msg.add_attachment(f.read(), maintype="application",
                           subtype="pdf", filename=presentazione.NOME)


def invia(dest, oggetto, corpo):
    """Spedisce e ritorna il Message-ID. Il provider PEC incapsula il messaggio
    in una busta firmata e le ricevute citano questo id: e' cio' che permette
    di riconciliare una ricevuta di consegna con la riga in radar.invio."""
    conn, utente, nome = apri_smtp()

    msg = EmailMessage()
    msg["From"] = formataddr((nome, utente))
    msg["To"] = dest
    msg["Subject"] = oggetto
    msg["Date"] = format_datetime(datetime.now().astimezone())
    mid = make_msgid(domain=utente.rsplit("@", 1)[-1])
    msg["Message-ID"] = mid
    msg.set_content(corpo, subtype="plain", charset="utf-8")
    allega(msg)

    try:
        with conn as s:
            s.send_message(msg)
    except Exception as e:
        raise RuntimeError(maschera(f"{type(e).__name__}: {e}", utente)) from None
    return mid


def verifica_accesso():
    """Prova host, TLS e credenziali. Non spedisce niente."""
    conn, utente, nome = apri_smtp()
    try:
        conn.noop()
    finally:
        try:
            conn.quit()
        except Exception:
            pass
    return utente, nome


# ------------------------------------------------------------------ CLI
def mostra(e):
    print(f"\n  A:       {e['dest']}")
    print(f"  OGGETTO: {e['oggetto']}")
    print("  " + "-" * 68)
    for r in e["corpo"].rstrip().split("\n"):
        print("  " + r)
    print("  " + "-" * 68)


def elenca(cur, lotto):
    cur.execute("SELECT progressivo, ente, pec, stato FROM radar.invio "
                "WHERE lotto = %s ORDER BY progressivo", (lotto,))
    righe = cur.fetchall()
    if not righe:
        sys.exit(f"lotto '{lotto}' non registrato")
    print(f"=== lotto '{lotto}' ===\n")
    for p, ente, pec, st in righe:
        marca = " . " if st == "da_inviare" else "   "
        print(f" {marca}{p:2d}. {(ente or '?')[:44]:46s} {st:18s} {pec}")
    print(f"\n  inviate oggi: {inviate_oggi(cur)}/"
          f"{conf('PEC_MAX_GIORNO', False) or MAX_GIORNO}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lotto", default="pec")
    ap.add_argument("--invia", metavar="N[,N|N-N]")
    ap.add_argument("--prova", action="store_true",
                    help="mostra cosa partirebbe, senza spedire")
    ap.add_argument("--forza", action="store_true",
                    help="rimanda anche una gia' inviata")
    ap.add_argument("--stato", action="store_true")
    ap.add_argument("--verifica", action="store_true",
                    help="prova connessione e credenziali senza spedire")
    ap.add_argument("--sollecito", action="store_true",
                    help="manda il secondo contatto invece del primo")
    a = ap.parse_args()

    if a.verifica:
        try:
            utente, nome = verifica_accesso()
        except RuntimeError as e:
            sys.exit(f"accesso NON riuscito.\n\n{e}")
        print(f"accesso riuscito: {nome} <{utente}>")
        print("TLS verificato e credenziali accettate. Nessun messaggio "
              "inviato.")
        return

    dsn = leggi_dsn()
    if not dsn:
        sys.exit("connessione non configurata: vedi ingestion/.env.local")

    with psycopg.connect(dsn, connect_timeout=30) as pg, pg.cursor() as cur:
        if a.stato or not a.invia:
            elenca(cur, a.lotto)
            if not a.invia:
                return

        elenco = numeri(a.invia)

        # Con piu' di una si chiede conferma una volta sola, mostrando a chi.
        if len(elenco) > 1 and not a.prova:
            print(f"Stai per inviare {len(elenco)} PEC dal lotto "
                  f"'{a.lotto}':\n")
            for n in elenco:
                try:
                    _, ente, pec, st, _ = riga(cur, a.lotto, n)
                    print(f"  {n:2d}. {(ente or '?')[:42]:44s} {pec}"
                          + ("   [GIA' INVIATA]" if st in GIA_PARTITE else ""))
                except LookupError as err:
                    print(f"  {n:2d}. {err}")
            if input("\nConfermi? scrivi INVIA: ").strip() != "INVIA":
                sys.exit("annullato.")
            print()

        for n in elenco:
            try:
                e = spedisci(cur, a.lotto, n, prova=a.prova, forza=a.forza,
                             sollecito=a.sollecito)
            except (LookupError, FileNotFoundError, ValueError) as err:
                print(f"  {n:2d}. NON INVIATA — {err}")
                continue
            except RuntimeError as err:
                pg.rollback()
                sys.exit(f"  {n:2d}. errore SMTP: {err}\n"
                         f"  interrotto: le successive non sono partite.")

            if a.prova:
                print(f"\n  {n:2d}. {(e['ente'] or '?')[:44]} -> {e['dest']}")
                for p in e["problemi"]:
                    print(f"      ! {p}")
                mostra(e)
            elif e["problemi"]:
                print(f"  {n:2d}. {(e['ente'] or '?')[:40]:42s} NON INVIATA")
                for p in e["problemi"]:
                    print(f"      - {p}")
            else:
                pg.commit()
                print(f"  {n:2d}. {(e['ente'] or '?')[:40]:42s} inviata  "
                      f"{e['message_id']}")

        if a.prova:
            print("\n  prova: nessun messaggio e' partito.")


if __name__ == "__main__":
    main()
