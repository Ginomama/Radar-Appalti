# Job schedulati — installazione

Il Radar funziona anche se nessuno lo accende. Due attività in Utilità di pianificazione
di Windows, gestite da `ingestion/job.py`.

## Cosa gira, e quando

| | Quando | Cosa fa | Quanto dura |
|---|---|---|---|
| `--giornaliero` | ogni giorno, 08:30 | backup delle tabelle operative → lettura ricevute PEC → notifica Telegram dei lead nuovi | secondi |
| `--mensile` | il **3** del mese, 07:00 | backup completo → delta CIG da ANAC → bulk (aggiudicazioni, contratti) → push su Supabase | 1–4 ore |

Due ritmi e non uno solo perché le ricevute PEC arrivano in minuti: leggerle una volta al
mese vorrebbe dire scoprire a fine mese che metà delle PEC non erano mai state consegnate.

Il **giorno 3** e non il 1: ANAC pubblica il primo del mese, ma non a mezzanotte. Un 404 al
primo tentativo costerebbe un mese di ritardo.

## Installazione

```bash
python ingestion/job.py --installa
```

Se risponde *accesso negato*, serve un terminale aperto **come amministratore**.

Le attività girano con il tuo utente, non come SYSTEM: `job.py` legge `.env.local` dal tuo
profilo e scrive i backup nella tua cartella, e come SYSTEM non troverebbe né l'uno né
l'altra.

Per verificare:

```bash
python ingestion/job.py --stato
```

Mostra prossima esecuzione, ultima esecuzione ed esito di entrambe, più il riassunto degli
ultimi log.

Per rimuoverle: `python ingestion/job.py --disinstalla`.

## Prima di installare, guarda cosa farebbe

```bash
python ingestion/job.py --prova --mensile
```

Elenca gli step senza eseguirne nessuno, e segnala quelli **non configurati** — cioè quelli
a cui manca una credenziale in `.env.local`.

## Se il PC è spento all'orario previsto

Windows recupera l'attività al successivo avvio. Nessun mese viene saltato: il delta ANAC
resta scaricabile anche dopo, e `ingest.py` è idempotente (le stesse righe reingerite non
si duplicano).

## Come si legge un fallimento

I log stanno in `ingestion/logs/`, un file per ritmo e per giorno, con rotazione a 60
giorni (due cicli mensili interi). L'ultima riga utile è il riassunto:

```
radar giornaliero: 1/2 ok in 0 min, 1 non configurati
  ok    backup      0.0 min
  FALLITO ricevute  (uscita 1) la connessione a imaps.pec.aruba.it non arriva dal server...
  --    telegram  non configurato (manca TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
```

Tre esiti, tre significati diversi:

- **ok** — fatto.
- **FALLITO** — guasto vero, da guardare. Il job esce con codice 1 e, se Telegram è
  configurato, manda un avviso.
- **non configurato** — manca una credenziale, quindi quella funzione non è ancora accesa.
  Non è un errore e non conta nel totale: un allarme che suona tutti i giorni non lo legge
  più nessuno.

## Cosa blocca cosa

Nessuno step blocca uno step indipendente: se il backup fallisce, le ricevute si leggono
lo stesso. Ci sono due sole dipendenze dichiarate, ed entrambe proteggono i dati:

- `bulk` non parte se è fallito `delta`
- `push` su Supabase non parte se è fallito `bulk` — pubblicherebbe dati a metà, che è
  peggio di dati vecchi

## Segreti nei log

Ogni riga di output di ogni step passa da `maschera()` prima di finire nel log. Serve
davvero: un traceback di psycopg contiene la stringa di connessione intera, password
compresa. `ingestion/logs/` è coperto da `.gitignore` (`*.log`), ma la mascheratura viene
prima — un file può sempre finire in un allegato o in uno screenshot.

## Da configurare per completare il quadro

- **Telegram** (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` in `ingestion/.env.local`) —
  vedi `setup-notifiche.md`. Finché manca, salta la notifica giornaliera *e* l'avviso di
  guasto: i fallimenti restano solo nel log.
- **Eccezione Avast** per `imaps.pec.aruba.it` — vedi `setup-pec.md`. Finché manca, lo step
  `ricevute` fallisce ogni giorno.
