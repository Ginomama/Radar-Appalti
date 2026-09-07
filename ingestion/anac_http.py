"""
Accesso HTTP ad ANAC — le trappole in un posto solo.

Verificate il 2026-08-23, dettaglio in docs/fonti-dati.md:

 1. WAF: filtra sullo User-Agent. 'Mozilla/5.0' viene rifiutato, serve uno UA
    browser completo. E blocca gli IP dei cloud provider: questo modulo deve
    girare su un runner residenziale.
 2. HEAD: ritorna 200 con content-length fasullo (~270 byte). Per sondare
    la disponibilita' si usa GET con header Range.

 3. TLS — attenzione, NON e' un problema di ANAC.
    Su macchine con un antivirus che fa ispezione TLS (verificato con Avast
    Web/Mail Shield) il certificato presentato e' rigenerato localmente e la
    CA dell'antivirus ha Basic Constraints non marcati 'critical'. Da Python
    3.13 VERIFY_X509_STRICT e' attivo di default e rifiuta la connessione.
    Colpisce quasi tutti gli host HTTPS, non solo ANAC: sulla macchina di
    sviluppo fallisce anche pypi.org.
    Su un runner senza antivirus intercettante il problema non si presenta.
    Percio' qui si prova PRIMA con la verifica standard, e solo se fallisce
    si ripiega sul contesto rilassato, avvisando una volta sola. Cosi' la
    produzione gira in modalita' stretta e non eredita un workaround che
    non le serve.

Solo libreria standard.
"""

import json
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_API = "https://dati.anticorruzione.it/opendata/api/3/action"
BASE_DL = "https://dati.anticorruzione.it/opendata/download/dataset"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

SSLCTX = ssl.create_default_context()            # standard, verifica stretta

_RILASSATO = ssl.create_default_context()        # ripiego, vedi trappola 3
_RILASSATO.verify_flags &= ~ssl.VERIFY_X509_STRICT

_avvisato = False


def _req(url, extra=None):
    h = {"User-Agent": UA, "Accept": "*/*"}      # trappola 1
    if extra:
        h.update(extra)
    return Request(url, headers=h)


def apri(req, timeout=60):
    """
    urlopen con verifica stretta; ripiega sul contesto rilassato solo se la
    catena viene rifiutata, e lo dice. Vedi trappola 3 nel docstring.
    """
    global _avvisato
    try:
        return urlopen(req, timeout=timeout, context=SSLCTX)
    except URLError as e:
        if not isinstance(e.reason, ssl.SSLCertVerificationError):
            raise
        if not _avvisato:
            print("[TLS] verifica stretta fallita — probabile antivirus con "
                  "ispezione TLS su questa macchina. Ripiego sul contesto "
                  "rilassato (hostname e catena restano verificati).",
                  file=sys.stderr)
            _avvisato = True
        return urlopen(req, timeout=timeout, context=_RILASSATO)


def package_list():
    """Elenco dei dataset reali. Non indovinare gli URL: chiedi al catalogo."""
    with apri(_req(f"{BASE_API}/package_list")) as r:
        return json.loads(r.read())["result"]


def package_show(nome):
    """Metadati di un dataset, incluse le risorse con URL esatti."""
    with apri(_req(f"{BASE_API}/package_show?id={nome}")) as r:
        return json.loads(r.read())["result"]


def risorse_csv(nome):
    """[(nome_risorsa, url)] dei soli CSV di un dataset, log tecnici esclusi."""
    return [(x["name"], x["url"])
            for x in package_show(nome).get("resources", [])
            if (x.get("format") or "").upper() == "CSV"
            and not x["name"].endswith("_logCsv")]


def testa(url):
    """(status, dimensione, last_modified) senza scaricare. GET+Range, mai HEAD."""
    try:
        with apri(_req(url, {"Range": "bytes=0-100"})) as r:
            cr = r.headers.get("content-range") or ""
            dim = int(cr.split("/")[-1]) if "/" in cr else None
            return r.status, dim, r.headers.get("last-modified")
    except HTTPError as e:
        return e.code, None, None
    except URLError:
        return None, None, None


TENTATIVI = 3


def scarica(url, dest, forza=False):
    """
    Scarica in cache locale. Ritorna (byte, last_modified, http_status).
    Se il file c'e' gia' non riscarica: rifare i download a ogni run e' inutile
    e ti fa notare dal WAF.

    Il confronto con Content-Length non e' pedanteria. Su file da 100+ MB la
    connessione cade a meta' senza sollevare niente: r.read() restituisce b''
    e il ciclo finisce come se avesse finito. Il risultato e' uno ZIP con
    l'header giusto e la coda mancante, che poi viene diagnosticato come
    'ZIP corrotto' — un errore che manda a cercare il problema dalla parte
    sbagliata. Peggio: senza il controllo, il file mozzo finisce in cache e
    passa il test 'esiste ed e' grande', quindi ogni ritentativo restituisce
    lo stesso file rotto senza riscaricare niente.
    """
    import os
    if not forza and os.path.exists(dest) and os.path.getsize(dest) > 1_000_000:
        return os.path.getsize(dest), None, 200

    ultimo = None
    for tentativo in range(1, TENTATIVI + 1):
        try:
            with apri(_req(url), timeout=900) as r, open(dest, "wb") as f:
                lm = r.headers.get("last-modified")
                atteso = r.headers.get("content-length")
                atteso = int(atteso) if atteso and atteso.isdigit() else None
                tot = 0
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
                    tot += len(b)
            if atteso is None or tot >= atteso:
                return tot, lm, 200
            # Troncato: si cancella, altrimenti la cache lo promuove a buono.
            os.remove(dest)
            ultimo = (f"scaricati {tot:,} byte su {atteso:,} "
                      f"({tot / atteso * 100:.0f}%)")
            print(f" [troncato, tentativo {tentativo}/{TENTATIVI}: {ultimo}]",
                  end="", flush=True)
        except HTTPError as e:
            return 0, None, e.code
        except URLError as e:
            ultimo = str(e)
            if os.path.exists(dest):
                os.remove(dest)
            print(f" [rete, tentativo {tentativo}/{TENTATIVI}]",
                  end="", flush=True)
    print(f" [{ultimo}]", end="")
    return 0, None, None
