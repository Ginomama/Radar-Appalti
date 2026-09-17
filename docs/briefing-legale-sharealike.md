# Briefing per il professionista — ShareAlike ANAC (task A1)

> Un foglio solo, pensato per essere letto in 5 minuti da un avvocato che non
> conosce il progetto. Il dettaglio completo è in `liceita.md` §2 e §6; questo
> foglio è l'estratto di ciò che serve per rispondere.

## Il progetto in due righe

Radar Appalti è un motore interno di FlowLine: incrocia open data di
contratti pubblici (ANAC) e domicili digitali degli enti (IndicePA) per
individuare enti che stanno per scadere un contratto in una categoria che
trattiamo, e li contatta via PEC con un'offerta di collaborazione. Oggi è
**uso interno**: FlowLine contatta enti per conto proprio, non rivende il
dato né l'accesso a terzi.

## Il fatto da valutare

ANAC rilascia i propri open data in licenza **CC BY-SA 4.0** (Creative
Commons, Attribuzione + Condividi allo stesso modo). La clausola
*ShareAlike* impone che un'opera derivata dai dati ANAC sia distribuita con
la stessa licenza — cioè, in sostanza, resa a sua volta liberamente
riutilizzabile.

## La domanda

**Se in futuro FlowLine volesse vendere l'accesso a questo motore (SaaS, o
servizio in abbonamento) a clienti terzi — non solo usarlo internamente per
il proprio outreach — la clausola ShareAlike si applica al prodotto
venduto?**

Tre letture possibili, in ordine di rischio crescente per chi vuole vendere:

1. **Semplice riproduzione dei dati ANAC** → la ShareAlike si applica
   sicuramente: il derivato andrebbe rilasciato con la stessa licenza aperta,
   incompatibile con un modello a pagamento chiuso.
2. **Raccolta (collection)** che accosta ANAC con altre fonti (IndicePA, dati
   interni) senza fondersi in un unico dataset ridistribuito → la licenza
   potrebbe restare confinata alla sola parte ANAC, non all'insieme.
3. **Prodotto che vende accesso ed elaborazione** (analisi, matching,
   automazione), non il dato grezzo in sé → posizione difendibile ma da
   argomentare, non scontata.

Rileva anche la disciplina italiana sul riutilizzo dei dati pubblici
(d.lgs. 36/2006 e successivi), che si applica insieme alla licenza
Creative Commons.

## Cosa serve dal professionista

Non un parere generale sul riuso di open data: la risposta specifica a
**quale delle tre letture si applica al caso concreto** (motore che elabora
dati ANAC + IndicePA e vende accesso/automazione a terzi), e se esiste una
struttura contrattuale o architetturale che permetterebbe di vendere il
servizio senza dover rilasciare a propria volta il dataset derivato in
ShareAlike.

## Perché non è urgente oggi, ma blocca il futuro

**Oggi il progetto resta uso interno** (FlowLine contatta enti per conto
proprio): non c'è redistribuzione del dataset, quindi la questione non si
pone finché resta così — decisione già presa e documentata (`liceita.md`,
2026-08-26). **Va risolta prima** di firmare il primo contratto che vende
l'accesso al motore a un cliente esterno, non prima.
