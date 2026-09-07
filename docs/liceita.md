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
