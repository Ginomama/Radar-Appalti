# Roadmap — da prototipo a sistema operativo

> Aggiornata 2026-08-26. Unisce la lista di Leonardo e lo stato reale del codice.
> Ogni voce ha un ID citabile. Le stime sono di lavoro effettivo, non di calendario.

## Stato attuale — cosa è già in piedi

| | |
|---|---|
| Ingestion ANAC incrementale e idempotente | ✅ `ingest.py`, idempotenza verificata |
| Backfill storico 2021–2025 | ✅ 60/60 file, 236.891 CIG, storico dal 2008 |
| Database locale | ✅ `radar.db`, 287 MB, 4 tabelle + 5 viste |
| **Schema `radar` su Supabase** | ✅ **fatto** — 4 tabelle + 2 viste |
| **Sync SQLite → Supabase** | ✅ **fatto** — `push_supabase.py`, 54.137 righe, 23 MB su 500 |
| TED — API e copertura | ✅ verificata, non ancora ingerita |
| IndicePA — accesso e chiave di join | ✅ verificata, non ancora ingerita |

⚠️ Due voci della lista originale — *"creazione dello schema radar"* e *"script di
sincronizzazione da SQLite a Supabase"* — **sono già chiuse**. I workflow n8n sono
sbloccati adesso: la credenziale Postgres può già interrogare `radar.v_scadenze_90gg`.

---

## Sprint 1 — Rendere azionabile ciò che già abbiamo

Le 11.196 scadenze in Supabase oggi **non sono lead**: manca il contatto e manca il
filtro tematico. Questo sprint vale più di qualsiasi fonte nuova.

### R1 — Ingestion IndicePA (contatti degli enti) · ✅ FATTO 2026-08-26

Verificato il 2026-08-26: IndicePA espone una CKAN come ANAC.

```
https://indicepa.gov.it/ipa-dati/api/3/action/package_list
dataset "amministrazioni" -> amministrazioni.txt, separatore TAB, UTF-8 con BOM
```

31 colonne. Quelle che servono:

| Colonna | Uso |
|---|---|
| `cf` | **chiave di join** con `cig.cf_amministrazione_appaltante` |
| `des_amm` | denominazione ufficiale (più pulita di quella ANAC) |
| `mail1..mail5` + `tipo_mail1..5` | PEC dove `tipo_mailN = 'Pec'` |
| `Comune`, `Provincia`, `Regione` | territorio normalizzato |
| `sito_istituzionale` | arricchimento |
| `tipologia_amm`, `tipologia_istat` | segmentazione (comune, ASL, università…) |
| `nome_resp`, `cogn_resp`, `titolo_resp` | ⚠️ dato personale, vedi R3 |

Sotto-task:
- R1.1 ✅ **gate superato**: match 89,4% sugli enti, 92,3% pesato sui lotti,
  **93,9% sulle scadenze a 12 mesi**. Il 10,6% escluso sono società partecipate e
  comparto difesa (Trenitalia, Terna, CDP, Ferservizi, Stato Maggiore Difesa):
  IndicePA elenca amministrazioni, non SpA a controllo pubblico. Gap spiegabile.
  La normalizzazione dei CF ha guadagnato 0 casi — i codici erano già coerenti — ma
  resta come difesa.
- R1.2 ✅ `ingestion/indicepa.py --ingest`, tabella `ente_contatti` (18.711 righe,
  100% con PEC) + vista `v_scadenze_contattabili`. Idempotente, verificato.
- R1.3 ✅ i nulli arrivano come stringa `'null'`: normalizzati in `leggi_ipa()`.
- R1.5 ⬅️ **da fare**: eseguire il Passo 6 di `setup-supabase.md` su Supabase
  (5 colonne + vista `v_lead_90gg`), poi rilanciare `push_supabase.py`.

⚠️ **Il RUP non è in questi dati.** Le 61 colonne ANAC non lo contengono e IndicePA
`amministrazioni` nemmeno: `nome_resp` è il rappresentante legale dell'ente, non il
responsabile del procedimento. Per RUP e RTD/CED servono i dataset `aoo` e `uo` di
IndicePA — **da verificare prima di prometterlo** (R1.4).

### R2 — Categorizzazione funzionale dei contratti · ✅ FATTO 2026-08-26

Tagging su `oggetto_lotto`: Cybersecurity, Sviluppo software, Manutenzione gestionale,
Cloud e datacenter, Assistenza hardware, Connettività, Licenze, Consulenza.

- R2.1 ✅ CPV: **64,5%** a costo zero, regole per prefisso in `ingestion/categorie.py`.
- R2.2 ✅ regex sull'oggetto: **+27,6%**.
- R2.3 ❌ **LLM non implementato, e non serve.** Copertura raggiunta **92,0%** senza
  nessuna dipendenza esterna. Restano 892 righe su 11.196: verticali eterogenei
  (ticketing trasporti, gestione contravvenzioni, sistemi sanitari). Aggiungere un LLM
  per l'8% significherebbe introdurre credenziale, latenza e un modo nuovo di fallire in
  una pipeline che oggi e' a sola libreria standard. Da rivalutare solo se quell'8%
  dovesse rivelarsi commercialmente rilevante.
- R2.4 ✅ misurato per livello, `categorie.py --misura` lo ripete quando serve.

⚠️ Bug trovato misurando: le prime regex erano scritte `(manutenzion|svilupp|...)`,
e il `` finale le rendeva **morte** — pretende un confine di parola dopo la radice, ma
in "manutenzione" segue una `e`. Correggendolo la copertura regex e' passata dal 16,1%
al 27,6%. Senza la misura per livello non ce ne saremmo accorti.

### R3 — Verifica di liceità sull'uso dei contatti · ✅ FATTO 2026-08-26

Da fare **prima** dell'outreach, non dopo. Non è burocrazia: è un prodotto che vuoi vendere.

- PEC istituzionale dell'ente → contatto di un'organizzazione, uso ordinario.
- `nome_resp`/`cogn_resp` e nominativi di RUP/RTD → **dati personali**. Usarli per
  comunicazioni commerciali richiede una base giuridica e un'informativa.
- IndicePA ha una licenza d'uso: verificarla e citarla.

Esito → **`docs/liceita.md`**. Non è un parere legale: è l'inventario fattuale da
portare a un professionista, così che la consulenza sia breve e mirata.

Tre esiti, in ordine di impatto:

1. ⚠️ **Il nodo non è la privacy, è il ShareAlike.** ANAC rilascia in **CC BY-SA 4.0**:
   le opere derivate vanno distribuite con la stessa licenza. Per un prodotto che si
   vuole vendere è la domanda più importante dell'intero progetto, e non si risolve a
   intuito. IndicePA è invece CC BY 4.0, solo attribuzione.
2. ⚠️ **Trattiamo dati personali, contrariamente a quanto assumevo.** 2.464 codici
   fiscali su 22.018 (11,2%) sono in formato persona fisica: ditte individuali e
   professionisti, con nome e cognome nella denominazione. Venti compaiono fra i lead
   a 90 giorni. Mitigazione applicata: flag `fornitore_persona_fisica` end-to-end fino
   a Supabase, per escluderli in un colpo solo.
3. La PEC dell'ente è un recapito organizzativo, ma IndicePA la pubblica per finalità
   di comunicazione istituzionale: l'uso commerciale va confermato.

### R4 — Notifiche "Radar Scadenze" · ✅ FATTO 2026-08-26

Realizzato in **due implementazioni equivalenti**. Sceglierne una, non attivarle
entrambe: condividono lo stato in `radar.notificato`, quindi niente doppioni, ma è
manutenzione duplicata.

