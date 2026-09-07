# Liceità dell'uso dei dati (task R3)

> Redatto 2026-08-26. **Non è un parere legale**: io non sono un avvocato e questo
> documento non lo sostituisce. È una mappa fattuale — cosa trattiamo davvero, con
> quali licenze, e dove servono decisioni che vanno prese con un professionista.
> Serve a rendere quella consulenza breve e mirata invece che esplorativa.

## 1. Cosa trattiamo davvero — inventario misurato

| Dato | Fonte | Natura |
|---|---|---|
| CIG, oggetto, importi, date, procedura | ANAC | dato di attività amministrativa |
| Denominazione e CF della stazione appaltante | ANAC + IndicePA | ente pubblico, non persona |
| PEC dell'ente | IndicePA | recapito **organizzativo** |
| Denominazione e CF degli aggiudicatari | ANAC | ⚠️ **in parte persone fisiche** |
| Nome e cognome dei referenti | — | **non memorizzato, per scelta** |

### ⚠️ Il punto non ovvio: trattiamo dati personali

Misurato sul database: **2.464 codici fiscali su 22.018 (11,2%)** sono in formato a
sedici caratteri, cioè persone fisiche. Le denominazioni lo confermano — *"ORLANDINI
MAURO"*, *"BERTONI STEFANO"*, *"SUPER TITE DI VILLA MARCO"*: ditte individuali,
artigiani e professionisti che hanno vinto affidamenti pubblici. Fra i lead a 90 giorni
ce ne sono **20**.

Nome e codice fiscale di una persona fisica sono dati personali anche quando la persona
agisce come impresa individuale, e anche quando la fonte è pubblica. **La pubblicità del
dato non è di per sé una base giuridica per qualunque trattamento successivo**: cambia il
modo in cui lo si è ottenuto, non lo scopo per cui lo si usa.

Abbiamo invece **deliberatamente escluso** `nome_resp`, `cogn_resp` e `titolo_resp` di
IndicePA (i rappresentanti legali degli enti): erano disponibili e non li abbiamo
caricati, in attesa di questa verifica.

## 2. Licenze delle fonti — verificate

| Fonte | Licenza | Obbligo |
|---|---|---|
| ANAC open data | **CC BY-SA 4.0** | attribuzione **+ condividi allo stesso modo** |
| IndicePA | **CC BY 4.0** | attribuzione |
| TED | riuso consentito, da verificare nel dettaglio | attribuzione |

### ⚠️ Il nodo più serio non è la privacy: è il ShareAlike

ANAC rilascia in **CC BY-SA 4.0**. La clausola *ShareAlike* impone che le opere derivate
siano distribuite con la stessa licenza. Per un prodotto che si vuole vendere è la
domanda più importante di tutto il progetto, e ha tre risposte possibili a seconda di
come si qualifica ciò che costruiamo:

1. **Semplice riproduzione** dei dati ANAC → BY-SA si applica, il derivato va rilasciato
   con la stessa licenza.
2. **Raccolta (collection)** che accosta fonti diverse senza fonderle → la licenza si
   applica alla parte ANAC, non necessariamente all'insieme.
3. **Prodotto che vende l'accesso e l'elaborazione**, non il dato → posizione diversa
   ancora, ma da argomentare.

Vale anche la disciplina italiana sul riutilizzo dei dati pubblici (d.lgs. 36/2006 e
successivi), che convive con la licenza.

**Non ho gli elementi per risolverla, e non è una questione da risolvere a intuito.** È
la prima domanda da portare al professionista, prima di firmare un contratto con un
cliente.

### ✅ Decisione presa il 2026-08-26 — uso come motore interno

Finché il radar resta **motore interno** — intelligence e outreach diretto di FlowLine
verso enti e partner — **non c'è redistribuzione del dataset**, quindi la clausola
ShareAlike non si attiva e siamo coperti con la sola attribuzione.

Il punto legale va riaperto **prima** di pacchettizzarlo come SaaS o prodotto vendibile
a terzi, perché è lì che scatta la distribuzione di un'opera derivata. In pratica: R7
(outreach nostro) procede, R8 (report a terzi) va valutato caso per caso — un report
mostrato a un partner non è la stessa cosa di un abbonamento venduto.

## 3. L'uso della PEC per contattare gli enti

La PEC istituzionale è un recapito dell'organizzazione, non di una persona. Ma restano
due questioni distinte, che è bene non confondere:

- **A chi si scrive**: un ente pubblico, quindi non si applica la disciplina sul
  marketing verso persone fisiche.
- **Perché quel recapito è pubblicato**: IndicePA espone le PEC per finalità di
  comunicazione istituzionale e interoperabilità. Usarle per solicitazione commerciale
  non è automaticamente equivalente. Le regole italiane sulle comunicazioni commerciali
  non sollecitate (art. 130 del Codice privacy e provvedimenti del Garante) vanno
  verificate su questo punto specifico.

Chi vende alla PA lo fa comunemente via PEC, ma "si fa" non è un argomento giuridico.

## 4. Mitigazioni già adottate

- ❌ Non memorizzati i nominativi dei referenti degli enti.
- ✅ Aggiunto il flag `fornitore_persona_fisica` sulle scadenze, così i 20 lead con
  fornitore uscente persona fisica sono **filtrabili ed escludibili in un colpo solo**.
- ✅ Le fonti sono tracciate: ogni riga ha `src_file`, e `ingestion_log` registra da dove
  arriva.
- ✅ Nessun dato originale su Supabase: solo derivato, rigenerabile.

## 5. Mitigazioni da valutare

