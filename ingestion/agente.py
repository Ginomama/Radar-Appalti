#!/usr/bin/env python3
"""
L'agente con cui parlare (task R39), prima versione: solo domande, mai scritture.

COME FUNZIONA. Due chiamate a un modello economico (Haiku), niente libreria
'anthropic' — stessa scelta di notifica.py e ted_verdetto.py, solo urllib:

  1. la domanda in italiano + una descrizione dello schema -> il modello
     scrive UNA query SELECT che dovrebbe rispondere, oppure dice che non e'
     possibile con questi dati.
  2. la query gira (in sola lettura, vedi sotto) e il risultato torna al
     modello, che scrive la risposta finale in italiano colloquiale.

PERCHE' TEXT-TO-SQL E NON UNA MANCIATA DI DOMANDE PRECONFEZIONATE. Le domande
possibili sono troppe e troppo varie ("quanti bandi TED aperti", "chi tiene
il Comune di Brescia", "quanto vale il funnel a ottobre") per un elenco
chiuso di query. Lo schema e' piccolo e documentato (SCHEMA sotto): il
modello lo legge una volta per domanda, non esplora il database da solo.

SICUREZZA — TRE LIVELLI, NON UNO SOLO:
  1. sicura(sql) rifiuta tutto cio' che non comincia per SELECT/WITH, che
     contiene una parola chiave di scrittura, o piu' di uno statement.
  2. La query gira dentro una transazione SET TRANSACTION READ ONLY: anche
     se il livello 1 avesse un buco, Postgres stesso rifiuta la scrittura.
  3. La connessione non fa mai commit e si chiude sempre in rollback.

Non e' un filtro di liceita' (R3): quello riguarda i CONTATTI degli enti,
qui si leggono dati gia' tutti visibili in console, in sola lettura.

Uso:
    python agente.py "quanti bandi TED sono aperti oggi?"

Dipendenza: psycopg. Anthropic via urllib, nessun pacchetto esterno.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

import psycopg
from push_supabase import leggi_dsn

QUI = os.path.dirname(os.path.abspath(__file__))
ENVFILE = os.path.join(QUI, ".env.local")
API = "https://api.anthropic.com/v1/messages"
MODELLO = "claude-haiku-4-5-20251001"

RIGHE_MAX = 200
TIMEOUT_QUERY_MS = 8000

# Solo le tabelle/viste gia' pubbliche in console — niente di che non sia
# gia' leggibile da chi apre la dashboard. cf_ente/codice_fiscale sono
# identificativi pubblici (ANAC), non dati personali.
SCHEMA = """
radar.scadenze — contratti IT della PA in scadenza nei prossimi 12 mesi
  (cig, ente, provincia, cf_ente, data_termine_contrattuale, importo_aggiudicazione,
   fornitore_uscente, categoria, pec, punteggio, prob_apertura, valore_atteso)

radar.ente — riepilogo per ente (un ente = una riga)
  (cf_ente, ente, provincia, lotti, gare, importo_bandito, importo_aggiudicato,
   gare_competitive, ribasso_medio_competitive, concorrenti_medi, lotti_pnrr)

radar.competitor — riepilogo per fornitore vincitore (un fornitore = una riga)
  (codice_fiscale, vincitore, gare_vinte, valore_vinto, gare_competitive,
   ribasso_medio_competitive, concorrenti_medi, enti_serviti,
   province_presidiate, prima_gara, ultima_gara)

radar.esito — che fine hanno fatto le scadenze gia' passate
  (cig, cf_ente, ente, provincia, oggetto, data_termine, importo,
   fornitore_uscente, fornitore_subentrante, esito, contendibile, punteggio)
  esito tipici: 'riaggiudicato_stesso' | 'riaggiudicato_altro' | 'sospeso' | ...

