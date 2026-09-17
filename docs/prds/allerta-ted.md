# ARD: Allerta TED

- **Status**: Draft
- **Obiettivo e KPI**: Notificare via Telegram, ogni giorno, i nuovi bandi pubblici
  italiani nei servizi IT pubblicati su TED (Tenders Electronic Daily, il
  portale UE degli appalti sopra soglia) — una fonte diversa da ANAC, con
  copertura diversa (sopra soglia UE) e tempistica diversa (pubblicazione
  ufficiale, non open data derivato). KPI: zero bandi rilevanti persi,
  zero duplicati rimandati.

## 0. Schema Flusso (ASCII)

```
[Schedule Trigger — ogni giorno 08:15]
        ↓
[HTTP Request — POST api.ted.europa.eu/v3/notices/search]
   query: classification-cpv=72* AND buyer-country=ITA
          AND publication-date>=ieri
        ↓
[Postgres — SELECT cig FROM radar.notificato WHERE canale='ted']
        ↓
[Filter — scarta i publication-number gia' notificati]
        ↓
   [Ci sono bandi nuovi?]
     ↓ Sì                    ↓ No
[Telegram — invia digest]  [Fine, nessun messaggio]
     ↓
[Postgres — INSERT in radar.notificato
 (cig=publication-number, canale='ted')]
        ↓
   [Fine]

Errore in qualsiasi nodo → workflow di errore esistente
("Flowline - Gestore Errori" / "Bot — Error Handler & Alert",
già attivi sull'istanza)
```

## 1. Architettura (Trigger & Sistemi)

- **Trigger**: Schedule Trigger n8n, ogni giorno alle 08:15 (15 minuti dopo
  `radar-giornaliero`, che manda il suo digest Telegram alle 08:00 — due
  messaggi separati, non in coda uno sull'altro).
- **TED Search API**: `https://api.ted.europa.eu/v3/notices/search`, `POST`.
  **Nessuna autenticazione richiesta** — verificato dal vivo il 17/09/2026
  con una chiamata reale (non solo dalla documentazione): l'API risponde
  con dati veri senza alcuna API key o header di auth. Nodo HTTP Request,
  Content-Type `application/json`.
- **Postgres**: stesso Supabase del radar (`radar.notificato`), via
  Credentials Manager n8n — credenziale ancora da creare (task B, non
  incluso in questo ARD: serve il DSN di produzione, che non va scritto
  qui in chiaro).
- **Telegram**: stesso bot già configurato per `notifica.py`
  (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` in `.env.local` sul lato
  Python) — va ricreato come credenziale separata nel Credentials Manager
  n8n, stesso token, perché n8n non legge `.env.local`.

## 2. Flusso Logico (Step-by-step)

1. **Schedule Trigger** parte alle 08:15.
2. **HTTP Request** interroga TED con query
   `classification-cpv=72* AND buyer-country=ITA AND publication-date>=<ieri, formato YYYYMMDD>`,
   campi richiesti: `publication-number`, `notice-title`, `buyer-name`,
   `publication-date`, `deadline-date-lot`, `links`. `scope: "ALL"`,
   `limit` generoso (es. 50 — il volume misurato è ~4-5/giorno per l'Italia,
   50 è un margine ampio, non un limite che si tocca mai in condizioni
   normali).
3. **Postgres** legge `SELECT cig FROM radar.notificato WHERE canale = 'ted'`
   — l'elenco di quello che e' gia' stato mandato (qui `cig` contiene il
   `publication-number` di TED, non un vero CIG: e' un riuso della colonna,
   non un'omonimia casuale — stessa tabella, stesso significato logico
   "identificativo di cosa e' stato notificato").
4. **Filter/Code**: tiene solo i bandi il cui `publication-number` NON e'
   nell'elenco del passo 3.
5. **If**: ci sono bandi nuovi?
   - **Sì** → **Telegram**: un messaggio digest (stesso stile di
     `notifica.py`: markdown, un blocco per bando con titolo in italiano
     quando `notice-title.ita` esiste — altrimenti il titolo in inglese —,
     ente, data pubblicazione, scadenza se presente, link al bando).
   - **No** → fine, nessun messaggio (stesso principio di `notifica.py`:
     niente rumore quando non c'e' niente di nuovo).
6. **Postgres**: `INSERT INTO radar.notificato (cig, canale, inviato_il)`
   una riga per ogni bando appena notificato, `canale = 'ted'`.

## 3. Data Processing & Mapping

- **Titolo**: `notice-title.ita` se presente, altrimenti `notice-title.eng`,
  altrimenti la prima lingua disponibile nell'oggetto (TED non garantisce
  l'italiano su ogni bando, verificato dal vivo: presente sulla maggior
  parte ma non tutte le notice controllate).
- **Ente**: `buyer-name` è un oggetto lingua→lista (es.
  `{"ita": ["CONSIP SPA"]}`) — si prende la prima voce della prima lingua
  disponibile.
- **Link**: da `links.html.ITA` quando c'e' (pagina bando in italiano),
  altrimenti `links.html.ENG` o la prima chiave disponibile.
- **Scadenza**: `deadline-date-lot` non è sempre presente (bandi tipo avviso
  preventivo non hanno una scadenza offerte) — quando assente, il digest
  scrive "scadenza non indicata" invece di ometterla in silenzio.
- **Deduplica**: chiave `publication-number` (es. `"622971-2026"`),
  univoco per notice TED — non serve normalizzazione.

## 4. Error Handling & Notifiche

- **HTTP Request fallisce o va in timeout** (TED irraggiungibile, risposta
  malformata): il nodo termina con errore → il workflow di errore globale
  già attivo sull'istanza (`Bot — Error Handler & Alert`) lo intercetta e
  avvisa, stessa disciplina già in uso per gli altri workflow attivi. Non
  si costruisce un secondo canale di errore per questo flusso.
- **Postgres irraggiungibile**: stesso trattamento — l'errore propaga al
  workflow di errore globale. Meglio un digest mancato con errore visibile
  che un digest duplicato per un fallimento silenzioso della deduplica.
- **Zero risultati da TED**: non è un errore, è un giorno senza bandi
  nuovi — nessuna notifica, nessun alert (vedi passo 5 del flusso logico).

## 5. Performance & Timing

- **Volume misurato dal vivo il 17/09/2026**: 31 notice (Italia, CPV 72*)
  in 7 giorni — circa 4-5 al giorno in media. Un digest giornaliero resta
  leggibile, non serve batching né paginazione oltre il primo `limit`.
- **Nessun rate limit documentato** dall'API TED per l'uso a una chiamata
  al giorno: non e' un problema a questo volume.
- **Query per data**: `publication-date>=<ieri>` invece di un intervallo
  fisso più ampio — la deduplica su `radar.notificato` è comunque la difesa
  vera contro i doppioni; la finestra di un giorno è solo per tenere la
  risposta dell'API piccola.
