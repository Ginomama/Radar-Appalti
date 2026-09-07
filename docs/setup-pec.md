# Invio PEC dalla console (task R7b)

> Il bottone **invia PEC** nella console apre un'anteprima e, alla conferma,
> spedisce davvero. Il messaggio parte e la riga si segna `inviata` nella
> stessa transazione, quindi console e `invii.py` non si disallineano.

## Prima di tutto: serve una casella PEC vera

Non basta una mail ordinaria. Molte PA rifiutano la posta non certificata
sulla propria PEC, e comunque senza PEC in partenza il messaggio non ha valore
legale né produce ricevute. Se hai partita IVA la casella dovrebbe già esistere
— è il domicilio digitale registrato in INI-PEC.

## Passo 1 — I dati SMTP del tuo provider

Ogni provider ha il suo server. Questi sono i più comuni come **punto di
partenza**: il valore autorevole è quello scritto nel pannello di
configurazione della tua casella, controllalo lì prima di incollarlo.

| Provider | Host | Porta |
|---|---|---|
| Aruba / PEC.it | `smtps.pec.aruba.it` | 465 |
| Legalmail (InfoCert) | `sendm.cert.legalmail.it` | 465 |
| Register.it | `smtps.pec.register.it` | 465 |
| Poste Italiane | `relay.poste.it` | 465 |

La porta 465 usa SSL diretto, qualunque altra porta fa STARTTLS: lo script
sceglie da solo in base al numero, non devi indicare nient'altro.

## Passo 2 — Credenziali in `.env.local`

Aggiungi a `ingestion/.env.local`, sotto le righe che ci sono già:

```
PEC_HOST=smtps.pec.aruba.it
PEC_PORT=465
PEC_USER=tuo.indirizzo@pec.esempio.it
PEC_PASSWORD=la-password-della-casella-pec
PEC_NOME=Leonardo Foschi
PEC_MAX_GIORNO=20
```

`PEC_NOME` è il nome visualizzato accanto all'indirizzo. `PEC_MAX_GIORNO` è
facoltativo: senza, il limite è 20.

Il file è git-ignored e verificato tale. La password non compare mai nel
codice, e ogni errore SMTP passa da `maschera()` prima di essere mostrato —
un traceback di `smtplib` può contenerla in chiaro.

## Passo 3 — Il mittente in firma · ✅ fatto

`MITTENTE` in `ingestion/genera_pec.py` è compilato, e i due lotti sono stati
rigenerati il 4 settembre 2026. In calce a ogni PEC ora c'è:

```
Leonardo Foschi
P.IVA IT00000000000
tel. 000 0000000 — contatto@esempio.it
```

⚠️ **La casella `@flowline.it` va letta ogni giorno.** È il recapito che
finisce nell'anagrafica fornitori dell'ente, ed è lì che scriveranno fra mesi
quando faranno un'indagine di mercato. Un indirizzo professionale non
presidiato è peggio di un gmail: la PA risponde una volta sola. Se vivi dentro
Gmail, imposta l'inoltro `flowline.it` → gmail più l'"invia come".

### Rigenerare i lotti

```bash
python ingestion/genera_pec.py --cartella pec
python ingestion/genera_pec.py --cartella pec-marche --provincia "ANCONA,MACERATA,PESARO E URBINO,ASCOLI PICENO,FERMO" --giorni-min 30 --giorni-max 365
```

Rigenerare **non** azzera lo stato di ciò che è già stato inviato. Cambia però
la numerazione, perché la finestra delle scadenze si sposta ogni giorno: il 2
settembre il lotto Marche aveva 15 destinatari, il 4 ne aveva 18, e diversi
enti hanno cambiato posizione. I file con la numerazione superata vengono
eliminati dallo script, così nella cartella non restano due versioni della
stessa lettera.

**Non rigenerare a metà di una tornata di invii.** Le righe già inviate
mantengono il loro stato, ma i numeri progressivi si riferiranno a enti
diversi, e `--inviata 7` non vorrà più dire quello che credi. Finisci il giro,
poi rigenera.

## Passo 4 — Prova senza spedire

```bash
python ingestion/pec_smtp.py --lotto pec-marche --invia 1 --prova
```

Stampa destinatario, oggetto e corpo esatti, ed elenca i motivi per cui non
partirebbe. Nessun messaggio lascia la casella.

## Passo 5 — Inviare

**Dalla console**, un destinatario per volta: `invia PEC` → si apre
l'anteprima con destinatario, oggetto e testo → `Conferma invio`. La riga
diventa verde e il Message-ID finisce nelle note.

**Da riga di comando**, in blocco:

```bash
python ingestion/pec_smtp.py --lotto pec-marche --invia 1-15
```

Con più di un destinatario mostra prima l'elenco completo e chiede di scrivere
`INVIA` per procedere. Se una spedizione fallisce si ferma lì: le successive
non partono, e nessuna riga resta marcata a vuoto.

## I quattro controlli

