#!/usr/bin/env python3
"""
Console del Radar in locale, con dati in diretta dal TUO Supabase.

Perche' esiste. La versione pubblicata su claude.ai non puo' interrogare il
database: i contenuti pubblicati hanno una policy che blocca le chiamate a
host esterni, e il connettore Supabase della sessione punta a un altro
account. In locale nessuno dei due vincoli esiste — la pagina e i dati
arrivano dallo stesso server, quindi si parla direttamente con Supabase.

Differenze rispetto alla versione pubblicata:
  - i dati sono sempre freschi, niente istantanea da rigenerare
  - segnare una PEC come inviata scrive dritto in radar.invio, quindi
    console e invii.py non si disallineano mai

La stessa pagina serve entrambe le modalita': se il payload incorporato e'
vuoto, il JavaScript lo chiede a /api/dati.

Uso:
    python console_live.py            # apre il browser su localhost:8420
    python console_live.py --porta 9000 --niente-browser

Dipendenza: psycopg. Il resto e' libreria standard.
"""

import argparse
import json
import os
import re
import sqlite3
import threading
import urllib.parse
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg

import scheda
from push_supabase import leggi_dsn, maschera

QUI = os.path.dirname(os.path.abspath(__file__))
PAGINA = os.path.join(QUI, "..", "docs", "console.html")

# Il browser ricarica spesso; senza cache ogni refresh e' un giro completo
# di query su un database che sul piano Free ha risorse modeste.
CACHE_SEC = 20
_cache = {"quando": None, "dati": None}
_lock = threading.Lock()


def raccogli(cur):
    D = {}
    cur.execute("""
        SELECT count(*), count(pec), count(categoria),
               count(*) FILTER (WHERE giorni_alla_scadenza <= 90),
               count(*) FILTER (WHERE giorni_alla_scadenza <= 365),
               sum(importo_aggiudicazione) FILTER (WHERE giorni_alla_scadenza <= 365)
        FROM radar.v_scadenze""")
    r = cur.fetchone()
    D["totali"] = dict(scadenze=r[0], con_pec=r[1], con_categoria=r[2],
                       a90=r[3], a365=r[4], valore365=float(r[5] or 0))
    cur.execute("SELECT count(*) FROM radar.competitor")
    D["totali"]["competitor"] = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM radar.ente")
    D["totali"]["enti"] = cur.fetchone()[0]

    cur.execute("""
        SELECT coalesce(categoria,'Non classificato'), count(*), sum(importo_aggiudicazione)
        FROM radar.v_scadenze WHERE giorni_alla_scadenza <= 365
        GROUP BY 1 ORDER BY 2 DESC""")
    D["categorie"] = [dict(nome=a, n=b, val=float(c or 0)) for a, b, c in cur.fetchall()]

    cur.execute("""
        SELECT lotto, progressivo, ente, provincia, pec, n_contratti,
               stato, inviata_il, risposta_il, note, cig_inclusi,
               consegnata_il, errore_consegna, sollecitata_il
        FROM radar.invio ORDER BY lotto, progressivo""")
    D["invii"] = [dict(lotto=a, n=b, ente=c, prov=d, pec=e, contratti=f, stato=g,
                       inviata=str(h) if h else None,
                       risposta=str(i) if i else None, note=j, cig=k,
                       consegnata=str(l)[:16] if l else None,
                       errore=m,
                       sollecitata=str(o) if o else None)
                  for a, b, c, d, e, f, g, h, i, j, k, l, m, o in cur.fetchall()]

    cur.execute("""
        SELECT cig, ente, provincia, categoria, data_termine_contrattuale,
               importo_aggiudicazione, fornitore_uscente, pec, left(oggetto_lotto,120),
               punteggio, prob_apertura, cf_ente
        FROM radar.v_scadenze
        WHERE fornitore_persona_fisica = 0 AND pec IS NOT NULL
          AND giorni_alla_scadenza <= 365
        -- R18: prima quelli che valgono. L'importo da solo metteva in cima
        -- contratti enormi e irraggiungibili; la data metteva in cima quelli
        -- che scadono presto anche se verranno rinnovati in silenzio.
        ORDER BY punteggio DESC NULLS LAST,
                 importo_aggiudicazione DESC NULLS LAST LIMIT 400""")
    D["lead"] = [dict(cig=a, ente=b, prov=c, cat=d, scad=str(e), imp=float(f or 0),
                      forn=g, pec=h, ogg=i, pt=j, pr=float(k or 0), cf=l)
                 for a, b, c, d, e, f, g, h, i, j, k, l in cur.fetchall()]

    cur.execute("""
        SELECT vincitore, gare_vinte, valore_vinto, enti_serviti, ribasso_medio_competitive
        FROM radar.competitor WHERE valore_vinto IS NOT NULL
        ORDER BY valore_vinto DESC LIMIT 15""")
    D["competitor"] = [dict(nome=a, gare=b, val=float(c or 0), enti=d,
                            rib=float(e) if e is not None else None)
                       for a, b, c, d, e in cur.fetchall()]

    cur.execute("""
        SELECT provincia, fornitore_uscente, count(*), sum(importo_aggiudicazione)
        FROM radar.v_scadenze
        WHERE provincia IS NOT NULL AND fornitore_uscente IS NOT NULL
          AND fornitore_uscente NOT ILIKE '%INESISTENTE%'
          AND giorni_alla_scadenza <= 365
        GROUP BY 1,2 ORDER BY 1, count(*) DESC""")
    terr = {}
    for prov, forn, n, val in cur.fetchall():
        terr.setdefault(prov, []).append(dict(nome=forn[:46], n=n, val=float(val or 0)))
    D["territorio"] = sorted(
        [dict(prov=p, tot=sum(x["n"] for x in v), forn=v[:5])
         for p, v in terr.items() if sum(x["n"] for x in v) >= 4],
        key=lambda x: -x["tot"])[:40]

    D["generato"] = datetime.now().isoformat(timespec="seconds")
    return D


