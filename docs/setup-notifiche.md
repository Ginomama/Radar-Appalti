# Setup notifiche (task R4)

> Telegram per gli alert quotidiani, email per il digest settimanale.
> Tutto gira sul runner locale, lo stesso che fa l'ingestion ANAC.

## Perché in locale e non su n8n

Il runner locale **serve comunque**: l'ingestion ANAC non può girare su n8n Cloud
perché il WAF risponde 403 agli IP dei cloud provider. Quella macchina è già l'host
dei job schedulati, quindi aggiungerci il notificatore significa un posto solo da
sorvegliare invece di due. E Telegram è una POST HTTPS, SMTP è nella libreria
standard: nessuna dipendenza nuova oltre a `psycopg`, già richiesta dal push.

Se in futuro serve modificare i filtri senza toccare il codice, la stessa query si
porta su n8n con un nodo Postgres in dieci minuti — la vista `radar.v_lead_90gg`
è già pensata per essere letta da fuori.

## Passo 1 — Bot Telegram

1. Su Telegram apri **@BotFather** → `/newbot` → scegli nome e username.
   Ti restituisce un **token** nella forma `123456789:AAE...`.
2. Scrivi un messaggio qualunque al tuo nuovo bot (serve a farlo "conoscere" con te,
   altrimenti non può scriverti per primo).
3. Recupera il tuo **chat id**:

```bash
curl "https://api.telegram.org/bot<IL_TUO_TOKEN>/getUpdates"
```

Nel JSON cerca `"chat":{"id":123456789`. Quel numero è il `TELEGRAM_CHAT_ID`.

## Passo 2 — SMTP per l'email

Con Gmail serve una **password per le app** (non la password dell'account):
Account Google → Sicurezza → Verifica in due passaggi → Password per le app.

Host `smtp.gmail.com`, porta `587`, STARTTLS.

## Passo 3 — Credenziali in `.env.local`

Aggiungi queste righe a `ingestion/.env.local`, **sotto** la riga del DSN che c'è già:

```
TELEGRAM_BOT_TOKEN=123456789:AAE...
TELEGRAM_CHAT_ID=123456789
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=tua.mail@gmail.com
SMTP_PASSWORD=la-password-per-le-app
EMAIL_A=tua.mail@gmail.com
```

Il file è git-ignored e verificato tale. Nessuna credenziale compare nel codice, e
gli errori passano da `maschera()` prima di essere stampati: un traceback SMTP può
contenere la password.

## Passo 4 — Prova senza inviare

```bash
python notifica.py --telegram --dry-run
python notifica.py --email --dry-run
```

## Passo 5 — Schedulazione (Utilità di pianificazione di Windows)

Tre job. I comandi presuppongono che il percorso di Python sia nel PATH.

| Job | Quando | Comando |
|---|---|---|
| Alert Telegram | ogni giorno 08:00 | `python ingestion\notifica.py --telegram` |
| Digest email | lunedì 08:15 | `python ingestion\notifica.py --email` |
| Ingestion + push | 1° del mese 03:00 | `python ingestion\ingest.py --delta ultimi:1 --bulk && python ingestion\push_supabase.py` |

Da PowerShell, in un colpo solo (esegui come amministratore):

```powershell
$dir = "C:\Users\leona\OneDrive\Desktop\Progetti n8n Claude\radar-appalti\ingestion"
$py  = (Get-Command python).Source

schtasks /create /tn "Radar - alert Telegram" /tr "$py `"$dir\notifica.py`" --telegram" /sc daily /st 08:00 /f
schtasks /create /tn "Radar - digest email"   /tr "$py `"$dir\notifica.py`" --email"    /sc weekly /d MON /st 08:15 /f
schtasks /create /tn "Radar - ingestion"      /tr "cmd /c `"$py`" `"$dir\ingest.py`" --delta ultimi:1 --bulk ^&^& `"$py`" `"$dir\push_supabase.py`"" /sc monthly /d 1 /st 03:00 /f
```

⚠️ **L'alert Telegram deve restare giornaliero anche quando non ha nulla da dire.**
Non è solo per gli alert: i progetti Supabase Free vengono sospesi dopo 7 giorni di
bassa attività, e quella query quotidiana è ciò che tiene il database sveglio. Se lo
rendi settimanale, il lunedì rischi di trovare il progetto in pausa.

## Come si comportano i due canali

**Telegram — solo il nuovo.** Manda esclusivamente i lead mai notificati prima, presi
dalla tabella `radar.notificato` su Supabase. Rimandare ogni giorno gli stessi 887
lead li trasformerebbe in rumore e in una settimana ignoreresti il bot. Al primo
avvio manderà i 15 più importanti (`--limite` per cambiarne il numero), poi solo i
nuovi entranti nella finestra dei 90 giorni.

**Email — la fotografia completa.** Tutti i lead entro 90 giorni, raggruppati per
categoria, più le 25 di maggior valore con ente, PEC, fornitore uscente e importo.

Entrambi filtrano `fornitore_persona_fisica = 0`: sono ditte individuali e
professionisti, cioè persone fisiche, ed escluderli è la mitigazione decisa in
`docs/liceita.md`. Entrambi riportano l'attribuzione delle fonti, dovuta dalle
licenze CC BY-SA 4.0 (ANAC) e CC BY 4.0 (IndicePA).

## Nota sui caratteri corrotti

Alcuni oggetti mostrano un carattere di sostituzione (`PI�` invece di `PIÙ`). Sono
**8 righe su 236.891** — lo 0,003% — e i byte non validi sono nel file sorgente di
ANAC, non nella nostra lettura. Non vale un intervento sull'ingestion.

---

## Console locale

```bash
python ingestion/console_live.py
```

Apre `http://127.0.0.1:8420` con la console collegata **in diretta** al database.
Segnare una PEC come inviata scrive direttamente in `radar.invio`, quindi console e
`invii.py` restano allineati.

Dalla stessa pagina si possono anche **spedire** le PEC: il bottone `invia PEC`
apre l'anteprima del messaggio e, alla conferma, lo manda davvero. Configurazione
e controlli in [`setup-pec.md`](setup-pec.md). Il bottone compare solo qui: la
versione pubblicata su claude.ai non può parlare con un server SMTP.

⚠️ **Un server solo per volta.** Se lanci lo script due volte, entrambi i processi
restano in ascolto sulla stessa porta (`allow_reuse_address` lo consente) e le
connessioni si azzuffano: il browser mostra `ERR_CONNECTION_RESET`. Se succede:

```bash
netstat -ano | findstr :8420
taskkill /PID <il-pid> /F
```

Ascolta solo su `127.0.0.1`: la console mostra PEC e recapiti e non deve essere
raggiungibile dalla rete locale.