| | Dove | Quando conviene |
|---|---|---|
| `ingestion/notifica.py` | runner locale | un posto solo da sorvegliare: quella macchina serve comunque per l'ingestion ANAC |
| `workflows/*.json` | n8n Cloud | vedere e cambiare i filtri nell'editor senza toccare codice |

⚠️ **Il primo giro l'avevo fatto solo in Python, dando per scontato un "sì" che non era
stato dato.** L'argomento usato — "il runner locale serve comunque per il WAF" — vale
per l'**ingestion**, non per la notifica: il workflow legge solo Supabase, e n8n Cloud
ci arriva benissimo. I due workflow n8n sono stati aggiunti dopo.

- ✅ **Telegram giornaliero**, solo i lead nuovi. Lo stato sta in `radar.notificato`
  su Supabase: rimandare ogni giorno gli stessi 887 lead li renderebbe rumore.
- ✅ **Email settimanale**, digest completo per categoria + le 25 per valore, HTML.
- ✅ Filtro `fornitore_persona_fisica = 0` su entrambi i canali (mitigazione R3).
- ✅ Attribuzione delle fonti in calce a entrambi, dovuta dalle licenze.
- ✅ **Due workflow n8n** in `workflows/`, validati (0 errori) e con le query eseguite
  davvero su Supabase. Formattazione in SQL con `string_agg`, nessun Code node.
- ⬅️ Restano da fare da parte di Leonardo: bot @BotFather, password per le app SMTP, e
  poi **o** le credenziali in `.env.local` (via Python) **o** l'import dei JSON in n8n
  con le credenziali nel Credentials Manager. Guide: `docs/setup-notifiche.md` e
  `workflows/README.md`.
- ⚠️ **L'API key di n8n non è valida**: `health_check` dice "connected" ma ogni chiamata
  reale risponde `AUTHENTICATION_ERROR`. Rigenerandola da Settings → n8n API posso
  creare e aggiornare i workflow direttamente, senza import manuali.

⚠️ **Il trigger Telegram resta giornaliero anche a mani vuote**, non solo per gli
alert: i progetti Supabase Free vengono sospesi dopo 7 giorni di bassa attività, e
quella query quotidiana è ciò che tiene il database sveglio.

---

## Sprint 2 — Nuova fonte: allerta gare aperte

### R5 — Ingestion TED · ✅ FATTO 2026-09-07

`ingestion/ted.py` + tabella `radar.ted` e vista `radar.v_ted_aperte`.

**È un'altra cosa rispetto al motore scadenze**, ed è il motivo per cui vale la pena. Le
scadenze ANAC sono una *previsione*: contratti finiti, e un'ipotesi su quando l'ente
ricomprerà. TED pubblica l'avviso **mentre la gara è aperta**, con la scadenza per
presentare offerta ancora nel futuro. Non è un lead da coltivare per sei mesi: è una cosa
a cui si può partecipare adesso.

Misurato: **453 avvisi in 90 giorni, 98 ancora aperti, 1.801 l'anno** sul verticale — che
conferma la stima onesta di ~1.900. Il 38,5% si aggancia a un ente che già conosciamo
dallo storico ANAC, e su quelli la scheda ente dice se vale la pena prima ancora di
leggere il capitolato.

Tre cose che l'API non dice e costano un pomeriggio:

- **`fields` è obbligatorio** e i nomi ammessi sono 1.826. Sbagliarne uno restituisce 400
  con l'elenco intero in risposta — che è anche l'unica documentazione davvero aggiornata.
- **I CPV vanno a otto cifre.** `classification-cpv IN (72)` è rifiutato, `(72000000)`
  prende tutto il ramo.
- **La paginazione vuole `paginationMode: "ITERATION"`.** Senza, l'API risponde
  regolarmente ma `iterationNextToken` torna `null` e ci si ferma alla prima pagina
  credendo di aver finito: il totale dichiarava 296 e in mano ce n'erano 100. Nessun
  errore, da nessuna parte.

Quasi ogni campo è un dizionario per lingua **e** una lista per lotto. La scadenza in
particolare: un avviso con otto lotti ha otto date, e quella utile è la più vicina fra
quelle ancora future.

### R6 — Workflow n8n "Allerta TED" · ✅ FATTO 2026-09-07

`workflows/radar-allerta-ted.json`, 9 nodi, validati uno per uno con `validate_node`.
Le tre query SQL sono state provate sul database vero, compresa la doppia esecuzione:
l'upsert è idempotente e un avviso già annunciato non torna.

Gira su n8n Cloud senza problemi — **TED non ha il WAF sugli IP cloud** che blocca ANAC.

Quattro scelte che evitano guasti silenziosi:

- **finestra di 3 giorni, non 1**, anche girando ogni giorno: se un'esecuzione salta, una
  finestra da 24 ore perde quegli avvisi per sempre e nessuno se ne accorge
- **`xmax = 0`** nella `RETURNING` distingue gli inserimenti veri dagli aggiornamenti,
  se no ogni giorno si riannunciano gli stessi avvisi
- **si segna dopo l'invio**, non prima: se Telegram fallisce l'avviso resta da annunciare
- **un IF prima del messaggio**: nei giorni senza avvisi non parte niente. Un messaggio
  vuoto al giorno è il modo più rapido per far ignorare il canale

⚠️ **Non è stato attivato**: servono le credenziali Postgres e Telegram nel Credentials
Manager di n8n, e quelle le configuri tu. Senza Telegram il workflow funziona lo stesso
disattivando l'ultimo nodo — `radar.ted` si riempie comunque, ed è quello che legge la
console.

---

## Sprint 3 — Output commerciale

### R7 — Template di outreach per affidamenti diretti · ✅ FATTO 2026-09-01

Esito → **`docs/outreach-template.md`** + **`docs/lista-outreach.csv`** (20 destinatari).

L'88% è affidamento diretto, quindi chiedere di partecipare a una gara è l'ask sbagliato:
spesso la gara non ci sarà. Ma per l'affidamento diretto l'ente deve fare una verifica di
mercato, e molti tengono un **elenco di operatori economici**. La richiesta giusta è
quindi *"inseriteci fra gli operatori da consultare"* — procedurale, non commerciale, e
il protocollo sa già come smistarla.

Il registro è stato **adattato**: la `brand-voice` usa il "tu" e un tono informale, giusto
per le PMI e sbagliato verso un protocollo generale. Resta il resto — linguaggio semplice,
numeri concreti, nessuna promessa vaga — e il differenziatore *"il risultato resta di
proprietà dell'ente"*, che verso una PA vale doppio perché parla al lock-in sul fornitore.

La riga sulla provenienza dei dati (IndicePA + ANAC) non è burocrazia: rende il messaggio
legittimo invece che inquietante, e assolve gli obblighi di trasparenza di R3.

⚠️ Prerequisito da verificare: abilitazione MEPA per le categorie pertinenti.

### R7b — Invio PEC dalla console · ✅ FATTO 2026-09-04

Esito → **`ingestion/pec_smtp.py`**, il bottone `invia PEC` nella console e
**`docs/setup-pec.md`**.

Mandarle a mano voleva dire aprire il `.txt`, copiare tre campi nella webmail, spedire e
poi ricordarsi di segnare l'invio: due minuti a PEC, e il passo che si dimentica sempre è
proprio quello del tracciamento. Ora il messaggio parte e la riga si segna `inviata` nella
stessa transazione, quindi console e `invii.py` non si disallineano più per dimenticanza.