radar.ted — bandi di gara europei (TED), aperti e chiusi
  (numero, titolo, ente, citta, cf_ente, pubblicato, scadenza, cpv, tipo,
   natura, valore, oggetto, link, verdetto, verdetto_motivo)
  per i soli bandi ancora aperti: WHERE scadenza >= current_date
  verdetto: parere AI si'/forse/no su se vale la pena risponderci

radar.ente_fornitore_dominante — il fornitore che vale di piu' nello storico
  di ogni ente (chi bisogna spostare per entrare), un ente = una riga
  (cf_ente, ente, prov, cf_fornitore, fornitore, contratti, valore, ultimo)

radar.anomalia_importo — contratti con importo sproporzionato rispetto al
  resto dello storico dello stesso ente, probabili errori ANAC
  (cig, cf_ente, ente, prov, fornitore, importo, secondo_importo, rapporto)

radar.invio — le PEC mandate agli enti e a che punto sono
  (lotto, progressivo, ente, provincia, pec, n_contratti, stato,
   inviata_il, risposta_il, note, accettata_il, consegnata_il,
   errore_consegna, sollecitata_il, discovery_fissata_il, discovery_fatta_il,
   offerta_il, vendita_il, persa_il, motivo_perdita, valore_offerta,
   valore_vendita)
  stato tipici: 'da_inviare' | 'inviata' | 'risposta' | 'nessuna_risposta' |
                'non_consegnata' | 'chiusa'
"""

PROMPT_SQL = """Sei l'assistente dati di Radar Appalti, uno strumento che segue \
contratti IT della PA in scadenza e le PEC mandate per proporsi prima del rinnovo.

Schema disponibile (solo queste tabelle, tutte nello schema "radar"):
{schema}

Domanda dell'utente: {domanda}

Scrivi UNA sola query SELECT (Postgres) che risponda alla domanda, usando SOLO \
le tabelle sopra. Se la domanda non si puo' rispondere con questi dati, non \
scrivere SQL: rispondi con una riga "IMPOSSIBILE: <perche', una frase>".

Nomi di enti e fornitori nei dati sono in MAIUSCOLO e possono avere forme \
leggermente diverse (es. "COMUNE DI BRESCIA" invece di "Comune di Brescia"): \
confronta sempre con ILIKE e '%parola%', mai con '='.

Se scrivi SQL: nient'altro nella risposta, solo il codice, in un blocco \
```sql ... ```. Metti sempre un LIMIT (100 righe massimo se non specificato \
altrimenti dalla domanda)."""

PROMPT_RISPOSTA = """Sei l'assistente dati di Radar Appalti.

Domanda dell'utente: {domanda}

Hai eseguito questa query:
{sql}

Risultato ({n} righe, eventualmente troncato):
{righe}

Rispondi alla domanda in italiano, colloquiale e diretto, 2-4 frasi, basandoti \
SOLO su questi dati. Se le righe sono zero, dillo chiaramente invece di \
inventare. Se il risultato e' un elenco, riportane solo i primi esempi utili, \
non tutta la tabella."""

VIETATE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|"
    r"call|execute|vacuum|pg_sleep|pg_read_file|pg_write_file|dblink|"
    r"into\s+\w)\b", re.I)


def conf(chiave):
    v = os.environ.get(chiave)
    if v:
        return v.strip()
    if not os.path.exists(ENVFILE):
        return None
    trovato = None
    with open(ENVFILE, encoding="utf-8") as f:
        for riga in f:
            riga = riga.strip()
            if riga.startswith("#") or "=" not in riga:
                continue
            k, _, val = riga.partition("=")
            if k.strip() == chiave:
                trovato = val.strip().strip('"').strip("'")
    return trovato or None


