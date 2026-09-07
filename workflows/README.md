# Workflow n8n del Radar

Tre workflow, tutti con i nodi validati uno per uno e con le query **eseguite davvero su
Supabase**, non solo scritte.

| File | Cosa fa | Quando |
|---|---|---|
| `radar-alert-telegram.json` | manda su Telegram solo i lead **nuovi** | ogni giorno 08:00 |
| `radar-digest-email.json` | digest HTML completo per categoria | lunedì 08:15 |
| `radar-allerta-ted.json` | scarica le gare europee aperte e annuncia le nuove | ogni giorno 07:15 |

### Nota sull'Allerta TED

È l'unico dei tre che **prende i dati da fuori** invece di leggere quelli già in
Supabase, e l'unico che può girare su n8n Cloud senza problemi: TED non ha il WAF sugli
IP dei provider cloud che invece blocca ANAC.

Quattro cose sono lì apposta, e toglierle rompe il workflow in silenzio:

- la finestra è di **3 giorni** anche se gira ogni giorno — se un'esecuzione salta, 24 ore
  di avvisi si perdono per sempre;
- `paginationMode: "ITERATION"` nel corpo della richiesta — senza, si ottengono 100
  avvisi su 296 e nessun errore;
- `xmax = 0` nella `RETURNING` distingue gli inserimenti dagli aggiornamenti, se no ogni
  giorno si riannunciano gli stessi;
- si segna come annunciato **dopo** l'invio: se Telegram fallisce, l'avviso resta da fare.

Se non hai ancora la credenziale Telegram, **disattiva l'ultimo nodo e attiva lo stesso**:
`radar.ted` si riempie comunque, ed è quello che legge la console.

## ⚠️ Perché non sono già sulla tua istanza

L'**API key di n8n non è valida**. Il `health_check` risponde "connected", ma è un
controllo superficiale: ogni chiamata reale — elenco workflow, credenziali, creazione —
restituisce `AUTHENTICATION_ERROR`. Le chiavi di n8n Cloud scadono.

Per rigenerarla: n8n → il tuo avatar in alto a destra → **Settings** → **n8n API** →
crea una nuova key. Poi aggiornala dove è configurato l'MCP (variabile `N8N_API_KEY`).
Fatto quello, posso creare e aggiornare i workflow direttamente senza import manuali.

## Import manuale (funziona subito, senza sistemare la key)

Per ciascuno dei due file:

1. n8n → **Workflows** → **Add workflow** → menu `⋯` in alto a destra → **Import from File**
2. Seleziona il JSON
3. Apri i nodi marcati e completa quello che manca (sotto)
4. **Save**, poi attiva col toggle

## Cosa devi completare dopo l'import

### Credenziale Postgres → Supabase

Serve a entrambi i workflow. In n8n → **Credentials** → **New** → **Postgres**:

| Campo | Valore |
|---|---|
| Host | `aws-1-eu-west-1.pooler.supabase.com` |
| Database | `postgres` |
| User | `postgres.mbxtivcehntonhrslsge` — **col punto e il ref** |
| Password | quella del database Supabase |
| Port | `5432` |
| SSL | abilitato |

⚠️ Usa la **Session pooler**, non la connessione diretta: quest'ultima è solo IPv6 sul
piano Free e n8n Cloud non la raggiunge.

### Alert Telegram

- Credenziale **Telegram** col token di @BotFather
- Nel nodo *Invia alert*, sostituisci `INSERISCI_IL_TUO_CHAT_ID` col tuo chat id

### Digest Email

- Credenziale **SMTP** (con Gmail serve una password per le app, non quella dell'account)
- Nel nodo *Invia digest*, sostituisci `INSERISCI_MITTENTE` e `INSERISCI_DESTINATARIO`

## Scelte di progettazione

**La formattazione sta in SQL, non in un Code node.** Il messaggio Telegram e l'HTML
dell'email vengono composti con `string_agg` dentro la query. Rispetta la regola del
`CLAUDE.md` — niente Code node dove bastano i nativi — e tiene la logica dove stanno i
dati. Nessun nodo Aggregate, nessun nodo Set intermedio.

**Nessun nodo IF per il caso "niente da mandare".** La query Telegram finisce con
`HAVING count(*) > 0`: con zero lead nuovi non esce nessuna riga e i nodi a valle non
partono da soli. Un ramo in meno da mantenere.

**Si marca come notificato DOPO l'invio**, non prima: se Telegram fallisce, i lead
restano in coda per il giorno dopo. E l'insert usa `$1` come parametro, non
concatenazione di stringhe.

**L'alert Telegram resta giornaliero anche a mani vuote.** Non è solo per gli alert: i
progetti Supabase Free vengono sospesi dopo 7 giorni di bassa attività, e quella query
quotidiana è ciò che tiene sveglio il database. Se lo rendessi settimanale, il lunedì
rischieresti di trovare il progetto in pausa e il digest fallirebbe.

**Entrambi filtrano `fornitore_persona_fisica = 0`** — ditte individuali e
professionisti restano fuori dall'outreach, come deciso in `docs/liceita.md` — e
riportano l'attribuzione ANAC/IndicePA, dovuta dalle licenze.

## Rapporto con `ingestion/notifica.py`

Fanno **la stessa cosa** in due posti diversi: lo script Python locale è nato prima,
questi workflow lo replicano su n8n.

⚠️ **Non attivarli entrambi**: condividono la tabella di stato `radar.notificato`, quindi
non riceveresti doppioni, ma è comunque manutenzione duplicata. Scegline uno:

- **n8n** se vuoi vedere e modificare i filtri nell'editor senza toccare codice.
- **Python locale** se preferisci un solo posto da sorvegliare — quella macchina serve
  comunque per l'ingestion ANAC, che su n8n Cloud non può girare per via del WAF.