Restano due passaggi e non uno: anteprima, poi conferma. Una PEC viene protocollata
dall'ente e non si ritira, e il destinatario sbagliato è l'unico errore qui che dopo non si
corregge — la rilettura costa cinque secondi contro i due minuti di prima.

Quattro controlli automatici: segnaposto `[INSERISCI …]` ancora nel testo, destinatario del
file diverso da quello registrato, riga già inviata, tetto di 20 al giorno (che non è
prudenza: i provider PEC limitano la casella oltre una soglia non pubblicata).

Il bottone esiste solo nella console locale. L'artifact pubblicato non può raggiungere un
server SMTP, ed è giusto così: le credenziali della casella PEC non devono uscire da
`.env.local`.

⚠️ Bloccato finché `MITTENTE` in `genera_pec.py` ha i campi vuoti — il controllo 1 rifiuta
di spedire una PEC che in calce dice `P.IVA [INSERISCI P.IVA]`.

### R14b — Console leggibile anche da chi non l'ha costruita · ✅ FATTO 2026-09-04

Revisione di interfaccia in vista dell'uso da parte del team. Le correzioni che
contavano davvero:

**Lo stato sembrava un bottone.** `.p-da` aveva bordo e sfondo identici a `.btn`:
ogni riga mostrava quattro forme uguali in fila — una informazione e tre azioni —
senza modo di distinguerle. Ora lo stato è un pallino colorato più testo, in una
colonna di larghezza fissa che allinea i bottoni fra le righe.

**I filtri della tabella si azzeravano da soli.** `disegna()` ricostruisce la
pagina intera, quindi cambiare provincia nel pannello territorio — o lotto nel
tracker — svuotava ricerca, categoria e urgenza senza dirlo. I criteri ora vivono
in un oggetto `tab` e sopravvivono al re-render.

**La pagina traboccava in orizzontale sotto i 900px.** Le colonne di una griglia
CSS non scendono sotto la larghezza minima del contenuto, e la tabella ha sette
colonne: a 700px la pagina diventava 1112px. Risolto con `min-width:0` sui figli
della griglia, che è la ragione per cui `overflow-x:auto` sulla tabella non
bastava.

**Il pallino di freschezza era sempre verde**, anche su dati di tre settimane
prima: sembrava un indicatore senza esserlo. Ora diventa ambra dopo una settimana
e rosso dopo tre.

Più: percentuali riportate tutte sulla stessa base (prima due tessere affiancate
mostravano denominatori diversi), colonne ordinabili da mouse e da tastiera,
legenda dei colori di urgenza, date in italiano accanto ai giorni mancanti,
`aria-label` con il nome dell'ente su ogni bottone di riga, una riga di
spiegazione sotto ogni titolo di pannello e una barra **"cosa fare adesso"** che
traduce lo stato in un compito. Via il gergo: niente più *"senza LLM"*,
*"valore intercettato"*, *"chi presidia il mercato"*, *"n.c."*, *"gg"*.

### R8 — Report dimostrativo territoriale · ✅ FATTO 2026-09-07

`ingestion/report.py`. PDF (e CSV) su una provincia: le 20 migliori scadenze, ordinate per
punteggio, con il contesto del territorio in testa.

Raccontare a voce che «esiste un motore che trova i contratti IT in scadenza» non convince
nessuno. Venti righe vere sulla provincia di chi ascolta — enti che conosce, fornitori che
ha già incontrato, CIG che può verificare su ANAC in due minuti — sono un'altra cosa. È la
**verificabilità** a fare la differenza fra una demo e una presentazione.

Due scelte che sembrano prudenza e sono commerciali:

**La PEC non c'è.** Il report mostra il giudizio — cosa scade, quanto vale, chi ce l'ha
adesso, quanto è probabile che quella porta si apra — e non il recapito per bussare. Chi
legge vede esattamente cosa comprerebbe e non ha ancora niente da usare. `--contatti` la
include, ed è la versione interna.

**L'attribuzione è su ogni pagina.** ANAC è CC BY-SA 4.0 e IndicePA CC BY 4.0: è dovuta da
entrambe e costa una riga. ⚠️ La clausola ShareAlike su un prodotto commerciale resta
aperta (`docs/liceita.md` §2): venti righe elaborate non sono la ridistribuzione del
dataset, ma vale la pena saperlo prima di stamparne cinquanta copie.

C'è anche il contesto storico: per Padova, 203 scadenze a 12 mesi in 36 enti per 922 M€, e
la quota misurata di cambi di fornitore su quella provincia. È il numero che rende il resto
credibile invece che ottimistico.

### R21 — Allegato alla PEC · ✅ FATTO 2026-09-07

`ingestion/presentazione.py` + `allega()` in `pec_smtp.py`, acceso da `PEC_ALLEGATO=1`.

**Era bloccato da una condizione scritta qui**: farlo solo dopo aver misurato il tasso di
risposta *senza*. Ora la misura c'è — 18 inviate, 18 consegnate, **0 risposte** — quindi la
linea di base è zero e qualunque miglioramento sarà attribuibile all'allegato.

Il contenuto viene da `MITTENTE` e `COSA_FACCIAMO` di `genera_pec.py`: un posto solo da
aggiornare, e allegato e messaggio non possono contraddirsi. La parte più utile non è
l'elenco delle competenze ma le **quattro domande che un RUP fa davvero** — chi resta
proprietario, cosa succede se ve ne andate, come si affida, quanto costa scoprire se serve.

#### Quello che manca è voluto

Referenze, casi studio e numeri di clienti **non ci sono**, e non li ho inventati. La
sezione esiste con un `[DA COMPILARE]`, e finché resta così **`pec_smtp.py` si rifiuta di
allegare il file**, fallendo rumorosamente invece di spedire in silenzio senza allegato.

Un PDF con dentro un segnaposto, mandato a un ente pubblico, non è un allegato incompleto:
è l'unica occasione che si aveva con quell'ente, bruciata. Bastano due o tre righe vere in
`REFERENZE`.

---

### R14 — Console operativa (artifact privato) · ✅ FATTO 2026-09-02

Pagina privata su claude.ai, un click e vedi tutto: riepilogo, tracker degli invii PEC
con i due lotti, 120 scadenze filtrabili, categorie, competitor per valore e **per
provincia**. Esportazione CSV della lista filtrata.

Due scelte che la rendono utile invece che una fotografia morta:

- **I giorni alla scadenza si ricalcolano a ogni apertura.** Nella pagina sono incorporate
  le date, non i giorni: fra due settimane i numeri sono ancora giusti senza ripubblicare.
- **Lo stato del tracker vive nel documento**, non in localStorage: segnando una PEC come
  inviata la pagina si ripubblica, quindi lo stato si ritrova da qualunque browser ed è
  rileggibile da qui per allinearlo a `radar.invio`.

### Due modalità, stessa pagina

Il vincolo della versione pubblicata è reale — la policy dei contenuti su claude.ai
blocca le chiamate a host esterni, e il connettore Supabase punta a un altro account —
ma **vale solo lì**. In locale non esiste, quindi `docs/console.html` funziona in due modi:

| | Dati | Stato degli invii | Dove |
|---|---|---|---|
| **Artifact** su claude.ai | istantanea, da rigenerare | nel documento, si ripubblica | ovunque, anche da telefono |
| **`console_live.py`** | **in diretta dal database** | **scritto in `radar.invio`** | solo sulla macchina |

