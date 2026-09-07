# Radar Appalti Pubblici — Progetto Interno FlowLine

Sotto-progetto del repo n8n FlowLine. Motore di intelligence sugli appalti pubblici
italiani, parametrizzato per CPV + territorio.

**Stato: gate di Fase 0 superato il 2026-08-23.** I numeri sono misurati, non stimati.

## Per chi deve solo usarlo

**[docs/guida.md](docs/guida.md)** — dieci minuti, nessun gergo: accendi la console, leggi
la barra grigia, fai quello che dice. Il resto di questo file serve a chi ci mette le mani.

## Come usare con Claude Code

1. Apri Claude Code nella root del repo → `/model opusplan`
2. Leggi il `CLAUDE.md` di questa cartella (contesto e vincoli) — eredita le regole d'oro dal `CLAUDE.md` globale
3. Leggi `docs/fonti-dati.md` — contiene i tassi di riempimento e le trappole già verificate
4. Costruisci l'ingestion su `docs/schema.sql`

> Claude Code gira in locale, quindi ha l'IP residenziale e **non** incontra il WAF ANAC.
> In produzione invece l'ingestion deve girare su un runner non-cloud: vedi `CLAUDE.md`.

## Cosa ha detto la ricognizione

`ingestion/anac_recon.py` — solo libreria standard, nessuna dipendenza. Rilanciabile:

```bash
cd ingestion
python anac_recon.py --mesi 12 --cpv 72,48               # verticale IT
python anac_recon.py --mesi 12 --cpv 45,50 --province VE,TV,PN,UD
```

Risultati sul verticale IT (CPV 72xxx/48xxx), misurati su 620.528 righe CIG:

| Domanda | Risposta |
|---|---|
| Il volume regge? | ✅ **~85.000 gare/anno** nazionali, ~10.200 su Veneto+FVG |
| Il mercato è concentrato? | ❌ **no**: l'ente più grande pesa lo 0,50%, i top 100 il 15,9% |
| Si può sapere **chi vince**? | ✅ **sì**, join al 95% su `aggiudicazioni`/`aggiudicatari` |
| ...e **con che ribasso**? | ⚠️ **quasi mai**: utilizzabile all'1,4% — l'88% sono affidamenti diretti, senza gara né ribasso |
| Si possono prevedere le scadenze contratti? | ✅ **sì**, via `avvio-contratto` (83,6% compilato) |
| Si può fare allerta tempestiva su ANAC? | ❌ **no**: il 96,6% delle gare è già scaduto all'arrivo del file |
| Quanto mercato è davvero contendibile? | ⚠️ **~350 procedure su 36.500** — il resto si vince essendo già noti all'ente |
| Quanti lead produce oggi il motore scadenze? | ✅ **5.334 contratti in scadenza a 12 mesi** per € 6,0 mld; 964 entro 90 giorni |

Dettaglio completo, con metodo e trappole → `docs/fonti-dati.md`

## Le tre trappole che fanno perdere tempo

Tutte verificate, tutte documentate in `docs/fonti-dati.md`:

1. **Scoprire i dataset via CKAN API**, non indovinando gli URL. È indovinando che si conclude — a torto — che ANAC abbia 8 mesi di ritardo: i delta mensili freschi esistono, sono nel dataset `cig`. Vale anche per aggiudicazioni, aggiudicatari e avvio-contratto, il cui file base è fermo a gennaio 2026.
2. **`HEAD` ritorna risposte fasulle.** Usare `GET` con header `Range`.
3. **L'errore TLS non è di ANAC: è l'antivirus.** Avast fa ispezione TLS e la sua CA ha Basic Constraints non `critical`, che Python 3.13 rifiuta. Fallisce anche `pypi.org`. Su un runner senza AV intercettante il problema non esiste — non portare il workaround in produzione.

## Struttura

```
radar-appalti/
├── CLAUDE.md                  # contesto, vincoli, regole (eredita globali)
├── README.md                  # questo file
├── docs/
│   ├── fonti-dati.md           # ricognizione fonti: numeri misurati, con URL e metodo
│   ├── schema.sql              # schema database — 4 tabelle + log + viste
│   └── prds/                   # ARD generati da /prd
├── ingestion/
│   ├── anac_recon.py           # Fase 0 — misura il segnale
│   ├── anac_http.py            # accesso ANAC: TLS, User-Agent, no-HEAD, CKAN
│   ├── ingest.py               # Fase 3 — ingestion incrementale e idempotente
│   ├── schema_sqlite.sql       # schema locale (rispecchia docs/schema.sql)
│   ├── analisi_*.py            # controlli su fill-rate e importi
│   ├── radar.db                # database SQLite (git-ignored)
│   └── recon_out/              # cache ZIP (git-ignored)
└── workflows/                  # n8n JSON — solo dopo che il database è popolato
```

## Ordine di lavoro

| Fase | Cosa | Stato |
|---|---|---|
| 0 | Ricognizione volumi + schema reale | ✅ fatto — gate superato |
| 1 | Scelta verticale pilota sui numeri di Fase 0 | ✅ IT (CPV 72/48) confermato |
| 2 | Schema database (CIG come chiave naturale) | ✅ `docs/schema.sql` |
| 3 | Ingestion incrementale + idempotente | ✅ `ingestion/ingest.py`, idempotenza verificata |
| 3b | Bulk come base + delta mensili | ✅ copertura recente da 0% a 81–91% |
| 3c | Backfill storico 2021-2025 | ✅ 60/60 file, 236.891 CIG, storico dal 2008 |
| 3d | Job mensile automatico | ⬅️ **prossimo** |
| 4 | Motore scadenze contratti (il prodotto) | 🔨 vista `v_scadenze_prossime` funzionante |
| 5 | Motore intelligence: chi presidia quale ente | 🔨 vista `v_competitor` funzionante |
| 6 | Motore allerta: TED sopra soglia | ✅ API verificata, copertura quantificata |
| 7 | Viste — dashboard o agente | ⏳ |

## Dove gira il database

**In fase 3 e 4: SQLite in locale.** Costo zero, nella stdlib, e lo schema si
riscrive due o tre volte prima di stabilizzarsi — meglio farlo dove iterare non costa. In piu la sintassi UPSERT e identica a Postgres: la logica di idempotenza migra su Supabase senza riscritture.

**Quando n8n dovrà leggere i dati**: uno schema `radar` dentro un progetto Supabase
**esistente**. ⚠️ L'org Flowline è su piano **Pro**: un progetto nuovo costa **$10/mese
ricorrenti**, non è free tier. Lo schema dedicato in un progetto esistente ha costo marginale
€0 e gli 8 GB inclusi bastano (il verticale IT sta in ~200-250 MB per 3 anni).

## Promemoria

La parte difficile non è la query: è **procurarsi i dati e tenerli freschi**. WAF, dump in
ritardo, schemi che cambiano, portali che vietano il crawling. Proprio per questo, se ci riesci,
è un vantaggio difendibile — chiunque sa leggere un portale aperto, quasi nessuno ha una copia
pulita e incrementale.

Corollario: non costruire viste prima che la tabella sia popolata e verificata. Una dashboard
su dati sbagliati è peggio di nessuna dashboard.

## Una precisazione sul modello di business

Il rationale originale — "il radar produce lead per FlowLine stessa, validazione a costo zero" —
**non regge**: il mercato è troppo polverizzato per essere una lista chiamabile. Ma la stessa
frammentazione è ciò che rende il radar vendibile ai vendor IT che alla PA ci vendono davvero.
Il prodotto tiene; salta la scorciatoia dell'auto-validazione. Dettaglio in `CLAUDE.md`.