def dati(dsn):
    with _lock:
        ora = datetime.now()
        if _cache["dati"] and (ora - _cache["quando"]).total_seconds() < CACHE_SEC:
            return _cache["dati"]
        with psycopg.connect(dsn, connect_timeout=20) as pg, pg.cursor() as cur:
            d = raccogli(cur)
        # Gli 8 record corrotti alla fonte ANAC portano U+FFFD, che rompe la
        # serializzazione a valle: si tolgono qui, una volta.
        d = json.loads(json.dumps(d, ensure_ascii=False).replace("�", ""))
        _cache.update(quando=ora, dati=d)
        return d


def segna(dsn, lotto, n, stato, quando):
    campo = {"inviata": "inviata_il", "risposta": "risposta_il"}.get(stato)
    sql = "UPDATE radar.invio SET stato = %s"
    par = [stato]
    if campo:
        sql += f", {campo} = %s"
        par.append(quando or date.today().isoformat())
    sql += " WHERE lotto = %s AND progressivo = %s"
    par += [lotto, n]
    with psycopg.connect(dsn, connect_timeout=20) as pg, pg.cursor() as cur:
        cur.execute(sql, par)
        tocc = cur.rowcount
        pg.commit()
    with _lock:                       # il prossimo giro rilegge
        _cache["dati"] = None
    return tocc


def manda_pec(dsn, lotto, n, prova, forza):
    """Anteprima (prova=True) o invio vero di una PEC gia' generata.

    L'import e' qui e non in cima di proposito: se un giorno manca la
    configurazione PEC o cambia qualcosa in pec_smtp, la console continua a
    mostrare i dati — si spegne solo il bottone di invio.
    """
    import pec_smtp
    with psycopg.connect(dsn, connect_timeout=30) as pg, pg.cursor() as cur:
        e = pec_smtp.spedisci(cur, lotto, int(n), prova=prova, forza=forza)
        # Commit solo se e' partita davvero: l'anteprima non deve lasciare
        # tracce, e una spedizione fallita non deve marcare la riga.
        if e.get("inviata"):
            pg.commit()
        else:
            pg.rollback()
    if e.get("inviata"):
        with _lock:                   # il prossimo giro rilegge lo stato
            _cache["dati"] = None
    return e


def pagina_html():
    with open(PAGINA, encoding="utf-8") as f:
        h = f.read()
    # Svuota il payload incorporato: e' il segnale che dice al JavaScript
    # di chiedere i dati a /api/dati invece di usare l'istantanea.
    h = re.sub(r'(<script id="dati" type="application/json">).*?(</script>)',
               lambda m: m.group(1) + m.group(2), h, count=1, flags=re.S)
    return ("<!doctype html>\n<html lang=\"it\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
            + h + "\n</body>\n</html>")


def scheda_ente(dsn, cf):
    """R19 — la scheda, per la console.

    Lo storico ANAC sta in SQLite (su Supabase vanno solo i derivati), i
    contatti PEC stanno su Supabase: la scheda e' l'unico punto del sistema
    che deve leggere da tutte e due."""
    cf = (cf or "").strip()
    if not cf:
        return {"errore": "manca il codice fiscale dell'ente"}
    cx = sqlite3.connect(scheda.DB)
    cx.row_factory = sqlite3.Row
    try:
        d = scheda.scheda(cx, cf)
    finally:
        cx.close()
    if not d:
        return {"errore": f"nessun ente con codice fiscale {cf}"}

    # I contatti non devono far fallire la scheda: se Supabase non risponde,
    # il resto e' comunque quello che serve prima di una chiamata.
    try:
        with psycopg.connect(dsn, connect_timeout=15) as pg, pg.cursor() as cur:
            # radar.invio non ha il codice fiscale: identifica il
            # destinatario dalla PEC, che e' la stessa chiave su cui lavora
            # l'anti-duplicato (R20). Una PEC sola puo' servire piu' enti,
            # ed e' giusto vederli tutti: e' la casella che ha gia' ricevuto.
            cur.execute(
                "SELECT lotto, progressivo, stato, inviata_il, risposta_il, "
                "       note, consegnata_il, sollecitata_il "
                "FROM radar.invio WHERE pec = %s OR upper(ente) = %s "
                "ORDER BY lotto, progressivo",
                ((d.get("contatti") or {}).get("pec") or "-nessuna-",
                 (d.get("ente") or "").upper()))
            d["invii"] = [dict(lotto=a, n=b, stato=c, inviata=str(e) if e else None,
                               risposta=str(f) if f else None, note=g,
                               consegnata=str(h) if h else None,
                               sollecitata=str(i) if i else None)
                          for a, b, c, e, f, g, h, i in cur.fetchall()]
    except Exception as err:
        d["invii"] = None
        d["invii_errore"] = maschera(str(err), dsn)[:160]
    return d