Il JavaScript sceglie da solo: se il payload incorporato è vuoto chiede i dati a
`/api/dati`, altrimenti usa l'istantanea. Una sola pagina da mantenere.

La versione locale serve 400 lead invece di 120 e 40 province invece di 30, perché non
deve stare dentro un documento pubblicato. E segnando una PEC come inviata scrive dritto
in `radar.invio`, quindi console e `invii.py` non si disallineano mai — cosa che con
l'artifact può succedere.

    python ingestion/console_live.py      # apre localhost:8420

Ascolta solo su `127.0.0.1`: la console mostra PEC e recapiti, non deve affacciarsi
sulla rete locale.

Sorgente in `docs/console.html`, payload in `docs/console-dati.json`.

---

## Sprint 4 — Robustezza (non nella lista originale, ma serve)

### R9 — Job schedulati · ✅ FATTO 2026-09-06 · ⚠️ da installare

`ingestion/job.py`. Due ritmi invece di uno: le ricevute PEC arrivano in minuti, leggerle
una volta al mese vorrebbe dire scoprire a fine mese che metà delle PEC non erano mai
state consegnate.

| | quando | step |
|---|---|---|
| `--giornaliero` | ogni giorno 08:30 | backup operative → ricevute PEC → Telegram |
| `--mensile` | il 3 del mese 07:00 | backup completo → delta ANAC → bulk → push Supabase |

Il giorno 3 e non il 1: ANAC pubblica il primo del mese ma non a mezzanotte, e un 404 al
primo tentativo costerebbe un mese di ritardo.

Tre regole che il file rispetta:

- **nessuno step blocca uno step indipendente.** Se il backup fallisce, le ricevute si
  leggono lo stesso. Se l'ingest fallisce, il push su Supabase **non** parte: pubblicherebbe
  dati a metà, che è peggio di dati vecchi.
- **credenziali assenti non sono un guasto.** Telegram non è configurato, quindi quello step
  risulta «non configurato» e non conta come errore. Un allarme che suona tutti i giorni non
  lo legge più nessuno.
- **niente segreti nei log.** Ogni riga di output passa da `maschera()` prima di essere
  scritta: un traceback di psycopg contiene la DSN intera, password compresa.

Rotazione log a 60 giorni (due cicli mensili interi), sulla data nel nome del file e non
sull'mtime. Se qualcosa fallisce parte un avviso Telegram, quando sarà configurato.

⚠️ Vincolo architetturale: **non può girare su n8n Cloud** — il WAF ANAC risponde 403
agli IP dei cloud provider. Da qui l'Utilità di pianificazione di Windows sulla macchina
locale.

Resta da fare: `python job.py --installa` (registra le due attività in Windows; è una
modifica permanente alla macchina, quindi la lancia Leonardo). Verifica: `--stato`.

### R10 — Sentinella sul drift dello schema ANAC · ✅ FATTO 2026-09-06

`controlla_schema()` in `ingest.py`, chiamata su ogni zip prima della mappatura. Distingue
due casi che meritano reazioni opposte:

- manca una colonna **essenziale** (`ESSENZIALI`, es. `data_termine_contrattuale` per
  `avvio_contratto`) → `SystemExit`. Senza quella colonna la tabella non serve a niente, e
  ingerirla vuota è peggio che non ingerirla.
- manca una colonna secondaria, o ANAC ne ha aggiunte di nuove → avviso su stderr, si va
  avanti. Sono informazioni, non guasti.

Provata in positivo su tre zip reali (passa, e segnala 8 colonne nuove mai usate:
`data_appr_prog_ese`, `cig_prog_esterna`, `cod_modo_riaggiudicazione`,
`cod_prestazioni_comprese`, `data_cons_prog`, `data_incarico_prog`, `flag_scomputo`,
`modo_riaggiudicazione`) e in negativo su uno zip con la colonna rimossa a mano (blocca).

### R11 — Conferma della quota IT delle scadenze · ✅ FATTO 2026-09-06

Misurata sullo stesso file, alla stessa data, con e senza il filtro sui 236.891 CIG del
verticale: **5,5% a 12 mesi** (3.672 su 67.032), cioè 10,1 al giorno. La vecchia stima
diceva 5,7% → ~3.800 → ~10/giorno: **reggeva**. Il che è un'informazione di per sé — la
quota IT sulle scadenze coincide con quella sui bandi, quindi i contratti IT non durano
né più né meno degli altri.

Il numero che conta per il commerciale non era però nessuno dei due: sono i **1.570 enti
distinti** con almeno una scadenza entro 12 mesi. I 5.272 contratti si accorpano, perché
più contratti dello stesso ente vanno in una PEC sola.

Resta un limite dichiarato in `fonti-dati.md`: lo storico CIG copre il 2021–2026, quindi
una convenzione quadro lunga bandita prima del 2021 non viene contata. Il 5,5% è un
minimo garantito, non un totale.

### R12 — Valutare `smartcig` per il sotto soglia · ✅ VALUTATO 2026-09-07 · **no**

Ispezionato il delta di settembre 2026: 683.756 righe, 102 MB compressi per un mese solo.
La risposta è no, per tre motivi indipendenti — e basterebbe il secondo.

**1. Non c'è il CPV.** Ventisei colonne, nessuna con il codice merceologico. Senza, il
verticale non si può filtrare e resta solo la regex sull'oggetto. Che su questo dataset
non regge: `categorie.py` è nata come **rete di sicurezza dopo il filtro CPV**, non come
classificatore autonomo, e applicata a dati non filtrati «manutenzione» prende tutto.

Su un campione di 14 righe sopra i 20.000 € classificate *Manutenzione e assistenza*,
**14 su 14 erano falsi positivi**: reti fognarie, illuminazione stradale, centrali
termiche, strade bianche. Quella categoria da sola valeva 70.701 righe delle 125.380
«IT».

**2. Non c'è la data di fine contratto.** Ci sono `data_comunicazione`, `anno_` e
`mese_comunicazione`. Il motore scadenze — cioè il prodotto — ha bisogno di sapere
**quando finisce** un contratto. Su smartcig non si può sapere, e nessuna elaborazione a
valle lo recupera.

**3. Gli importi sono un altro mestiere.** Mediana 919 €, media 3.483 €. Togliendo i
falsi positivi di «manutenzione» restano 54.679 righe plausibilmente IT in un mese, di cui
**182 sopra i 20.000 €** (in 134 enti) e **7 sopra i 40.000 €**. Il motore ANAC oggi ne dà
13.567 con la data di fine.

E il costo: 102 MB al mese, ~1,2 GB l'anno, contro un tetto Supabase di 500 MB.

**Cosa resta vero**: il sotto soglia è dove sta l'88% del mercato, e i portali regionali
lo vietano nel `robots.txt`. Semplicemente **smartcig non è la via**: pubblica che un
affidamento è avvenuto, non quando finirà. Se un domani ANAC aggiungesse CPV e durata,
questa valutazione va rifatta — le due misure da controllare sono in questa pagina.

### R13 — Revisione sicurezza · ✅ FATTO 2026-09-07

`ingestion/sicurezza.py`. Sette controlli sullo schema `radar`, esce 1 se c'è qualcosa da
sistemare. Non una lettura a occhio: queste impostazioni non si vedono aprendo la console
di Supabase, e **si degradano da sole** — non spegnendo una protezione, ma creando una
tabella nuova e dimenticandosene.

Che è esattamente quello che era successo, e che il primo giro ha trovato:

**`radar.esito` era nata senza RLS** (R17), mentre tutte le altre sei ce l'avevano.

