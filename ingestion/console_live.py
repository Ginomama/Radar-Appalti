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

import job
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

    # Le colonne sono tante e crescono a ogni stadio del funnel: si nominano
    # una volta sola e si legge per nome. Con la tupla posizionale, aggiungere
    # una colonna in mezzo spostava silenziosamente tutti i campi dopo.
    COL = ["lotto", "progressivo", "ente", "provincia", "pec", "n_contratti",
           "stato", "inviata_il", "risposta_il", "note", "cig_inclusi",
           "consegnata_il", "errore_consegna", "sollecitata_il",
           "discovery_fissata_il", "discovery_fatta_il", "offerta_il",
           "vendita_il", "persa_il", "motivo_perdita",
           "valore_offerta", "valore_vendita", "esito_risposta"]
    RINOMINA = {"progressivo": "n", "provincia": "prov", "n_contratti": "contratti",
                "cig_inclusi": "cig", "errore_consegna": "errore",
                "motivo_perdita": "motivo", "esito_risposta": "esito"}
    cur.execute(f"SELECT {', '.join(COL)} FROM radar.invio "
                f"ORDER BY lotto, progressivo")
    D["invii"] = []
    for r in cur.fetchall():
        v = {}
        for nome, val in zip(COL, r):
            k = RINOMINA.get(nome, nome[:-3] if nome.endswith("_il") else nome)
            if nome in ("valore_offerta", "valore_vendita"):
                v[nome] = float(val) if val is not None else None
            elif nome == "consegnata_il":
                v[k] = str(val)[:16] if val else None
            elif nome.endswith("_il"):
                v[k] = str(val) if val else None
            else:
                v[k] = val
        D["invii"].append(v)

    # Indice CIG -> invio: una riga di invio puo' coprire piu' CIG in una
    # sola PEC (batch per ente), quindi si spacca cig_inclusi per collegare
    # ogni singolo bando alla PEC precisa che lo ha coperto (se c'e'). E'
    # piu' preciso del solo confronto per ente/PEC gia' usato altrove in
    # console.html: dice "questo bando specifico e' dentro quella spedizione
    # e sta a quel punto", non solo "questo ente e' gia' stato contattato".
    indice_cig = {}
    for i in D["invii"]:
        for cig in (i.get("cig") or "").split(","):
            cig = cig.strip()
            if cig:
                indice_cig[cig] = dict(lotto=i["lotto"], n=i["n"], stato=i["stato"],
                                       quando=i.get("inviata") or i.get("consegnata"))

    cur.execute("""
        SELECT cig, ente, provincia, categoria, data_termine_contrattuale,
               importo_aggiudicazione, fornitore_uscente, pec, left(oggetto_lotto,500),
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
                      forn=g, pec=h, ogg=i, pt=j, pr=float(k or 0), cf=l,
                      inv=indice_cig.get(a))
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

    # R5/R6 + collegamento in console del 18/09: gare europee ancora aperte,
    # con la scadenza per presentare offerta nel futuro — a differenza delle
    # scadenze sopra, che sono contratti gia' finiti e una previsione. Qui il
    # link porta al bando ufficiale, dove i requisiti esistono davvero.
    cur.execute("""
        SELECT numero, titolo, ente, scadenza, giorni_alla_scadenza,
               valore, link, ente_noto, cf_ente, verdetto, verdetto_motivo
        FROM radar.v_ted_aperte
        ORDER BY scadenza LIMIT 200""")
    D["ted"] = [dict(numero=a, titolo=b, ente=c, scad=str(d), gg=e,
                     val=float(f or 0), link=g, noto=h, cf=i,
                     verdetto=j, motivo=k)
               for a, b, c, d, e, f, g, h, i, j, k in cur.fetchall()]

    # R40/R41/R42 — stessa idea, tre fonti (sotto soglia, un marketplace per
    # regione). Raggruppato per regione fin da qui (non solo in console.html):
    # aggiungere una regione nuova e' una query in piu' con la sua chiave,
    # non una ristrutturazione del payload. Il nome della regione resta
    # fisso qui e non nella tabella, perche' ogni script di ingestion e'
    # gia' specifico per la sua regione.
    D["regionali"] = {}
    for nome_regione, vista in (("Marche", "radar.v_suam_aperti"),
                                 ("Emilia-Romagna", "radar.v_intercenter_aperti"),
                                 ("Toscana", "radar.v_start_aperti")):
        cur.execute(f"""
            SELECT codice, titolo, ente, scadenza, giorni_alla_scadenza,
                   importo, link, ente_noto, cf_ente, verdetto, verdetto_motivo
            FROM {vista}
            ORDER BY scadenza LIMIT 200""")
        D["regionali"][nome_regione] = [
            dict(codice=a, titolo=b, ente=c, scad=str(d), gg=e,
                 val=float(f or 0), link=g, noto=h, cf=i, verdetto=j, motivo=k)
            for a, b, c, d, e, f, g, h, i, j, k in cur.fetchall()]

    # R38 — poche righe (decine, non migliaia), sta nel payload principale
    # senza bisogno di un endpoint a parte come /api/dominanti.
    cur.execute("""
        SELECT cig, ente, prov, fornitore, importo, secondo_importo, rapporto
        FROM radar.anomalia_importo ORDER BY importo DESC""")
    D["anomalie"] = [dict(cig=a, ente=b, prov=c, fornitore=d, importo=float(e or 0),
                          secondo=float(f or 0) if f is not None else None,
                          rapporto=float(g) if g is not None else None)
                    for a, b, c, d, e, f, g in cur.fetchall()]

    D["generato"] = datetime.now().isoformat(timespec="seconds")
    D["auto_genera"] = leggi_stato_auto_genera()
    D["task"] = leggi_stato_task()
    return D


def chiedi_agente(dsn, domanda):
    """R39 — l'agente con cui parlare. Import qui e non in cima, stessa
    ragione di pec_smtp in manda_pec(): se manca ANTHROPIC_API_KEY o
    agente.py ha un problema, il resto della console continua a funzionare,
    si spegne solo la chat."""
    import agente
    chiave = agente.conf("ANTHROPIC_API_KEY")
    if not chiave:
        return dict(risposta="ANTHROPIC_API_KEY non configurata in .env.local: "
                    "la chat non puo' funzionare senza.", sql=None)
    try:
        return agente.chiedi(dsn, domanda, chiave)
    except Exception as e:
        return dict(risposta=f"Errore: {maschera(str(e), dsn)[:300]}", sql=None)


def cerca_dominanti(dsn, testo):
    """R36 — chi presidia quale ente, cercabile su tutti i 17mila.

    Fuori da dati(): 17mila righe nel payload iniziale rallenterebbero ogni
    apertura della console, anche per chi non apre mai questa sezione. Si
    cerca su richiesta, come /api/ente."""
    testo = (testo or "").strip()
    with psycopg.connect(dsn, connect_timeout=20) as pg, pg.cursor() as cur:
        if testo:
            cur.execute("""
                SELECT ente, prov, fornitore, contratti, valore, ultimo
                FROM radar.ente_fornitore_dominante
                WHERE ente ILIKE %s OR fornitore ILIKE %s
                ORDER BY valore DESC NULLS LAST LIMIT 100""",
                (f"%{testo}%", f"%{testo}%"))
        else:
            cur.execute("""
                SELECT ente, prov, fornitore, contratti, valore, ultimo
                FROM radar.ente_fornitore_dominante
                ORDER BY valore DESC NULLS LAST LIMIT 50""")
        return [dict(ente=a, prov=b, fornitore=c, contratti=d, valore=float(e or 0),
                     ultimo=str(f) if f else None)
               for a, b, c, d, e, f in cur.fetchall()]


def leggi_stato_task():
    """Interroga schtasks per i due job pianificati (R9).

    Non i log: quelli dicono se lo script e' andato a buon fine, questo dice
    se Windows lo ha lanciato per niente. E' cosi' che si e' scoperto il
    guasto del 16/09 sul task console-locale."""
    try:
        return job.stato_dict()
    except Exception:
        # schtasks non esiste fuori da Windows, o l'utente non ha ancora
        # installato i task: la console deve funzionare lo stesso.
        return []


def leggi_stato_auto_genera():
    """L'ultimo giro di auto_genera.py (R30), se l'ha mai scritto.

    E' un file, non una query: le bozze restano nel lotto finche' non
    vengono inviate, quindi contarle non direbbe se il job di stanotte e'
    partito davvero o se sono ferme li' da giorni."""
    percorso = os.path.join(QUI, "logs", "auto-genera-stato.json")
    try:
        with open(percorso, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


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


# stato -> colonna data. Stessa mappa di invii.py: console e riga di comando
# scrivono la stessa riga, e se divergessero il funnel conterebbe due volte.
DATE_STATO = {
    "inviata":           "inviata_il",
    "risposta":          "risposta_il",
    "discovery_fissata": "discovery_fissata_il",
    "discovery_fatta":   "discovery_fatta_il",
    "offerta":           "offerta_il",
    "vendita":           "vendita_il",
    "persa":             "persa_il",
}


def segna(dsn, lotto, n, stato, quando, motivo=None, valore=None, esito=None):
    campo = DATE_STATO.get(stato)
    sql = "UPDATE radar.invio SET stato = %s"
    par = [stato]
    if campo:
        sql += f", {campo} = %s"
        par.append(quando or date.today().isoformat())
    if motivo:
        sql += ", motivo_perdita = %s"
        par.append(motivo)
    if esito:
        # R14b, trovato con Regione Toscana: classifica la risposta una
        # volta, alla fonte, invece di far indovinare alla barra "cosa fare
        # adesso" se vale un richiamo leggendo (o ignorando) la nota libera.
        sql += ", esito_risposta = %s"
        par.append(esito)
    if valore is not None:
        # L'importo offerto e quello vinto sono numeri diversi: confonderli
        # falserebbe il tasso di conversione a valore.
        sql += (", valore_vendita = %s" if stato == "vendita"
                else ", valore_offerta = %s")
        par.append(valore)
    sql += " WHERE lotto = %s AND progressivo = %s"
    par += [lotto, n]
    with psycopg.connect(dsn, connect_timeout=20) as pg, pg.cursor() as cur:
        cur.execute(sql, par)
        tocc = cur.rowcount
        pg.commit()
    with _lock:                       # il prossimo giro rilegge
        _cache["dati"] = None
    return tocc


def manda_pec(dsn, lotto, n, prova, forza, sollecito=False):
    """Anteprima (prova=True) o invio vero di una PEC gia' generata.

    L'import e' qui e non in cima di proposito: se un giorno manca la
    configurazione PEC o cambia qualcosa in pec_smtp, la console continua a
    mostrare i dati — si spegne solo il bottone di invio.

    Con sollecito=True il testo non e' pre-generato da `genera_pec.py
    --solleciti`: lo scrive qui, fresco, con gli stessi dati che la riga ha
    in quel momento (`componi_sollecito`, la stessa funzione della riga di
    comando — un posto solo, non due testi che possono disallinearsi). Se il
    bottone e' comparso, la riga ha gia' passato i requisiti in JavaScript
    (consegnata, non ancora sollecitata, 12+ giorni); qui non si ricontrolla
    quello — lo ricontrolla `pec_smtp.spedisci` leggendo `sollecitata_il` da
    database, che e' la fonte di verita' e puo' essere cambiata da un'altra
    scheda aperta nel frattempo.
    """
    import pec_smtp
    with psycopg.connect(dsn, connect_timeout=30) as pg, pg.cursor() as cur:
        if sollecito:
            import genera_pec
            cur.execute(
                "SELECT ente, pec, cig_inclusi, inviata_il, file FROM radar.invio "
                "WHERE lotto = %s AND progressivo = %s", (lotto, int(n)))
            riga_db = cur.fetchone()
            if not riga_db:
                raise LookupError(f"{lotto}#{n} non trovato in radar.invio")
            ente, pec, cigs, inviata_il, nomefile = riga_db
            if not nomefile:
                raise RuntimeError(f"{lotto}#{n} non ha un file di origine registrato")
            oggetto, corpo = genera_pec.componi_sollecito(ente, pec, cigs, inviata_il)
            cartella = os.path.join(genera_pec.QUI, "..", "docs", lotto)
            os.makedirs(cartella, exist_ok=True)
            with open(os.path.join(cartella, "sollecito-" + nomefile),
                      "w", encoding="utf-8") as f:
                f.write(f"A:       {pec}\nOGGETTO: {oggetto}\n\n{'-'*70}\n\n{corpo}")
        e = pec_smtp.spedisci(cur, lotto, int(n), prova=prova, forza=forza,
                              sollecito=sollecito)
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


def genera_pec_ente(dsn, cf):
    """Bottone 'Genera PEC' sulla riga della tabella scadenze (R30).

    L'import e' qui e non in cima, come in manda_pec: se genera_pec.py
    cambia o manca una dipendenza, la console continua a mostrare i dati.
    Scrive nel lotto fisso 'pec-auto' — sia il bottone sia il job notturno
    ci aggiungono righe, mai svuotato ne' rigenerato.
    """
    import genera_pec
    with psycopg.connect(dsn, connect_timeout=30) as pg, pg.cursor() as cur:
        r = genera_pec.genera_singolo(cur, cf, lotto="pec-auto")
        if r.get("generato"):
            pg.commit()
        else:
            pg.rollback()
    if r.get("generato"):
        with _lock:                   # il prossimo giro rilegge
            _cache["dati"] = None
    return r


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
            # self.wfile e' un socket "grezzo" non bufferizzato (wbufsize=0):
            # .write() equivale a una singola socket.send(), che su risposte
            # grandi puo' inviare solo una parte senza segnalare errore
            # (troncamento silenzioso, sempre alla stessa soglia). sendall()
            # ripete finche' non e' tutto trasmesso.
            self.connection.sendall(b)

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
                elif self.path.startswith("/api/dominanti"):
                    q = (urllib.parse.parse_qs(qs).get("q") or [""])[0]
                    self._invia(200, json.dumps(cerca_dominanti(dsn, q),
                                                ensure_ascii=False, default=str))
                else:
                    self._invia(404, '{"errore":"non trovato"}')
            except Exception as e:
                self._invia(500, json.dumps(
                    {"errore": maschera(str(e), dsn)[:300]}, ensure_ascii=False))

        def do_POST(self):
            try:
                if not (self.path.startswith("/api/invio")
                        or self.path.startswith("/api/pec")
                        or self.path.startswith("/api/genera")
                        or self.path.startswith("/api/chiedi")):
                    return self._invia(404, '{"errore":"non trovato"}')
                n = int(self.headers.get("Content-Length") or 0)
                c = json.loads(self.rfile.read(n) or b"{}")

                if self.path.startswith("/api/chiedi"):
                    domanda = (c.get("domanda") or "").strip()
                    if not domanda:
                        return self._invia(200, json.dumps(
                            {"risposta": "Scrivi una domanda.", "sql": None},
                            ensure_ascii=False))
                    self._invia(200, json.dumps(chiedi_agente(dsn, domanda),
                                                ensure_ascii=False, default=str))
                    return

                if self.path.startswith("/api/genera"):
                    cf = (c.get("cf") or "").strip()
                    if not cf:
                        return self._invia(200, json.dumps(
                            {"generato": False, "motivo": "manca il codice fiscale"},
                            ensure_ascii=False))
                    try:
                        r = genera_pec_ente(dsn, cf)
                    except Exception as err:
                        return self._invia(200, json.dumps(
                            {"generato": False, "motivo": maschera(str(err), dsn)[:200]},
                            ensure_ascii=False))
                    self._invia(200, json.dumps(r, ensure_ascii=False))
                    try:
                        print(f"  genera-pec {cf} -> " + (
                            f"OK {r.get('ente')}" if r.get("generato")
                            else f"no: {r.get('motivo')}"))
                    except Exception:
                        pass
                    return

                if self.path.startswith("/api/pec"):
                    prova = bool(c.get("prova"))
                    sollecito = bool(c.get("sollecito"))
                    try:
                        e = manda_pec(dsn, c.get("lotto"), c.get("n"),
                                      prova, bool(c.get("forza")), sollecito)
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
                        print(f"  PEC {c.get('lotto')}#{c.get('n')}"
                              f"{' (sollecito)' if sollecito else ''} -> "
                              f"{etichetta}"
                              + (f"  {e['message_id']}"
                                 if e.get("message_id") else ""))
                    except Exception:
                        pass
                    return

                val = c.get("valore")
                tocc = segna(dsn, c.get("lotto"), int(c.get("n")),
                             c.get("stato"), c.get("data"),
                             c.get("motivo"),
                             float(val) if val not in (None, "") else None,
                             c.get("esito"))
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
