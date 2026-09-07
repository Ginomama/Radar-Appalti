"""Chi sta firmando il certificato delle porte PEC (task R15b).

IL PROBLEMA CHE SPIEGA. Le ricevute PEC non si leggevano e l'errore era
'CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate'. Letto
cosi' sembra un problema di Aruba, o della catena di certificati di Python:
si perde mezza giornata a reinstallare certifi.

Non e' nessuna delle due cose. Avast Mail Shield si mette in mezzo alla
connessione, rifa' il certificato con una CA sua, e lo firma con la radice
che chiama 'Untrusted Root' — una CA che di proposito NON e' nel deposito
certificati di Windows, cosi' nessun client puo' verificarla.

La prova che non c'entra Aruba: sulla 993 succede lo stesso con Gmail. E
sulla 443, dove lavora il Web Shield invece del Mail Shield, lo stesso Avast
firma con 'Avast Web/Mail Shield Root', che nel deposito c'e'.

    porta 443  ->  Avast Web/Mail Shield Root             (nel deposito)
    porta 993  ->  Avast Web/Mail Shield Untrusted Root   (fuori, apposta)

Quindi non e' un problema da risolvere nel codice: finche' lo Scudo posta
analizza le connessioni protette, nessun programma su questa macchina puo'
verificare un server IMAP. Si sistema nelle impostazioni di Avast — vedi
docs/setup-pec.md, sezione "Avast".

COSA NON FARE. La scorciatoia sarebbe disattivare la verifica del
certificato, o aggiungere la radice 'Untrusted' al deposito. Sono la stessa
cosa: si spegne l'unica difesa contro qualcuno che si mette in mezzo alla
connessione. Su un canale che porta credenziali PEC non si fa. Meglio
ricevute non lette che una casella PEC compromessa.

Uso:
    python diagnosi_tls.py
    python diagnosi_tls.py --host imaps.pec.aruba.it --porta 993

Solo libreria standard, piu' 'cryptography' se c'e' (per leggere l'emittente
in chiaro; senza, si arrangia con quello che espone ssl).
"""

import argparse
import socket
import ssl
import sys

# Le due porte che servono al Radar, piu' una 443 di controllo: e' la 443 che
# fa capire se il problema e' generale o solo della posta.
PROVE = [
    ("imaps.pec.aruba.it", 993, "IMAP — lettura delle ricevute"),
    ("smtps.pec.aruba.it", 465, "SMTP — invio delle PEC"),
    ("pec.aruba.it", 443, "HTTPS — controllo, non serve al Radar"),
]

INTERCETTATORI = ("avast", "avg", "kaspersky", "eset", "bitdefender",
                  "norton", "mcafee", "sophos", "fortinet", "zscaler",
                  "bluecoat", "netskope")


def emittente(der):
    """Il nome dell'emittente, in chiaro. Senza 'cryptography' si ripiega su
    una lettura grezza: basta per riconoscere un nome di antivirus."""
    try:
        from cryptography import x509
        c = x509.load_der_x509_certificate(der)
        return c.issuer.rfc4514_string(), c.subject.rfc4514_string()
    except ImportError:
        testo = der.decode("latin-1")
        pezzi = [p for p in testo.split("\x00") if p.isprintable() and len(p) > 6]
        return " / ".join(pezzi[:4]), ""


def prova(host, porta, timeout=12):
    d = {"host": host, "porta": porta}
    try:
        # Primo giro: la verifica vera, quella che fa il Radar.
        try:
            ctx = ssl.create_default_context()
            with ctx.wrap_socket(socket.create_connection((host, porta), timeout),
                                 server_hostname=host):
                d["verifica"] = "ok"
        except ssl.SSLCertVerificationError as e:
            d["verifica"] = f"fallita: {e.verify_message or e}"
        except ssl.SSLError as e:
            d["verifica"] = f"fallita: {e}"

        # Secondo giro: si guarda CHI ha firmato. Non si manda niente e non si
        # riusa questa connessione — serve solo a leggere il certificato.
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with ctx.wrap_socket(socket.create_connection((host, porta), timeout),
                             server_hostname=host) as s:
            em, sog = emittente(s.getpeercert(binary_form=True))
        d["emittente"] = em
        d["soggetto"] = sog
    except Exception as e:
        d["errore"] = f"{type(e).__name__}: {e}"
    return d


def chi_intercetta(em):
    b = (em or "").lower()
    for nome in INTERCETTATORI:
        if nome in b:
            return nome
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host")
    ap.add_argument("--porta", type=int, default=993)
    a = ap.parse_args()

    prove = [(a.host, a.porta, "richiesto da riga di comando")] if a.host else PROVE

    print("\n=== chi firma il certificato su queste porte ===\n")
    esiti = []
    for host, porta, perche in prove:
        d = prova(host, porta)
        esiti.append(d)
        print(f"  {host}:{porta}   {perche}")
        if "errore" in d:
            print(f"    non raggiungibile: {d['errore']}\n")
            continue
        buona = d["verifica"] == "ok"
        print(f"    verifica  : {'OK' if buona else d['verifica'][:96]}")
        print(f"    emittente : {d['emittente'][:96]}")
        chi = chi_intercetta(d["emittente"])
        if chi:
            print(f"    -> la connessione la sta rifacendo {chi.upper()}, "
                  f"non e' il certificato del server")
        print()

    # Il verdetto. Serve piu' del dettaglio: chi legge vuole sapere se puo'
    # leggere le ricevute, e se no di chi e' la colpa.
    rotte = [d for d in esiti
             if d.get("verifica", "").startswith("fallita")
             and d["porta"] in (993, 465)]
    if not rotte:
        print("  Tutto verificabile: il Radar puo' leggere le ricevute PEC.\n")
        return 0

    chi = next((chi_intercetta(d.get("emittente", "")) for d in rotte
                if chi_intercetta(d.get("emittente", ""))), None)
    print("  " + "-" * 66)
    if chi:
        print(f"  Le porte della PEC sono intercettate da {chi.upper()}.")
        print("  Nessun programma su questa macchina puo' verificare quei server")
        print("  finche' resta cosi'. Non e' un problema di Aruba ne' del codice:")
        print("  si sistema nelle impostazioni dell'antivirus.")
        print("\n  Come: docs/setup-pec.md, sezione 'Avast'.")
    else:
        print("  Verifica fallita ma nessun antivirus riconosciuto nell'emittente.")
        print("  Guarda il nome qui sopra: se e' un nome aziendale, la connessione")
        print("  passa da un proxy che rifa' i certificati.")
    print("  " + "-" * 66 + "\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