**Tutte e cinque le viste scavalcavano RLS.** Questo è il trabocchetto vero, ed è
invisibile: una vista Postgres, per default, legge coi diritti di **chi l'ha creata** —
qui `postgres`, che ha `bypassrls`. Si sarebbe potuto accendere RLS su ogni tabella,
sentirsi al sicuro, ed esporre una singola vista che continuava a servire tutto a
chiunque. Risolto con `security_invoker = on` su tutte e cinque.

Il resto è pulito: nessun privilegio ad `anon` o `public`, nessuna estensione che possa
chiamare l'esterno, nessuna colonna con dati personali fra quelle escluse da R3.

Le policy restano **volutamente assenti**: RLS attivo senza policy significa che non legge
nessuno tranne chi ha `bypassrls`, ed è la configurazione più restrittiva possibile finché
l'unico accesso è la chiave di servizio. Scriverle adesso vorrebbe dire indovinare a chi
dare accesso.

Da rilanciare prima di ogni esposizione, e dopo ogni migrazione che crea tabelle.

---

## Sprint 5 — Chiudere il ciclo dell'invio

> Oggi il sistema sa mandare una PEC e segnarla. Non sa se è arrivata, non
> richiama nessuno, e non scopre mai com'è finita la scadenza. Sono i tre buchi
> che separano uno strumento da un processo.

### R15 — Leggere le ricevute PEC via IMAP · ✅ SCRITTO 2026-09-06 · ⚠️ da provare

Il buco più grosso. `pec_smtp.py` spedisce, ma **nessuno legge le ricevute**: se
una casella è piena, l'indirizzo è dismesso o il dominio rifiuta, la riga resta
`inviata` per sempre e tu aspetti una risposta che non può arrivare. Lo stato
`non_consegnata` esiste in `radar.invio` e **non lo popola nessuno**.

Una PEC genera due ricevute automatiche: *accettazione* (il tuo provider l'ha
presa in carico) e *consegna* (è entrata nella casella del destinatario), oppure
un *avviso di mancata consegna*. Tutte citano il `Message-ID` che già salviamo
nelle note.

- R15.1 IMAP sulla casella PEC (Aruba: `imaps.pec.aruba.it:993`), stessa
  "password per programmi di posta" già in `.env.local`.
- R15.2 riconoscere il tipo di ricevuta dall'header `X-Ricevuta` e dall'oggetto
  normalizzato (`ACCETTAZIONE:`, `CONSEGNA:`, `ANOMALIA MESSAGGIO:`).
- R15.3 estrarre il `Message-ID` originale dal `postacert.eml` allegato e
  riconciliarlo con `radar.invio.note`.
- R15.4 nuove colonne `accettata_il`, `consegnata_il`, `errore_consegna`;
  la mancata consegna porta la riga a `non_consegnata`.
- R15.5 **non cancellare né spostare nulla** nella casella: solo lettura.

Vale anche come prova legale: la ricevuta di consegna è ciò che dimostra che
l'ente ha ricevuto, e va conservata.

### R16 — Sollecito automatico a 12 giorni · ✅ SCRITTO 2026-09-06 · ⚠️ dipende da R15

Il primo messaggio a freddo a una PA finisce al protocollo e spesso si ferma lì.
Il secondo contatto è dove arriva la maggior parte delle risposte, e oggi non
esiste: dopo 21 giorni la riga passa a `nessuna_risposta` e la partita si chiude
senza aver mai riprovato.

- R16.1 secondo modello, corto (5 righe), che **cita la PEC precedente e la data**
  — non un rinvio dello stesso testo.
- R16.2 parte solo se `consegnata_il` è valorizzata (serve R15: sollecitare una
  PEC mai consegnata è rumore).
- R16.3 un solo sollecito per ente, mai due.
- R16.4 stato `sollecitata`, e la finestra dei 21 giorni riparte da lì.

### R17 — Che fine ha fatto la scadenza · ✅ FATTO 2026-09-07

`ingestion/esito.py`. Misurato su **30.301 contratti scaduti** fra gennaio 2023 e giugno 2026.

#### La risposta

| Esito | | |
|---|---:|---:|
| nessun seguito trovato | 24.888 | 82,1% |
| rinnovato allo stesso fornitore | 3.429 | 11,3% |
| vinto da un altro fornitore | **1.205** | **4,0%** |
| seguito trovato, aggiudicatario non ancora noto | 779 | 2,6% |

Dei 5.413 con un seguito, il **94,1% è andato per affidamento diretto** e solo il 5,9% a gara.

**Su 30.301 scadenze, 1.429 (4,7%) sono finite in una porta aperta.** È il numero che
mancava. Tre cose che cambia:

1. **L'ordinamento dei lead va per importo, non per data.** La quota di porta aperta più
   che raddoppia con la dimensione del contratto:

   | fascia | scadenze | porta aperta |
   |---|---:|---:|
   | sotto 40 k€ | 15.195 | 3,2% |
   | 40–140 k€ | 9.717 | 5,8% |
   | 140 k€–1 M€ | 4.139 | **7,5%** |
   | oltre 1 M€ | 1.250 | 5,0% |

   Sotto i 40 k€ ci sono metà delle scadenze e il tasso peggiore: sono il grosso del lavoro
   e la parte meno redditizia. La fascia 140 k€–1 M€ è il bersaglio.

2. **Il gioco non è vincere gare, è sostituire l'uscente prima dell'affidamento diretto.**
   Il 94,1% dei seguiti non passa da una gara: quando la PA rinnova un servizio IT lo fa
   quasi sempre in via diretta. Arrivare *dopo* il bando è arrivare tardi per definizione —
   ed è esattamente la finestra che il Radar apre.

3. **Chi si sostituisce davvero**: TIM (32 volte), GPI (18), Dedalus (18), Engineering (16+12),
   Maggioli (14), Var Group (11). Sono gli uscenti che più spesso vengono rimpiazzati.

#### Come si trova il seguito

Stesso ente, finestra −6/+12 mesi dalla scadenza (il −6 perché i rinnovi si pubblicano prima
che il contratto scada), poi somiglianza fra gli oggetti. Tre passaggi, ognuno nato da un
falso positivo visto nei dati:

- **IDF invece di una lista di stopword.** La lista non converge: dopo tre giri restavano
  fuori «procedura negoziata senza previa pubblicazione», il preambolo PNRR, gli articoli del
  Codice. Pesare ogni parola per quanto è rara nei 283.929 oggetti li annulla da soli
  (`servizio` vale 1,4, `symantec` vale 9,1) e non richiede manutenzione.
- **Boilerplate per ente.** Restava un buco: parole rare in Italia ma banali per quell'ente.
  `giannina gaslini` è rarissimo nel dataset e sta in ogni bando dell'ospedale, quindi due
  contratti scollegati si somigliavano per il nome del committente.
- **Soglia adattiva sugli oggetti corti.** Con la soglia fissa, «CANONE ANNUALE DARKTRACE»
  era inconfrontabile per costruzione: una parola rara sola. Il **26,9% dei contratti non
  veniva nemmeno cercato**. Ora si chiede il minore fra la soglia piena e quasi tutta la
  massa rara che l'oggetto possiede — mai però su una parola sola.

#### Un bug che valeva il 6% della categoria più importante

`DROMEDIAN SRL` → `DROMEDIAN S.R.L.` risultava **cambio di fornitore**. In ANAC la stessa
azienda compare con codici fiscali diversi (02147390690 su un CIG, 0003015146 sul rinnovo
dello stesso Comune), e 827 aggiudicatari hanno la P.IVA senza lo zero iniziale. Il confronto
ora è su due chiavi: CF normalizzato **e** ragione sociale senza forma giuridica.

