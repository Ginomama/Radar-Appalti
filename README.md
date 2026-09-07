# Radar Appalti

Un motore che legge i dati aperti degli appalti pubblici italiani e risponde a una domanda
sola: **quali contratti IT della PA stanno per scadere, e quali di quelli valgono davvero una
telefonata.**

Non è un aggregatore di bandi. I bandi sono pubblici e chiunque li vede: quando esce il bando
la partita è già decisa. Questo guarda il passo prima — i contratti *in corso* e la loro data
di scadenza — e apre una finestra di qualche mese in cui un fornitore alternativo può ancora
farsi conoscere.

Costruito su un verticale (CPV 72 e 48: servizi informatici e software), ma il verticale è un
parametro: cambiando i prefissi CPV la stessa macchina lavora su qualsiasi settore.

## Cosa dicono i dati

Numeri misurati sul dataset reale, non stime.

| | |
|---|---:|
| CIG del verticale IT in database | 283.929 |
| Contratti con scadenza futura | 13.567 |
| In scadenza entro 12 mesi | 6.677 |
| **Enti distinti da contattare entro 12 mesi** | **1.732** |
| Quota IT sulle scadenze nazionali | 5,5% |

L'ultima riga vale la pena: la quota IT sulle *scadenze* coincide con la quota IT sui *bandi*,
cioè i contratti informatici non durano né più né meno degli altri.

## La domanda difficile: serve a qualcosa?

Segnalare una scadenza è facile. La domanda vera è quante di quelle scadenze diventino
davvero un'occasione. `esito.py` la misura: per ogni contratto scaduto cerca il CIG con cui
lo stesso ente ha ricomprato la stessa cosa, e guarda chi ha vinto e con che procedura.

Su **30.301 contratti scaduti** fra il 2023 e il 2026:

| | | |
|---|---:|---:|
| nessun seguito trovato | 24.888 | 82,1% |
| rinnovato allo stesso fornitore | 3.429 | 11,3% |
| **vinto da un altro fornitore** | **1.205** | **4,0%** |
| aggiudicatario non ancora noto | 779 | 2,6% |

**Il 4,7% delle scadenze finisce in una porta aperta** — cambio di fornitore, o procedura
competitiva. Tre cose che questo numero cambia:

1. **I lead vanno ordinati per importo, non per data.** Sotto 40 k€ la porta si apre nel 3,2%
   dei casi e lì sta metà del volume; nella fascia 140 k€–1 M€ si arriva al 7,5%. Il grosso
   del lavoro sta dove rende meno.
2. **Il gioco non è vincere gare.** Il 94,1% dei contratti che vengono ricomprati passa per
   affidamento diretto, non per gara. Aspettare il bando significa arrivare tardi per
   definizione.
3. **Il tasso è piatto al 13–19% su 45 mesi**, quindi l'82% senza seguito non è un ritardo di
   pubblicazione: è strutturale. I limiti veri della misura sono dichiarati in
   [docs/roadmap.md](docs/roadmap.md), R17.

## Come è fatto

```
ANAC open data  ──┐
                  ├──> ingest.py ──> SQLite (grezzo, ~300 MB) ──> viste derivate
IndicePA        ──┘                                                     │
                                                                        ▼
                                          push_supabase.py ──> Supabase (~37 MB)
                                                                        │
                            ┌───────────────────────────────────────────┤
                            ▼                     ▼                     ▼
                     console_live.py         notifica.py            n8n workflows
                     (console locale)      (Telegram/email)        (automazioni)
```

Il grezzo resta in locale di proposito: su Supabase Free il database va in sola lettura oltre
i 500 MB, e i record ANAC ci arriverebbero in pochi mesi. In cloud vanno solo le viste.

### I file che contano

| | |
|---|---|
| `ingestion/ingest.py` | scarica e ingerisce ANAC, con sentinella sul drift dello schema |
| `ingestion/esito.py` | che fine hanno fatto le scadenze passate — il motore di R17 |
| `ingestion/categorie.py` | classificazione funzionale dei contratti dal CPV e dall'oggetto |
| `ingestion/genera_pec.py` | genera le lettere PEC, con anti-duplicato e solleciti |
| `ingestion/pec_smtp.py` | invio PEC, con quattro guardie prima di spedire |
| `ingestion/pec_imap.py` | legge le ricevute di consegna (sola lettura, non tocca la casella) |
| `ingestion/job.py` | i job schedulati, due ritmi diversi |
| `ingestion/backup.py` | backup delle tabelle che nessuno può rigenerare |
| `ingestion/console_live.py` | console operativa, in ascolto solo su 127.0.0.1 |

## Un problema che vale la pena raccontare

Trovare il "seguito" di un contratto scaduto vuol dire capire se due oggetti d'appalto
parlano della stessa cosa. Il primo tentativo — lista di stopword — non converge mai: dopo
tre giri restavano fuori «procedura negoziata senza previa pubblicazione», il preambolo PNRR,
gli articoli del Codice dei contratti.

La soluzione è pesare ogni parola per quanto è rara nei 283.929 oggetti: `servizio` vale 1,4,
`symantec` vale 9,1. Il boilerplate si annulla da solo e non richiede manutenzione.

Restava un buco: parole rare in Italia ma banali per quell'ente. `giannina gaslini` è
rarissimo nel dataset e compare in ogni bando dell'ospedale Gaslini, quindi due contratti
scollegati si somigliavano per il nome del committente. Si scartano le parole che compaiono
in oltre il 30% degli oggetti dello stesso ente.

E poi il caso opposto: «CANONE ANNUALE DARKTRACE» è specifico quanto basta ma ha una parola
rara sola, e con una soglia fissa era inconfrontabile per costruzione — il 26,9% dei
contratti non veniva nemmeno cercato. La soglia è diventata adattiva.

Dettagli e falsi positivi residui: [docs/roadmap.md](docs/roadmap.md), R17.

## Provarlo

Serve solo Python 3.11+. L'ingestione è a libreria standard; `psycopg[binary]` serve solo per
la parte Supabase.

```bash
python ingestion/ingest.py --init
python ingestion/ingest.py --storico 2021-2025 --purga
python ingestion/ingest.py --bulk
python ingestion/categorie.py
python ingestion/console_live.py
```

Il primo carico scarica diversi GB da ANAC e richiede ore. `--cpv 45,50` (o qualsiasi altro
prefisso) cambia verticale.

> **Il WAF di ANAC risponde 403 agli IP dei cloud provider.** L'ingestione deve partire da una
> linea residenziale: non gira su n8n Cloud, GitHub Actions o un VPS. È il motivo per cui i
> job stanno nell'Utilità di pianificazione di Windows e non in cloud.

## Dati e licenze

Il codice è MIT (vedi [LICENSE](LICENSE)). **I dati no**: ANAC pubblica in CC BY-SA 4.0, che è
ShareAlike. Questo repository contiene solo codice — nessun dataset ANAC, nessun estratto,
nessuna lista di enti. Vedi [DATI.md](DATI.md) prima di ridistribuire qualsiasi cosa che il
codice produce.

`docs/console.html` contiene **dati inventati**: enti, PEC, CIG e fornitori non esistono e
servono solo a far vedere l'interfaccia.

## Stato

Funzionante e in uso. La roadmap in [docs/roadmap.md](docs/roadmap.md) tiene traccia di cosa
è fatto e cosa manca, con il motivo di ogni scelta. Le considerazioni su liceità e GDPR del
contatto agli enti stanno in [docs/liceita.md](docs/liceita.md).

---

Leonardo Foschi — [FlowLine](https://flowline.it)
