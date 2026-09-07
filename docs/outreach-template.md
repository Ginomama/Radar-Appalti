# Template di outreach via PEC (task R7)

> Per i 20 destinatari di `docs/lista-outreach.csv`.
> Registro adattato: la `brand-voice` di FlowLine usa il "tu" e un tono informale,
> giusto per le PMI ma sbagliato verso un protocollo generale di una PA. Resta il
> resto della voce — linguaggio semplice, numeri concreti, nessuna promessa vaga.

## L'intuizione che cambia il messaggio

Il primo istinto è chiedere di partecipare a una gara. **È l'ask sbagliato**: l'88% degli
affidamenti IT alla PA è diretto, quindi la gara spesso non ci sarà proprio.

Ma per l'affidamento diretto l'ente deve comunque fare una verifica di mercato, e molti
enti tengono un **elenco di operatori economici** da consultare. Quindi la richiesta
giusta, quella che un protocollo può davvero smistare e lavorare, è:

> «Inseriteci fra gli operatori da consultare per questa categoria.»

È una richiesta procedurale, non commerciale. Non chiede una decisione, non promette
nulla, e cade dentro un processo che l'ufficio già conosce. È molto più probabile che
arrivi a destinazione di una presentazione aziendale.

⚠️ **Prerequisito**: essere abilitati su MEPA/Consip per le categorie pertinenti. Molti
enti acquistano solo da lì, e senza abilitazione la richiesta si blocca comunque.

---

## Template

**Oggetto** — deve permettere allo smistamento di capire dove mandarlo:

```
Richiesta iscrizione elenco operatori economici — servizi informatici (CIG [CIG] in scadenza)
```

**Corpo**:

```
Spett.le [ENTE]

con la presente FlowLine, operatore economico attivo nei servizi di
automazione dei processi e integrazione di sistemi informativi, chiede di
essere inserita fra gli operatori economici da consultare per la categoria
[CATEGORIA].

La richiesta nasce da una verifica sui dati aperti ANAC, da cui risulta in
scadenza il [DATA] il contratto CIG [CIG], relativo a "[OGGETTO]",
attualmente affidato a [FORNITORE USCENTE].

Cosa facciamo, in concreto: costruiamo automazioni che collegano fra loro i
sistemi già in uso — gestionali, protocollo, banche dati, posta — eliminando
i passaggi manuali ripetitivi fra un applicativo e l'altro. Non vendiamo
licenze né abbonamenti: il risultato resta di proprietà dell'ente, che può
farlo mantenere anche da altri fornitori.

Se ritenete utile un approfondimento, possiamo trasmettere una presentazione
delle competenze e delle referenze, oppure una proposta tecnica senza
impegno sull'ambito sopra indicato.

Il recapito PEC è stato reperito dall'Indice dei domicili digitali della PA
(IndicePA); i dati sui contratti provengono dagli open data ANAC
(CC BY-SA 4.0). Restiamo a disposizione per qualsiasi chiarimento e per
l'eventuale cancellazione dai nostri contatti.

Cordiali saluti

[NOME] — FlowLine
[contatti] · [P.IVA]
```

---

## Perché è scritto così

**"Chiede di essere inserita fra gli operatori da consultare"** — l'unica azione
richiesta, e cade in un processo che l'ufficio già gestisce.

**Il CIG e il fornitore uscente nel corpo** — è ciò che distingue questa PEC da una
presentazione qualunque. Dimostra che avete guardato la loro situazione specifica, non
che state mandando mille email uguali.

**"Il risultato resta di proprietà dell'ente"** — è il differenziatore FlowLine dalla
`brand-voice`, e verso una PA vale il doppio: parla direttamente al problema del lock-in
sul fornitore, che è una preoccupazione reale e ricorrente nel procurement pubblico.

**La riga sulla provenienza dei dati** — sembra burocrazia, è invece la parte che rende
il messaggio legittimo anziché inquietante. Dice che l'informazione è pubblica, che non
l'avete presa da chissà dove, e assolve gli obblighi di trasparenza di cui a
`docs/liceita.md`. Non toglietela.

**Nessun nome di persona** — né vostro interlocutore né RUP. Non li abbiamo nei dati e
non li useremmo comunque, per la decisione presa in R3.

---

## Varianti per categoria

Sostituire il paragrafo "Cosa facciamo" secondo la categoria del lead.

**Dati e analytics**
> Costruiamo flussi che raccolgono dati da fonti diverse, li normalizzano e li
> rendono consultabili senza estrazioni manuali ricorrenti.

**Manutenzione e assistenza**
> Affianchiamo i gestionali esistenti automatizzando le attività ripetitive che oggi
> richiedono intervento manuale — caricamenti, riconciliazioni, notifiche, controlli.

**Sviluppo software**
> Sviluppiamo applicativi su misura e integrazioni fra sistemi esistenti, consegnando
> codice e documentazione all'ente.

**Gestione documentale**
> Automatizziamo i passaggi fra protocollo, conservazione e gestionali: acquisizione,
> smistamento, notifiche e controlli di completezza.

**Consulenza IT**
> Analizziamo i processi esistenti e individuiamo dove l'automazione riduce tempi e
> errori, con una stima delle ore recuperate prima di qualsiasi sviluppo.

---

## Esempio compilato

Destinatario **Regione Emilia-Romagna** (3 contratti, il primo fra 36 giorni):

```
Oggetto: Richiesta iscrizione elenco operatori economici — servizi
         informatici (CIG in scadenza 10/2026)

Spett.le Regione Emilia-Romagna

con la presente FlowLine, operatore economico attivo nei servizi di
automazione dei processi e integrazione di sistemi informativi, chiede di
essere inserita fra gli operatori economici da consultare per le categorie
"dati e analytics" e "manutenzione applicativa".

La richiesta nasce da una verifica sui dati aperti ANAC, da cui risultano in
scadenza nei prossimi mesi tre contratti nell'area dei servizi informativi,
attualmente affidati a Redturtle Technology, REP e Wegg.
[...]
```

---

## Come misurare se funziona

Venti PEC sono un campione piccolo ma sufficiente a distinguere due scenari opposti:

| Risposte su 20 | Cosa significa |
|---|---|
| 0 | il canale PEC-protocollo non arriva a nessun decisore. Vale la pena cercare il RUP (task R1.4) o cambiare canale |
| 1–2 | il canale funziona: il tasso è normale per un primo contatto a freddo |
| 3+ | segnale forte, conviene industrializzare l'invio |

Annotare data di invio ed esito per ciascuna: senza quello, dopo tre settimane non
saprete distinguere "non hanno risposto" da "non ho mandato".

⚠️ **Le prime venti mandatele a mano.** Automatizzare l'invio prima di sapere se il
messaggio funziona significa solo sbagliare più in fretta.