#### Cosa questo numero non dice

- **`nessun_seguito` non vuol dire «il servizio è finito».** Il tasso di seguito trovato è
  **piatto al 13–19% su 45 mesi**, quindi non è un ritardo di pubblicazione: è strutturale.
  Ma il seguito può essere invisibile perché l'ente è passato a una convenzione Consip o a un
  accordo quadro, il cui oggetto è scritto in modo del tutto diverso («ADESIONE CONVENZIONE
  CONSIP LICENZE SOFTWARE MULTIBRAND 3» non somiglia a «MANUTENZIONE SOFTWARE X»). Le adesioni
  sono il 4,3% dei CIG e già l'8,8% dei seguiti che troviamo: quelle che ci sfuggono sono
  probabilmente una fetta importante dell'82%.
- **Fuori dal verticale non vediamo.** Il seguito cambia divisione CPV nell'8,3% dei casi
  osservati; quello che esce del tutto da 72/48 (hardware 30xxx, telco 32xxx) è invisibile.
- **Precisione dichiarata, non perfetta.** Il 72% dei match ha punteggio ≥ 0,60. Sotto quella
  soglia restano dentro gli acquisti fratelli dello stesso progetto («PNRR Scuola 4.0 Azione 2
  — laboratori» contro «… — software gestionale»). `python esito.py --dubbi` li elenca.

Tabella `esito` in SQLite, vista `v_esito`, spedita su `radar.esito` con il push. In più
`radar.v_ente_apertura`: la quota di porta aperta per ente, che alimenta R18.

Nel job mensile sta fra `bulk` e `push`.

### R20 — Anti-duplicato fra lotti e nel tempo · ✅ FATTO 2026-09-06

Un ente che compare in due lotti riceve due PEC diverse, e non c'è nulla che lo
impedisca: oggi `pec` e `pec-marche` non si sovrappongono per fortuna, non per
costruzione. Serve anche una regola temporale — riscrivere allo stesso protocollo
dopo tre settimane è insistenza, dopo otto mesi è un nuovo contatto legittimo.

- R20.1 `genera_pec.py` esclude gli enti già presenti in un altro lotto attivo.
- R20.2 blocco in `pec_smtp.py` se allo stesso `cf_ente` è partita una PEC negli
  ultimi N mesi (default 6), scavalcabile con `--forza`.
- R20.3 la console mostra "già contattato il …" sulle righe interessate.

---

## Sprint 6 — Profondità dei dati

### R18 — Punteggio dei lead · ✅ FATTO 2026-09-07

`ingestion/punteggio.py`. I pesi non sono decisi a tavolino: sono **misurati** sui 30.301
contratti scaduti di R17. Tasso base di porta aperta 4,72%, e ogni fattore è un
moltiplicatore su quello.

| Fattore | Migliore | Peggiore |
|---|---|---|
| storia dell'ente | oltre 25% di aperture ×2,80 | mai aperta ×0,45 |
| categoria | Cybersecurity ×1,87 | Software verticale ×0,58 |
| importo | 140k–1M ×1,59 | sotto 40k ×0,69 |
| fornitore uscente | micro, 1–2 contratti ×1,27 | oltre 200 contratti ×0,70 |

`--calibra` li rimisura e li scrive in tabella, quindi non invecchiano: il job mensile lo
rifà dopo ogni ingestione.

#### Tre cose che il primo tentativo sbagliava

**L'ente vedeva se stesso.** Calcolando la storia di un ente contavo anche la riga che
stavo valutando, quindi una scadenza aperta si spiegava da sola. Togliendola, il fattore
ente scende da ×6,87 a ×2,80 — resta il più forte dei quattro, ma è la metà di quello che
sembrava.

**«Ignoto» era un bonus.** Il fornitore uscente sconosciuto usciva ×3,00, il tetto massimo,
su 111 osservazioni. Non sapere non è un buon segno né cattivo: premiarlo significa premiare
le righe compilate peggio. Ora ogni valore `ignoto` è forzato a neutro.

**La scala era inutilizzabile.** Normalizzando su una probabilità massima teorica il 97% dei
lead finiva sotto 20 e il punteggio non ordinava niente. Ora 100 = quattro volte il tasso
base con servibilità e urgenza piene, che sui dati veri è il 99° percentile.

#### Probabilità e servibilità sono due cose diverse

Le probabilità migliori stanno in **Cybersecurity** (×1,87) e **Abbonamenti editoriali**
(×2,08), che non sono categorie che FlowLine serve. Al primo giro il punteggio metteva in
cima quattro lead perfetti e irraggiungibili.

Da qui la separazione in tre termini, tenuti distinti anche nell'output di `--perche`:

- **probabilità** — misurata, dai quattro fattori sopra
- **servibilità** — assunzione commerciale dichiarata: fascia d'importo aggredibile, e
  categoria fra le cinque che sappiamo servire (fuori categoria ×0,25, non zero: un lavoro
  adiacente ogni tanto si prende)
- **urgenza** — euristica dichiarata: il picco è fra i 60 e i 210 giorni. R17 guarda
  contratti già scaduti, quindi sui giorni di anticipo non ha niente da dire

Il punteggio è il loro **prodotto**, non una somma pesata: se una delle tre è zero il lead
non vale niente, e una somma lo terrebbe a galla lo stesso.

#### Risultato

Su 13.567 scadenze: **193 eccezionali** (60+), 439 ottimi (35+), 1.034 buoni (18+), il resto
sotto. Che tre quarti non valgano la pena è il risultato corretto, non un difetto.

È l'ordinamento predefinito della console, di `radar.v_lead_90gg` che legge n8n, e di
`--top`. `--perche <CIG>` scompone il punteggio nei suoi pezzi.

### R19 — Scheda ente · ✅ FATTO 2026-09-07

`ingestion/scheda.py`, più `/api/ente` nella console e il nome dell'ente cliccabile in ogni
riga. Sette sezioni, nell'ordine in cui servono prima di una chiamata:

| | |
|---|---|
| **Si entra?** | lo storico degli esiti (R17) per quell'ente, col verdetto in chiaro |
| Come comprano | gara o affidamento diretto, in che proporzione |
| Ogni quanto | durata media dei contratti — dice quando ripassare |
| Quanto spendono | importo aggiudicato per anno, con la tendenza |
| Chi glieli tiene | i fornitori che presidiano l'ente, per valore |
| Cosa scade | le scadenze in arrivo, col punteggio di R18 |
| Cosa gli abbiamo già detto | lo storico dei nostri invii PEC |

La prima sezione è messa per prima di proposito: **un ente che in tre anni non ha mai
cambiato fornitore non è un lead, per quanto spenda**, e saperlo prima di leggere il resto
evita dieci minuti di preparazione a una chiamata che non andava fatta.

Esempio reale, ASL Toscana Sud Est: *«Ente che si muove: 15 porte aperte su 79 scadenze
osservate (19%). Vale la pena insistere»*, 100% per affidamento diretto, durata media 16
mesi, GPI e TIM che si tengono 32 M€ su 202 lotti.

Due note tecniche:

- **È l'unico punto del sistema che legge da entrambi i database.** Lo storico ANAC sta in
  SQLite (su Supabase vanno solo i derivati), i contatti PEC stanno su Supabase. Se Supabase
  non risponde la scheda si costruisce lo stesso senza la sezione contatti: quello che serve
  prima di una chiamata è il resto.