def chiama(prompt, chiave, max_tokens=600):
    dati = json.dumps({
        "model": MODELLO,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(API, data=dati, headers={
        "x-api-key": chiave,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        risposta = json.loads(r.read())
    return risposta["content"][0]["text"]


def sicura(sql):
    """True/messaggio: la query e' un SELECT solo, uno statement solo, senza
    parole di scrittura. E' il primo dei tre livelli, vedi il docstring del
    modulo — non e' l'unico."""
    sql = sql.strip()
    nudo = sql[:-1].strip() if sql.endswith(";") else sql
    if ";" in nudo:
        return False, "piu' di uno statement"
    if not re.match(r"^\s*(select|with)\b", nudo, re.I):
        return False, "non comincia con SELECT/WITH"
    if VIETATE.search(nudo):
        return False, "contiene una parola chiave non permessa"
    return True, nudo


def estrai_sql(testo):
    """(sql, None) se ha trovato un blocco ```sql```, (None, motivo) se il
    modello ha rifiutato con IMPOSSIBILE, (None, None) se non riconosce ne'
    l'uno ne' l'altro nella risposta."""
    m = re.search(r"```sql\s*(.+?)```", testo, re.S | re.I)
    if m:
        return m.group(1).strip(), None
    m = re.search(r"IMPOSSIBILE:\s*(.+)", testo, re.I)
    if m:
        return None, m.group(1).strip()
    return None, None


def esegui(dsn, sql):
    if "limit" not in sql.lower():
        sql += f" LIMIT {RIGHE_MAX}"
    with psycopg.connect(dsn, connect_timeout=20) as pg, pg.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(f"SET LOCAL statement_timeout = {TIMEOUT_QUERY_MS}")
        cur.execute(sql)
        cols = [d.name for d in cur.description] if cur.description else []
        righe = cur.fetchmany(RIGHE_MAX)
        pg.rollback()          # mai un commit: sola lettura anche lato app
    return cols, righe


def formatta(cols, righe):
    if not righe:
        return "(nessuna riga)"
    testa = " | ".join(cols)
    corpo = "\n".join(" | ".join(str(v) if v is not None else "—" for v in r)
                      for r in righe[:50])
    extra = f"\n… e altre {len(righe) - 50} righe" if len(righe) > 50 else ""
    return f"{testa}\n{corpo}{extra}"


def chiedi(dsn, domanda, chiave):
    """Ritorna dict(risposta, sql, righe) — sql/righe None se non e' servita
    una query (domanda fuori scope, o il modello ha rifiutato)."""
    testo = chiama(PROMPT_SQL.format(schema=SCHEMA, domanda=domanda), chiave,
                   max_tokens=400)
    sql_grezzo, motivo_no = estrai_sql(testo)
    if sql_grezzo is None:
        return dict(risposta=motivo_no or
                    "Non sono riuscito a interpretare la domanda con i dati disponibili.",
                    sql=None, righe=0)

    ok, esito = sicura(sql_grezzo)
    if not ok:
        return dict(risposta=f"La query generata non e' sicura ({esito}): non eseguita.",
                    sql=sql_grezzo, righe=0)
    sql = esito

    try:
        cols, righe = esegui(dsn, sql)
    except Exception as e:
        return dict(risposta=f"La query non e' andata a buon fine: {e}",
                    sql=sql, righe=0)

    risposta = chiama(PROMPT_RISPOSTA.format(
        domanda=domanda, sql=sql, n=len(righe), righe=formatta(cols, righe)),
        chiave, max_tokens=400)
    return dict(risposta=risposta.strip(), sql=sql, righe=len(righe))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("domanda", nargs="+")
    a = ap.parse_args()

    dsn = leggi_dsn()
    if not dsn:
        sys.exit("connessione non configurata: vedi ingestion/.env.local")
    chiave = conf("ANTHROPIC_API_KEY")
    if not chiave:
        sys.exit("ANTHROPIC_API_KEY mancante in .env.local")

    d = chiedi(dsn, " ".join(a.domanda), chiave)
    if d["sql"]:
        print(f"[SQL] {d['sql']}\n")
    print(d["risposta"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as e:
        sys.exit(f"Anthropic non raggiungibile: {e}")