Una PEC ha valore legale, viene protocollata dall'ente e non si ritira. Questi
controlli costano zero tempo e coprono ciò che dopo non si corregge:

1. **Segnaposto.** Se nel testo è rimasto un `[INSERISCI …]` non parte. È
   l'errore più probabile, perché `MITTENTE` nasce coi campi vuoti.
2. **Destinatario.** L'indirizzo scritto nel file deve coincidere con quello
   registrato in `radar.invio`. Se non coincide qualcosa è stato rigenerato a
   metà, e il messaggio andrebbe all'ente sbagliato.
3. **Doppio invio.** Una riga già inviata non riparte senza `--forza`. Una PEC
   doppia allo stesso protocollo è l'errore che si nota di più.
4. **Tetto giornaliero.** Venti al giorno. Non è prudenza: i provider limitano
   la casella oltre una certa soglia, e il numero esatto non è pubblicato.

## Se esce `SSLCertVerificationError`

```
[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate
```

**Non è un problema di Aruba né di Python: è Avast.** Misurato il 4 settembre
2026 su questa macchina.

Avast intercetta le connessioni TLS e le rifirma con un proprio certificato.
Ne usa due diversi:

| Connessione | Firmato da | Nel magazzino di Windows? |
|---|---|---|
| `pypi.org:443` (web) | `Avast Web/Mail Shield Root` | sì |
| `smtps.pec.aruba.it:465` | `Avast Web/Mail Shield **Untrusted** Root` | **no, di proposito** |
| `smtp.gmail.com:465` | `Avast Web/Mail Shield **Untrusted** Root` | **no** |

Il secondo certificato si autodescrive come *"generated by Avast Antivirus for
untrusted server certificates"*. Avast lo usa quando **lui** non è riuscito a
validare il server, e non lo installa in nessun magazzino apposta: è il modo di
dire "non fidarti". Nessun programma può accettarlo, e **nessuna modifica al
codice può aggirarlo** senza disattivare del tutto la verifica — cioè spedire
le credenziali della casella PEC su un canale che ci è stato appena segnalato
come non verificato. Non si fa.

Che Avast dichiari non affidabile anche `smtp.gmail.com` dice che il problema è
suo, non dei server: il suo archivio di certificati è probabilmente vecchio o
danneggiato.

### Il rimedio

Sta dentro Avast, ed è una impostazione di sicurezza — falla tu:

1. **Avast → Menu → Impostazioni → Protezione → Componenti principali →
   Protezione posta** e togli la spunta a *Scansiona connessioni SSL/TLS*
   (in alcune versioni: *Protezione posta → Personalizza → SSL*).
2. In alternativa, **Impostazioni → Eccezioni** e aggiungi
   `smtps.pec.aruba.it`.
3. Se vuoi tenere la scansione, prova prima **Riparazione/aggiornamento di
   Avast**: se torna a validare gmail, tornerà a validare anche Aruba.

Poi riverifica con:

```bash
python ingestion/pec_smtp.py --lotto pec-marche --invia 1 --prova
```

Vale anche la pena saperlo a prescindere dall'errore: finché la scansione è
attiva, **Avast decifra e rilegge la tua posta certificata**. Su un canale che
ha valore legale è una cosa da decidere consapevolmente.

`pec_smtp.py` riconosce da solo la situazione e la spiega, invece di mostrare
il messaggio criptico di OpenSSL. Non ripiega mai su una verifica più
permissiva.

## Se esce `SMTPAuthenticationError: 535 5.7.8 Authentication failed`

**Con la verifica in due passaggi attiva, la password principale della PEC non
funziona più dai programmi di posta.** Serve una *password per programmi di
posta*, generata a parte. Questa è la causa nel nostro caso: indirizzo e
password erano corretti, ma Aruba rifiuta comunque le credenziali della
webmail su SMTP.

### Come generarla

1. Entra nella **gestione della casella** (`gestionemail.pec.it`) con le
   credenziali PEC, e conferma l'accesso dall'**app Aruba PEC** — è il secondo
   fattore.
2. Menu di sinistra → **Sicurezza** → **Password per programmi di posta**.
3. **Genera nuova password** → **Genera**.
4. **Copiala subito.** Chiusa la finestra non è più visibile: se la perdi devi
   generarne un'altra. È automatica, non la scegli tu.
5. Incollala in `PEC_PASSWORD` dentro `ingestion/.env.local`, al posto di
   quella attuale.

### ⚠️ Scade dopo 6 mesi

Questa password ha validità semestrale. Aruba avvisa prima della scadenza e
permette di prorogarla di altri sei mesi o di rigenerarla.

Conseguenza pratica: **due volte l'anno l'invio smetterà di funzionare con un
535**, e non sarà un guasto. `pec_smtp.py` lo dice esplicitamente nel messaggio
d'errore, scadenza inclusa. Se hai schedulato dei job, mettiti un promemoria a
cinque mesi.

### Le altre due cause, se non è questa