- **`radar.invio` non ha il codice fiscale**, identifica il destinatario dalla PEC — la
  stessa chiave dell'anti-duplicato di R20. La scheda quindi cerca per PEC, non per CF.

Funziona anche da riga di comando: `python scheda.py "comune di jesi"`, o `--cerca` per
elencare gli enti che somigliano.

### R28 — Arrivare a chi decide, non al protocollo · ✅ FATTO 2026-09-07 (in parte)

Sbloccato dal criterio che era già scritto qui: **18 PEC inviate, 18 consegnate, zero
risposte**. Le ricevute confermano la consegna, quindi il messaggio arriva e si ferma
dopo — il sospetto è il destinatario, non il testo.

Tre strade possibili. Misurate tutte e tre **prima** di costruire, e due non funzionano:

| Strada | Copertura su 18.707 enti | Dati personali |
|---|---|---|
| PEC di un ufficio IT **diversa** da quella dell'ente | **2,7%** | no |
| **Nome** dell'ufficio IT, da mettere nell'oggetto | 97,3% | no |
| Nome e recapito del **responsabile** (RTD) | **92,7%** | **sì** |

`ingestion/uffici.py` fa le prime due: carica 24.997 uffici da IndicePA tenendo **solo le
colonne organizzative**, e `genera_pec.py` mette il nome dell'ufficio in testa
all'oggetto — la riga che legge chi smista. Le quattro colonne personali del dataset sono
escluse nel codice con il nome scritto per esteso, così fra un anno nessuno pensa a una
dimenticanza.

**Il gate è stato utile perché è andato male.** La prima strada, quella che sembrava
ovvia — trovare la PEC dell'ufficio informatico — copre il 2,7%, e guardando quei 514 casi
la PEC «dell'ufficio IT» è spesso `ragioneria@` o `tecnico@`. Mezz'ora di misura ha
risparmiato una pipeline intera.

Anche la seconda è onestamente piccola: il 95% degli enti dichiara *«Ufficio per la
transizione al Digitale»*, che ogni PA ha dovuto istituire per il CAD. Corretto, non
distintivo. Costa zero e si misura sul prossimo lotto.

#### La terza strada è ferma, ed è una decisione, non un lavoro

Il 92,7% degli enti ha un RTD con nome e email nel dataset IndicePA — pubblicato per
obbligo di legge (CAD art. 17) proprio perché sia raggiungibile. È l'unica delle tre che
sposterebbe davvero il tasso di risposta, ed è dato personale.

Il dataset è stato scaricato per contare la copertura e **cancellato subito dopo**:
nessun nome su disco, nessuna colonna personale nello schema. La misura c'è, il
trattamento no.

Cosa serve per procedere → `docs/liceita.md` §7: base giuridica, informativa art. 14,
minimizzazione. C'è anche una via intermedia che vale la pena valutare per prima —
**nominare la persona scrivendo comunque alla PEC istituzionale**, che ottiene quasi lo
stesso effetto di smistamento senza conservare recapiti personali.

### R26 — Spinta dei lead su GoHighLevel · RINVIATO · ~3h

Il CRM è già in uso in FlowLine. Un lead che ha risposto va tracciato dove si
tracciano gli altri, non in una tabella a parte: senza, il seguito commerciale
vive in due posti e uno dei due muore.

⚠️ Prima verificare i nomi esatti degli stage nella pipeline — è la regola già
scritta in `CLAUDE.local.md`.

**Rinviato il 2026-09-07.** Il CRM è a pagamento e oggi non c'è niente da tracciarci: zero
risposte su 18 PEC. R25 intanto tiene il funnel dove i dati già stanno, con gli stessi nomi
di stage, quindi il rinvio non costa una riscrittura.

**Quando riprenderlo** — al primo dei tre:
- una seconda persona lavora i lead, e serve sapere chi ha in mano cosa
- una ventina di conversazioni aperte insieme: a quel punto la console non basta più
- servono calendario, sequenze automatiche o preventivi — cose che qui non ci saranno mai

### R27 — Copertura territoriale a rotazione · ✅ FATTO 2026-09-07

`ingestion/territorio.py`. Due lotti scelti a mano vanno bene per provare, non per
lavorare: senza un ordine si rifanno le province comode, si saltano quelle grandi, e non
si sa mai quanto manca.

**L'ordine non è per numero di lead**, ed è il punto. La provincia con più scadenze è
quasi sempre quella con gli enti più grandi, che sono anche quelli con i fornitori più
radicati: tanti lead e nessuno da sostituire. Il valore di una provincia tiene insieme
tre cose:

- **quanti enti restano** — a radice, non lineare, se no Roma e Milano mangiano il piano
- **quanto sono buoni i lead** — il punteggio di R18
- **quanto è piccolo l'uscente** — la quota di lead il cui fornitore attuale ha al
  massimo 8 contratti in tutta Italia

Il terzo è quello che nessuno guarda ed è il più predittivo: distingue un mercato
contendibile da uno presidiato.

Il ritmo è 18 enti a settimana, che non è un'ambizione: è il tetto `PEC_MAX_GIORNO` (20,
per non farsi marcare come spam) insieme al fatto che una risposta va lavorata a mano.

Oggi: **633 enti in 52 province, 36 settimane** a questo ritmo. `--prossimo` stampa il
comando esatto da lanciare, che è la differenza fra un piano e una tabella che nessuno
traduce in azione. L'anti-duplicato di R20 salta da solo chi è già stato contattato.

### R25 — Funnel completo, non solo la risposta · ✅ FATTO 2026-09-07

Il tracciamento si fermava a `risposta`, e *«tasso di risposta 8%»* non dice se il canale è
**redditizio**. Ora gli stati arrivano fino al fatturato.

I nomi sono quelli degli stage **GoHighLevel già in uso sui clienti** — Discovery Call
Fissata, Discovery Call Fatta, Offerta, Vendita. Non è pedanteria: quando R26 porterà i lead
nel CRM, l'import sarà un copia-incolla invece che una mappatura da riscrivere e da tenere
allineata.

`radar.invio` guadagna una colonna data per stadio più `motivo_perdita`, `valore_offerta` e
`valore_vendita`. Le date separate servono a misurare **quanto si resta fermi in uno
stadio**, che è l'unico modo per capire dove il funnel perde; una sola colonna "stato"
avrebbe detto solo dove sono adesso.

#### Tre scelte che cambiano cosa si riesce a misurare

**Il conteggio è cumulativo, non per stato corrente.** Chi è arrivato a Vendita è passato
anche da Offerta. Contando solo lo stato attuale, gli stadi intermedi sembrerebbero vuoti
proprio quando le cose vanno bene.

**Due percentuali per stadio, non una.** Sul totale (dove sono finiti) e sullo stadio
precedente (dove si perde). La seconda è quella che indica il buco: *«14 consegnate, 78% del
passo prima»* dice che quattro PEC non sono mai arrivate, e nessuna percentuale sul totale
lo direbbe.

**L'importo separato fra offerto e vinto.** Confonderli falserebbe la conversione a valore,
che è il numero per cui esiste tutto il resto. Senza importi, *«3 vendite»* non dice se il
canale ripaga il tempo.

**Il motivo della perdita è una lista chiusa** (`gia-fornitore`, `no-budget`, `prezzo`,
`requisiti`, `tempi`, `silenzio`, `altro`). Con il testo libero, fra sei mesi *«già
fornito»* e *«hanno già un fornitore»* sarebbero due righe diverse in un conteggio, e il
motivo più frequente non si vedrebbe.

#### Dove si usa

