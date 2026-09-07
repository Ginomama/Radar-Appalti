# Radar Appalti — guida d'uso

Dieci minuti di lettura. Poi si lavora.

---

## Cos'è, in tre righe

Ogni mese scarichiamo da ANAC tutti i contratti informatici della pubblica
amministrazione italiana. Il Radar guarda **quando scadono** e dice **a chi
conviene scrivere**: un ente che sta per restare senza fornitore è disposto ad
ascoltare, gli altri no.

Il contatto si fa via PEC, perché è l'unico canale che una PA è obbligata a
leggere.

---

## La cosa da fare ogni giorno (cinque minuti)

**1. Accendi la console.**

```bash
python ingestion/console_live.py
```

Apre `http://127.0.0.1:8420/`. Resta acceso finché lavori; si spegne con Ctrl+C.

**2. Leggi la barra grigia in cima al tracker.**

C'è scritto in italiano cosa fare adesso, e cambia da sola:

| Se dice | Fai |
|---|---|
| *«2 hanno risposto»* | **richiamali oggi**, è la cosa che vale di più |
| *«1 PEC non è arrivata»* | casella piena o indirizzo morto: verifica il recapito prima di riprovare |
| *«3 consegnate da oltre 12 giorni senza risposta»* | manda i solleciti |
| *«4 invii sono fermi da oltre 21 giorni»* | chiudili: dopo tre settimane non risponde più nessuno |
| *«Prossimo passo: inviare le 6 rimanenti»* | invia |
| *«Niente in sospeso»* | genera un lotto nuovo |

**3. Fai quello che dice.** È tutto qui il lavoro quotidiano.

---

## Come si legge un lead

La tabella è già ordinata dal migliore. La colonna dei **punti** è il numero da
guardare — è la probabilità che quella scadenza diventi una porta aperta,
moltiplicata per quanto quel lavoro lo sappiamo fare noi.

| Colore | Punti | Cosa significa |
|---|---|---|
| 🟢 | 60+ | eccezionale — 193 in tutto il database |
| 🔵 | 35–59 | ottimo |
| 🟡 | 18–34 | buono |
| ⚪ | sotto 18 | lascia perdere |

**Che tre quarti dei lead non valgano la pena è il risultato giusto**, non un
difetto del sistema. Serve a non sprecare PEC.

### Prima di chiamare: clicca il nome dell'ente

Si apre la scheda. La prima riga è quella che conta:

> *«Ente che si muove: 15 porte aperte su 79 scadenze osservate (19%). Vale la
> pena insistere.»*

oppure

> *«Chiuso: 1 porta aperta su 44 (2%). Rinnova quasi sempre a chi ha già, per
> quanto spenda.»*

**Se dice "chiuso", non chiamare.** Un ente che in tre anni non ha mai cambiato
fornitore non è un lead, per quanto grande sia il contratto. Sotto trovi quanto
spendono, chi glieli tiene oggi, ogni quanto ricomprano e cosa gli abbiamo già
scritto.

---

## Il ciclo di un contatto

```
generi → invii → il gestore consegna → risponde → call → offerta → vinto/perso
```

**Sapere dove andare** — non scegliere le province a caso:

```bash
python ingestion/territorio.py --prossimo
```

Stampa quale provincia tocca e **il comando esatto** per generare il lotto. L'ordine
non è per numero di lead: mette avanti dove il fornitore uscente è piccolo, cioè
dove qualcuno si può davvero sostituire.

**Generare un lotto** — prepara le PEC già scritte, una per ente:

```bash
python ingestion/genera_pec.py --limite 18 --provincia ROMA
```

**Inviare** — dalla console, bottone *invia PEC* su ogni riga. Prima mostra
l'anteprima: leggila, parte davvero solo dopo la conferma.

**Le ricevute si leggono da sole** ogni mattina alle 8:30. In console compare
*consegnata*, oppure il motivo per cui non è arrivata.

**Far avanzare un contatto** — il menù *avanza…* in fondo alla riga:

| Stadio | Quando |
|---|---|
| Discovery Call Fissata | hanno accettato di sentirsi |
| Discovery Call Fatta | la call è avvenuta |
| Offerta | abbiamo mandato un preventivo — **chiede l'importo** |
| Vendita | firmato — **chiede l'importo** |
| persa | finita male — **chiede il perché**, da una lista |

Sono gli stessi nomi degli stage che usiamo su GoHighLevel per i clienti: se un
domani il funnel passa nel CRM, si copia e basta.

L'importo va messo. Senza, *«3 vendite»* non dice se il canale ripaga il tempo
che ci mettiamo.

Il motivo della perdita va scelto dalla lista, non scritto a mano: con il testo
libero, fra sei mesi *«già fornito»* e *«hanno già un fornitore»* sono due righe
diverse in un conteggio, e il motivo più frequente non si vede più.

---

## Il funnel

Sopra le righe di ogni lotto c'è la striscia con i numeri per stadio, e sotto
ogni numero la percentuale **rispetto allo stadio precedente**. Quella è la
percentuale da leggere: dice dove si perde.

> 18 destinatari → 18 inviate → 14 consegnate (78%) → 2 risposte (14%) → …

Il tasso di risposta dice se il messaggio funziona. Solo il funnel completo dice
se il canale è **redditizio**, ed è il motivo per cui gli importi vanno inseriti.

Lo stesso conto da riga di comando:

```bash
python ingestion/invii.py --lotto pec-marche --funnel
```

---

## Cosa succede senza che nessuno lo accenda

| Quando | Cosa |
|---|---|
| ogni giorno 08:30 | backup, lettura ricevute PEC, notifica Telegram dei lead nuovi |
| il 3 di ogni mese 07:00 | scarico ANAC, ricalcolo di categorie e punteggi, aggiornamento del database |

Controllo che siano vivi:

```bash
python ingestion/job.py --stato
```

Se una delle due dice *mai eseguita* o un esito diverso da 0, chiedi a
Leonardo. Il mensile dura fra una e quattro ore: è normale.

---

## Quando qualcosa non torna

| Sintomo | Cosa fare |
|---|---|
| la console dice *Database non raggiungibile* | Supabase in pausa o password cambiata — chiedi a Leonardo |
| i dati sembrano vecchi | in alto a destra c'è la data: se è di oggi sono freschi |
| *consegna non confermata* su tutte le PEC | l'antivirus blocca la lettura delle ricevute — problema noto |
| una PEC risulta inviata ma non l'hai mandata tu | l'ha mandata qualcun altro: la console e il database sono la stessa cosa |
| un ente non compare | vediamo solo contratti informatici sopra una certa soglia, e solo dal 2021 |

---

## Tre regole

1. **Una PEC per ente, mai due.** Il sistema blocca i duplicati, ma se cambi
   lotto e provincia il blocco non può indovinare che è lo stesso ufficio.
2. **Non modificare i file in `docs/pec*/` a mano.** Sono generati; la modifica
   sparisce al giro dopo. Se il testo non va bene si cambia il modello.
3. **Le password non stanno in nessun file che si apre.** Se ti serve un
   accesso, chiedi — non copiarlo da un altro computer.

---

## Le due domande a cui il Radar risponde davvero

**«A chi scrivo oggi?»** → la tabella, ordinata dai punti, filtrata per la tua
provincia.

**«Sta funzionando?»** → il funnel. Se dopo cinquanta PEC non c'è una call
fissata, non è il momento di mandarne altre cinquanta: è il momento di cambiare
il messaggio.