def crea_handler(dsn):
    class H(BaseHTTPRequestHandler):
        def _invia(self, codice, corpo, tipo="application/json; charset=utf-8"):
            b = corpo.encode("utf-8") if isinstance(corpo, str) else corpo
            self.send_response(codice)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            try:
                # La query si legge PRIMA di spogliare il path: /api/ente ne ha
                # bisogno. Spogliarlo serve comunque, perche' un "/?x=1" —
                # da un segnalibro o da un ricarica forzata — cadrebbe nel 404.
                percorso, _, qs = self.path.partition("?")
                self.path = percorso
                if self.path in ("/", "/index.html"):
                    self._invia(200, pagina_html(), "text/html; charset=utf-8")
                elif self.path.startswith("/api/dati"):
                    self._invia(200, json.dumps(dati(dsn), ensure_ascii=False))
                elif self.path.startswith("/api/ente"):
                    cf = (urllib.parse.parse_qs(qs).get("cf") or [""])[0]
                    self._invia(200, json.dumps(scheda_ente(dsn, cf),
                                                ensure_ascii=False, default=str))
                else:
                    self._invia(404, '{"errore":"non trovato"}')
            except Exception as e:
                self._invia(500, json.dumps(
                    {"errore": maschera(str(e), dsn)[:300]}, ensure_ascii=False))

        def do_POST(self):
            try:
                if not (self.path.startswith("/api/invio")
                        or self.path.startswith("/api/pec")):
                    return self._invia(404, '{"errore":"non trovato"}')
                n = int(self.headers.get("Content-Length") or 0)
                c = json.loads(self.rfile.read(n) or b"{}")

                if self.path.startswith("/api/pec"):
                    prova = bool(c.get("prova"))
                    try:
                        e = manda_pec(dsn, c.get("lotto"), c.get("n"),
                                      prova, bool(c.get("forza")))
                    except (LookupError, FileNotFoundError,
                            ValueError, RuntimeError) as err:
                        # Errori attesi: il testo e' gia' leggibile e non
                        # contiene segreti (pec_smtp li maschera all'origine).
                        return self._invia(200, json.dumps(
                            {"errore": str(err)}, ensure_ascii=False))
                    self._invia(200, json.dumps(e, ensure_ascii=False))
                    try:
                        etichetta = ("anteprima" if prova else
                                     ("INVIATA" if e.get("inviata")
                                      else "bloccata"))
                        print(f"  PEC {c.get('lotto')}#{c.get('n')} -> "
                              f"{etichetta}"
                              + (f"  {e['message_id']}"
                                 if e.get("message_id") else ""))
                    except Exception:
                        pass
                    return

                tocc = segna(dsn, c.get("lotto"), int(c.get("n")),
                             c.get("stato"), c.get("data"))
                # Prima la risposta, poi il log: se stdout e' chiuso o
                # rediretto verso una pipe interrotta, print() solleva e
                # trasformerebbe una scrittura riuscita in un errore 500.
                self._invia(200, json.dumps({"aggiornate": tocc}))
                try:
                    print(f"  {c.get('lotto')}#{c.get('n')} -> {c.get('stato')}"
                          + ("" if tocc else "  (nessuna riga aggiornata)"))
                except Exception:
                    pass
            except Exception as e:
                self._invia(500, json.dumps(
                    {"errore": maschera(str(e), dsn)[:300]}, ensure_ascii=False))

        def log_message(self, *a):
            pass                       # il log di default e' rumore

    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--porta", type=int, default=8420)
    ap.add_argument("--niente-browser", action="store_true")
    a = ap.parse_args()

    dsn = leggi_dsn()
    if not dsn:
        raise SystemExit("connessione non configurata: vedi ingestion/.env.local")

    # Solo 127.0.0.1: la console mostra PEC e dati di contatto, non deve
    # affacciarsi sulla rete locale.
    srv = ThreadingHTTPServer(("127.0.0.1", a.porta), crea_handler(dsn))
    url = f"http://127.0.0.1:{a.porta}/"
    print(f"Console del Radar su {url}")
    print("dati in diretta dal database — Ctrl+C per fermare\n")
    if not a.niente_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nfermata.")


if __name__ == "__main__":
    main()