- **console**, striscia sopra le righe di ogni lotto, e un menù *avanza…* per riga —
  Offerta e Vendita chiedono l'importo, «persa» chiede il perché
- `python invii.py --funnel` (`--tutti` per tutti i lotti insieme)
- `radar.v_funnel`, una riga per lotto, per n8n

Console e riga di comando scrivono la stessa riga con la stessa mappa stato→colonna: se
divergessero, il funnel conterebbe due volte.

Oggi dice quello che c'è da dire: 18 partite, 0 consegnate confermate, 0 risposte. Il
canale non ha ancora prodotto niente, e adesso si vede.

---

## Sprint 7 — Tenuta

### R22 — Backup del database · ✅ FATTO 2026-09-06

Il piano Supabase Free **non ha point-in-time recovery**, e `push_supabase.py`
fa TRUNCATE + COPY: un push andato male su una fonte corrotta cancella tutto e
non c'è undo. Il SQLite locale è la copia di riserva, ma sta su una macchina
sola. Dump mensile compresso, tre generazioni, fuori da OneDrive.

Costa un'ora e copre lo scenario in cui si perde tutto.

### R23 — Test sui punti che si sono già rotti · ✅ FATTO 2026-09-07

`ingestion/test_regressioni.py`, 39 casi, solo libreria standard. Non test in generale:
test **sui bug veri di questo progetto**. Un test che non protegge da un errore osservato
invecchia male — dice che il codice fa quello che fa, e passa anche quando il
comportamento è sbagliato. Qui ogni classe ha una data e una storia.

| Classe | Il guasto, e cosa era costato |
|---|---|
| `Categorie` | un `` di troppo aveva dimezzato la copertura senza dare errore — qui c'è anche una soglia sul database vero |
| `ParsingEnv` | `.env.local` con chiave ripetuta o BOM del Blocco note: una serata |
| `Mascheramento` | la password nei traceback che `console_live` rimanda al browser |
| `FormatoPEC` | il formato dei `.txt`, che si scopre rotto con una PEC in mano |
| `MediaCheIgnoraIZeri` | `avg()` salta i NULL: 26,40% invece di 4,72%, un fattore cinque sul numero che il prodotto vende |
| `AggregazionePerCIG` | gli RTI gonfiavano il valore del 99,8% |
| `DownloadTroncato` | uno ZIP a metà messo in cache come buono: 47.038 CIG mancanti |
| `CodiciFiscali` | P.IVA a 10 cifre e forme societarie: 41 rinnovi su 660 letti come porte aperte |
| `Funnel` | gli stadi cumulativi, e le due mappe stato→colonna che devono restare allineate |
| `DatiPersonali` | test di **disciplina**: se qualcuno carica i nominativi senza passare da `liceita.md`, questo se ne accorge prima della produzione |
| `UfficiRilevanti` | i falsi amici: «Anagrafe – servizi demografici informatizzati» |

Girano **in testa al piano mensile**, insieme a `sicurezza.py --breve`: durano secondi e
stanno prima delle quattro ore di ingestione. Se una regex è stata stretta, meglio saperlo
adesso che dopo aver riscritto il database con una classificazione dimezzata.

Al primo giro hanno già trovato un difetto vero: `leggi_dsn()` usciva con un `return`
dentro il `for` e lasciava `.env.local` aperto — su Windows un handle aperto impedisce di
riscrivere il file mentre il processo gira, che è esattamente quello che serve fare per
ruotare la password.

### R24 — Guida d'uso in una pagina · ✅ FATTO 2026-09-07

`docs/guida.md`. C'era molta documentazione tecnica e nessuna operativa: chi apre la console
senza aver seguito i sei sprint vede numeri e non sa da dove cominciare.

Dieci minuti di lettura, nessun gergo. La struttura è la domanda vera di chi lavora, non
l'architettura del sistema:

| | |
|---|---|
| cos'è, in tre righe | perché la PEC e non l'email |
| **la cosa da fare ogni giorno** | accendi la console, leggi la barra grigia, fai quello che dice |
| come si legge un lead | le fasce di punteggio, e perché tre quarti vanno scartati |
| prima di chiamare | clicca il nome dell'ente: se dice *chiuso*, non chiamare |
| il ciclo di un contatto | genera → invia → ricevute → i cinque stadi del funnel |
| il funnel | perché gli importi vanno inseriti |
| cosa succede da solo | i due job, e come si controlla che siano vivi |
| quando qualcosa non torna | cinque sintomi, cinque risposte |

Due cose ci finiscono di proposito, perché sono quelle che il sistema **non** può impedire:
una PEC per ente anche cambiando lotto, e non modificare a mano i file generati.

---

## Ordine consigliato

```
FATTI   R1 → R2 → R3 → R4 → R7 → R14 → R7b → R14b
        R22 → R20 → R15 → R16      il ciclo dell'invio e' chiuso
        R9 → R10 → R11             il sistema si mantiene da solo

        R17                        sappiamo se il prodotto vale: 4,7%
        R18 → R19                  i dati sono diventati un giudizio
        R24 → R25                  il team lo usa, e si vede se rende

        R13 → R23                  sicurezza misurata, guasti sotto test
        R28 → R27                  a chi scrivere, e in che ordine
        R5 → R6                    TED: le gare aperte, non solo previste
        R8 → R21                   il materiale commerciale

VALUTATI E SCARTATI
        R12                        smartcig: ne' CPV ne' data di fine

RINVIATO
        R26                        GoHighLevel: si paga, e con 0 risposte
                                   non c'e' ancora niente da tracciarci

APERTO, E NON E' UN LAVORO
        R28 (parte 2)              il nome del responsabile: 92,7% di
                                   copertura, ma e' dato personale e passa
                                   da docs/liceita.md §7
```

**La domanda vera, adesso.** La roadmap tecnica e' finita. Restano tre cose, e nessuna
delle tre e' codice:

1. **Avast** blocca la lettura delle ricevute PEC (`ingestion/diagnosi_tls.py` dice
   esattamente perche'). Le consegne sono confermate a mano, ma il canale automatico e'
   fermo.
2. **18 consegnate, 0 risposte.** Il messaggio arriva e non produce niente. R28 ha
   escluso che si risolva con un ufficio diverso (2,7% di copertura utile); resta il nome
   del responsabile, che e' una decisione sul trattamento dei dati personali.
3. **Le referenze** per l'allegato di R21. Due o tre righe vere: nessuno puo' scriverle
   al posto di chi le ha.


**Perché in quest'ordine.**

`R22` (backup) prima di tutto perché costa un'ora e copre la perdita totale: il
piano Free non ha recovery e il push fa TRUNCATE. `R20` subito dopo perché evita
la figuraccia della PEC doppia, e costa altrettanto poco.

Poi `R15`+`R16`: senza leggere le ricevute non sai neanche se le PEC arrivano, e
senza sollecito butti via la parte di risposte che arriva al secondo contatto.
Sono i due pezzi che trasformano l'invio in un processo.

`R17` viene prima di `R18` perché il punteggio ha bisogno di sapere com'è finita
davvero una scadenza — altrimenti si sta indovinando.

`R28` (nomi dei RUP) resta indietro **finché il canale PEC non è misurato**: se i
primi venti invii danno zero risposte, sale in cima, perché vorrà dire che il
protocollo non porta a chi decide. Se ne danno due o tre, non serve.

Il criterio generale non cambia: **prima si rende affidabile ciò che c'è**, poi si
aggiungono fonti. TED porta dati nuovi ma su ~2,2% del mercato.