- **Attribuzione visibile**: qualunque output verso terzi (report, dashboard, email)
  dovrebbe citare ANAC e IndicePA come fonti, con licenza. È dovuto da entrambe le
  licenze e costa una riga a piè di pagina.
- **Escludere per default i fornitori persona fisica** dall'outreach: il flag c'è, si
  tratta di decidere se il default è includerli o escluderli. Suggerisco escluderli.
- **Informativa**, se si trattano dati personali per finalità commerciali: gli artt. 13-14
  GDPR prevedono obblighi di trasparenza anche quando il dato non è raccolto dalla
  persona, con esenzioni da valutare.
- **Minimizzazione**: valutare se serve conservare il CF degli aggiudicatari persone
  fisiche o se basta la denominazione.

## 6. Cosa chiedere al professionista

Tre domande, in ordine di impatto:

1. **La clausola ShareAlike di ANAC come vincola un prodotto commerciale costruito su
   quei dati?** È la domanda che può cambiare il modello di business.
2. **Contattare via PEC un ente pubblico con un'offerta commerciale, usando recapiti
   presi da IndicePA, è legittimo?** Se sì, con quali cautele.
3. **Quale base giuridica per il trattamento dei dati degli aggiudicatari persone
   fisiche**, e quali obblighi informativi ne derivano.

Portare questo documento e `docs/fonti-dati.md`: contengono l'inventario esatto di cosa
si tratta e da dove viene, che è il 90% del lavoro preparatorio.

## 7. R28 — il nome del responsabile: cosa cambia, e cosa serve deciderne

Misurato il **7 settembre 2026**, dopo che il primo lotto ha dato 18 PEC inviate, 18
consegnate e **zero risposte**. Le consegne sono confermate: il messaggio arriva, e si
ferma dopo. La spiegazione più probabile è il destinatario, non il testo.

Ci sono tre strade per arrivare a chi decide invece che al protocollo. Sono state
misurate tutte e tre prima di scegliere, perché due su tre non funzionano.

| Strada | Copertura sul nostro bacino (18.707 enti) | Dati personali? |
|---|---|---|
| PEC di un ufficio informatico **diversa** da quella dell'ente | **2,7%** (514 enti) | no |
| **Nome** dell'ufficio informatico, da mettere nell'oggetto | 97,3% — ma il 95% è la stessa dicitura di legge | no |
| **Nome e recapito del responsabile** (RTD) | **92,7%**, di cui 99,6% con email | **sì** |

**Fatto subito, senza gate:** le prime due. `ingestion/uffici.py` carica gli uffici da
IndicePA tenendo **solo le colonne organizzative** — `nome_resp`, `cogn_resp`,
`mail_resp` e `tel_resp` sono escluse esplicitamente nel codice, non per dimenticanza.
`genera_pec.py` mette il nome dell'ufficio in testa all'oggetto, che è la riga che legge
chi smista. È gratis e si misura sul prossimo lotto.

Onestamente: è un miglioramento piccolo. Il 95% degli enti dichiara *«Ufficio per la
transizione al Digitale»*, che ogni PA ha dovuto istituire per il CAD — corretto ma non
distintivo, e chi smista lo sa.

### La terza strada è l'unica che sposterebbe davvero l'ago, ed è una decisione tua

Il dataset `responsabili-della-transizione-al-digitale` di IndicePA dà nome, cognome e
email personale del RTD per il **92,7%** dei nostri enti. È pubblicato in open data con
licenza CC BY 4.0, e la pubblicazione è **obbligatoria per legge** (CAD, art. 17): non è
un dato sfuggito, è un recapito che la norma vuole conoscibile proprio perché quel
responsabile sia raggiungibile.

Questo però riguarda la *fonte*, non lo *scopo*. Restano da decidere tre cose, e sono
esattamente le domande del §6:

1. **Base giuridica.** Per un contatto commerciale la strada normale è il legittimo
   interesse (art. 6.1.f GDPR), che richiede una valutazione scritta di bilanciamento:
   l'interesse nostro contro l'aspettativa ragionevole di quella persona. Il fatto che
   la sua funzione istituzionale sia proprio ricevere proposte sulla transizione
   digitale gioca a favore, ma va scritto, non dato per buono.
2. **Informativa (art. 14).** Dato raccolto non dall'interessato: l'informativa va
   fornita, con le esenzioni del comma 5 da verificare. In pratica: un paragrafo nella
   PEC e una pagina raggiungibile.
3. **Minimizzazione.** Serve l'email personale, o basta nominare la persona scrivendo
   comunque alla PEC istituzionale? La seconda ottiene quasi lo stesso effetto di
   smistamento con un trattamento molto più leggero, ed è probabilmente la risposta
   giusta.

**Cosa è stato fatto nel frattempo:** il dataset è stato scaricato per contare la
copertura e **cancellato subito dopo**. Nessun nome è su disco, nessuna colonna
personale è nello schema. La misura c'è, il trattamento no.

**Cosa serve per procedere:** una risposta alle tre domande sopra. Se la risposta è la
via 3 (nominare la persona nella PEC istituzionale, senza conservarne l'email), il
lavoro tecnico è di circa un'ora e il trattamento resta minimo.

---

---

## Riepilogo operativo

| | |
|---|---|
| Si può **costruire e usare internamente** il database? | Sì, con attribuzione |
| Si può **fare outreach via PEC agli enti**? | Probabile, ma da confermare (§3) |
| Si può **vendere il prodotto a terzi**? | ⚠️ **dipende dal ShareAlike** (§2) |
| Si possono usare **nominativi di persone**? | Non ora: non li memorizziamo (§1) |

Il blocco è sul terzo punto, e non è un dettaglio formale: riguarda se il prodotto è
vendibile nella forma che stiamo costruendo.