- **Password sbagliata delle due.** Aruba ne ha due distinte: quella
  dell'**Area Clienti** (`admin.aruba.it`, per gestire il servizio) e quella
  della **casella PEC** (`webmail.pec.it`). Solo la seconda ha a che fare con
  SMTP.
- **Indirizzo diverso da quello che credi.** Confronta `PEC_USER` con quello
  scritto nel pannello, carattere per carattere.

La verifica che chiude il discorso: entra in `webmail.pec.it` con le stesse
identiche credenziali del file. Se **non entri**, è la password. Se **entri**,
la password è giusta e ti serve quella dedicata ai programmi di posta.

Fonti: [verifica in 2 passaggi](https://guide.pec.it/gestione-account-pec/verifica-in-due-passaggi/caratteristiche.aspx)
· [come generare la password per i programmi di posta](https://guide.pec.it/gestione-account-pec/password/programmi-di-posta/come-generarla.aspx)

## Leggere le ricevute (obbligatorio, non facoltativo)

Ogni PEC genera ricevute automatiche: *accettazione*, *avvenuta consegna*,
oppure *errore di consegna*. Finché non le leggi, una PEC mai arrivata resta
`inviata` per sempre e aspetti una risposta che non può esistere.

```bash
python ingestion/pec_imap.py --leggi       # ultimi 30 giorni
python ingestion/pec_imap.py --stato       # esito tecnico di tutti gli invii
```

Aggiorna `consegnata_il`, `accettata_il` e porta a `non_consegnata` chi non è
arrivato. **Non cancella e non sposta niente**, e non marca i messaggi come
letti: una casella PEC è un archivio con valore probatorio, e la ricevuta di
consegna è la prova che l'ente ha ricevuto.

⚠️ **Serve l'eccezione Avast anche per l'IMAP.** Se hai escluso solo
`smtps.pec.aruba.it`, la lettura fallisce con lo stesso errore di prima:
aggiungi `imaps.pec.aruba.it`, o disattiva del tutto la scansione SSL della
Protezione posta.

### Configurazione

Nessuna, se il tuo provider è Aruba: l'host IMAP viene dedotto da `PEC_HOST`
(`smtps.` → `imaps.`) e le credenziali sono le stesse. Se il tuo gestore non
segue quello schema:

```
PEC_IMAP_HOST=imaps.esempio.it
PEC_IMAP_PORT=993
```

## Il secondo contatto

Il primo messaggio a freddo a una PA finisce al protocollo e spesso si ferma lì.
La maggior parte delle risposte arriva dal richiamo.

```bash
python ingestion/genera_pec.py --solleciti --cartella pec-marche
python ingestion/pec_smtp.py --lotto pec-marche --sollecito --invia 3,7,12
```

Il sollecito è corto — cinque righe — e cita la PEC precedente con la sua data.
Un secondo messaggio lungo quanto il primo si legge come un rinvio automatico.

Tre regole applicate dal codice:

- **parte solo verso chi ha ricevuto davvero** (`consegnata_il` valorizzata):
  sollecitare una PEC mai arrivata è rumore, quindi va eseguito prima
  `pec_imap.py --leggi`
- **dopo 12 giorni**, non prima: una PEC entra in protocollo e viene smistata
  in qualche giorno
- **uno solo per ente**. Un secondo richiamo è insistenza, e il codice lo rifiuta

## Non scrivere due volte allo stesso protocollo

Due controlli automatici, entrambi sulla **casella** e non sull'ente — lotti
diversi chiamano lo stesso ente con nomi diversi, ma la posta arriva sempre lì:

- `genera_pec.py` esclude gli enti già presenti in un altro lotto e dice quali
  (`--consenti-doppioni` per forzare)
- `pec_smtp.py` blocca l'invio se alla stessa casella è già partita una PEC
  negli ultimi **6 mesi** (`--forza` per scavalcare)

⚠️ **Non rigenerare un lotto a metà di una tornata di invii.** La numerazione si
sposta quando cambiano gli enti in finestra, e una riga già inviata finirebbe a
puntare a un ente diverso. Da oggi `genera_pec.py` se ne accorge e **si ferma**
prima di toccare il tracciamento, elencando le righe a rischio.

## Se il server non risponde dopo la conferma

La console lo dice esplicitamente e **non segna la riga**. Non rimandare
subito: controlla la ricevuta di accettazione nella tua casella PEC. Se c'è,
il messaggio è partito e va segnato a mano con
`python ingestion/invii.py --lotto <lotto> --inviata <n>`. Un reinvio alla
cieca crea il doppione.

## Un punto ancora aperto

L'uso commerciale dei recapiti presi da IndicePA è la seconda delle tre
domande elencate in [`liceita.md`](liceita.md) e non è ancora chiusa.
Automatizzare l'invio non cambia la sostanza — restano PEC scritte una per
una, a persone giuridiche, con la fonte dichiarata e l'opzione di
cancellazione in calce — ma rende più facile alzare i volumi, ed è a volume
alto che la domanda diventa concreta.
