# Setup Supabase — livello di servizio del Radar

> Da eseguire **una volta sola**, a mano, nel dashboard Supabase.
> Verificato sulla documentazione ufficiale il 2026-08-24.

## Il principio: cosa va su Supabase e cosa no

**Non caricare i dati grezzi.** Restano in locale, in `ingestion/radar.db`.

| Cosa | Dove | Peso |
|---|---|---|
| `cig`, `aggiudicazioni`, `aggiudicatari`, `avvio_contratto` — 686.000 righe | locale | 287 MB |
| `scadenze`, `competitor`, `ente` — le viste derivate | **Supabase** | ~30 MB |

Due motivi, entrambi vincoli reali del piano Free:

1. **Oltre 500 MB di database il progetto va in sola lettura.** I dati grezzi ci
   arriverebbero in pochi mesi; il livello di servizio no, mai.
2. **I progetti Free vengono sospesi dopo 7 giorni di bassa attività.** Bastano poche
   query al giorno per evitarlo — e il workflow n8n che notifica le scadenze gira
   quotidianamente, quindi lo tiene sveglio da solo.

Il flusso resta: ANAC → runner locale (IP residenziale, per il WAF) → `radar.db` →
push delle viste → Supabase → n8n Cloud.

---

## Passo 1 — Creare il progetto

Nel dashboard Supabase:

1. **Organizzazione**: creane una nuova sul piano **Free**. Si possono avere due
   organizzazioni Free con un progetto attivo ciascuna, anche accanto a una Pro —
   quindi non serve un account separato.
2. **New project**:
   - *Name*: `radar-appalti`
   - *Database Password*: generala e **salvala subito** nel password manager. Serve
     per la connessione da n8n e dallo script di push, e non è più recuperabile in chiaro.
   - *Region*: **Central EU (Frankfurt)** — dati pubblici italiani, latenza minima.
3. Attendi il provisioning (~2 minuti).

---

## Passo 2 — Creare lo schema

Dashboard → **SQL Editor** → *New query* → incolla tutto e premi **Run**.

```sql
-- =====================================================================
-- Radar Appalti — livello di servizio
-- Le tabelle sono POPOLATE DA FUORI (push dal runner locale).
-- Qui non si calcola niente sui dati grezzi: quelli non arrivano mai.
-- =====================================================================

create schema if not exists radar;

-- ---------------------------------------------------------------------
-- 1. SCADENZE — il motore che regge il prodotto
-- ---------------------------------------------------------------------
-- Una riga per contratto, MAI una per fornitore: il 9% dei CIG e' aggiudicato
-- a un RTI e la versione riga-per-fornitore gonfiava il valore del 99,8%.
-- I membri del raggruppamento stanno concatenati in fornitore_uscente.
create table if not exists radar.scadenze (
    cig                        text primary key,
    oggetto_lotto              text,
    cpv_norm                   text,
    provincia                  text,
    ente                       text,
    cf_ente                    text,
    data_stipula_contratto     date,
    data_termine_contrattuale  date not null,
    importo_aggiudicazione     numeric(18,2),
    fornitore_uscente          text,
    n_fornitori                integer,
    cf_fornitore_uscente       text,
    aggiornato_il              timestamptz not null default now()
);

create index if not exists ix_scad_termine   on radar.scadenze (data_termine_contrattuale);
create index if not exists ix_scad_provincia on radar.scadenze (provincia);
create index if not exists ix_scad_cpv       on radar.scadenze (cpv_norm);
create index if not exists ix_scad_cf_ente   on radar.scadenze (cf_ente);

-- ---------------------------------------------------------------------
-- 2. COMPETITOR — chi presidia quale ente
-- ---------------------------------------------------------------------
create table if not exists radar.competitor (
    codice_fiscale             text primary key,
    vincitore                  text,
    gare_vinte                 integer,
    valore_vinto               numeric(18,2),
    gare_competitive           integer,
    ribasso_medio_competitive  numeric(10,2),
    concorrenti_medi           numeric(10,1),
    enti_serviti               integer,
    province_presidiate        integer,
    prima_gara                 date,
    ultima_gara                date,
    aggiornato_il              timestamptz not null default now()
);

create index if not exists ix_comp_valore on radar.competitor (valore_vinto desc);

-- ---------------------------------------------------------------------
-- 3. ENTE — quanto spende, su cosa, quanto e' contendibile
-- ---------------------------------------------------------------------
create table if not exists radar.ente (
    cf_ente                    text primary key,
    ente                       text,
    provincia                  text,
    lotti                      integer,
    gare                       integer,
    importo_bandito            numeric(18,2),
    importo_aggiudicato        numeric(18,2),
    gare_competitive           integer,
    ribasso_medio_competitive  numeric(10,2),
    concorrenti_medi           numeric(10,1),
    lotti_pnrr                 integer,
    aggiornato_il              timestamptz not null default now()
);

create index if not exists ix_ente_provincia on radar.ente (provincia);

-- ---------------------------------------------------------------------
-- 4. SYNC LOG — quando e' stato aggiornato, e se e' andato storto
-- ---------------------------------------------------------------------
create table if not exists radar.sync_log (
    id            bigint generated always as identity primary key,
    tabella       text not null,
    righe         integer,
    esito         text not null,          -- ok | errore
    iniziato_il   timestamptz not null default now(),
    finito_il     timestamptz,
    note          text
);

-- ---------------------------------------------------------------------
-- 5. VISTE — i giorni alla scadenza si calcolano ADESSO, non si salvano
-- ---------------------------------------------------------------------
-- Salvare giorni_alla_scadenza sarebbe un errore: il giorno dopo il push
-- il numero e' gia' sbagliato. Si calcola a ogni query.
create or replace view radar.v_scadenze as
select
    s.*,
    (s.data_termine_contrattuale - current_date) as giorni_alla_scadenza
from radar.scadenze s
where s.data_termine_contrattuale >= current_date;

-- Quella che interroga n8n ogni giorno.
create or replace view radar.v_scadenze_90gg as
select *
from radar.v_scadenze
where giorni_alla_scadenza <= 90
order by giorni_alla_scadenza;

-- ---------------------------------------------------------------------
-- 6. SICUREZZA
-- ---------------------------------------------------------------------
-- Lo schema "radar" non e' esposto via PostgREST (di default lo e' solo
-- "public"), quindi le API key anon/service non lo raggiungono. In piu'
-- attiviamo RLS senza policy: nessun accesso per anon/authenticated.
-- Il ruolo postgres, che possiede le tabelle, non e' soggetto a RLS: e'
-- quello che useranno n8n e lo script di push.
alter table radar.scadenze   enable row level security;
alter table radar.competitor enable row level security;
alter table radar.ente       enable row level security;
alter table radar.sync_log   enable row level security;

-- verifica
select table_name from information_schema.tables where table_schema = 'radar';
```

Deve restituire 4 righe: `scadenze`, `competitor`, `ente`, `sync_log`.

---

## Passo 3 — Prendere la stringa di connessione GIUSTA

⚠️ **Qui si sbaglia facilmente.** Nel dashboard, pulsante **Connect** in alto.

| Modalità | Host | Rete | Usare? |
|---|---|---|---|
| Direct connection | `db.<ref>.supabase.co:5432` | **solo IPv6** sul piano Free | ❌ n8n Cloud non si connette |
| **Session pooler** | `aws-<region>.pooler.supabase.com:5432` | **IPv4** | ✅ **questa** |
| Transaction pooler | `...pooler.supabase.com:6543` | IPv4 | ❌ non supporta i prepared statement |

Copia la **Session pooler**, che ha questa forma:

```
postgresql://postgres.<PROJECT-REF>:<PASSWORD>@aws-<REGION>.pooler.supabase.com:5432/postgres
```

L'IPv4 diretto esiste ma è un add-on a pagamento: sul Free serve il pooler.

---

## Passo 4 — Credenziale in n8n

n8n → **Credentials** → *New* → **Postgres**:

| Campo | Valore |
|---|---|
| Host | `aws-<REGION>.pooler.supabase.com` |
| Database | `postgres` |
| User | `postgres.<PROJECT-REF>` ← con il punto e il ref, non solo `postgres` |
| Password | quella salvata al Passo 1 |
| Port | `5432` |
| SSL | **abilitato** |

Chiamala `Supabase Radar Appalti`.

⚠️ Mai incollare la password dentro un nodo o dentro un JSON di workflow: solo qui,
nel Credentials Manager. È la regola del `CLAUDE.md` globale.

Query di prova in un nodo Postgres:

```sql
select count(*) from radar.v_scadenze_90gg;
```

---

## Passo 5 — Cosa serve allo script di push (lato locale)

Lo script `ingestion/push_supabase.py` leggerà la connessione da variabile
d'ambiente, **mai da file versionato**:

```bash
setx RADAR_SUPABASE_DSN "postgresql://postgres.<REF>:<PWD>@aws-<REGION>.pooler.supabase.com:5432/postgres"
```

Richiede `psycopg` (`pip install psycopg[binary]`) — è l'unica dipendenza esterna
del progetto, e sta solo nel push, non nell'ingestion.

---

## Passo 6 — Arricchimento contatti (task R1, aggiunto il 2026-08-26)

Da eseguire nel **SQL Editor** dopo il primo setup. Aggiunge i recapiti IndicePA
alle scadenze: senza contatto una scadenza è una riga, non un lead.

⚠️ **Trappola Postgres, incontrata sul campo il 2026-08-26.** `SELECT *` dentro una
vista viene **espanso e congelato alla creazione**: aggiungere colonne alla tabella non
le fa comparire in una vista già esistente. E `CREATE OR REPLACE VIEW` non basta a
correggerla, perché le colonne nuove finirebbero *prima* di `giorni_alla_scadenza` e
Postgres non permette di spostare colonne esistenti. Le viste vanno **ricreate**, ed è
il motivo per cui qui sotto le colonne sono elencate una per una invece di usare la
stella.

```sql
alter table radar.scadenze
    add column if not exists pec                text,
    add column if not exists mail_alt           text,
    add column if not exists denominazione_ipa  text,
    add column if not exists tipologia_amm      text,
    add column if not exists sito_istituzionale text;

drop view if exists radar.v_lead_90gg;
drop view if exists radar.v_scadenze_90gg;
drop view if exists radar.v_scadenze;

create view radar.v_scadenze as
select
    cig, oggetto_lotto, cpv_norm, provincia, ente, cf_ente,
    data_stipula_contratto, data_termine_contrattuale,
    importo_aggiudicazione, fornitore_uscente, n_fornitori,
    cf_fornitore_uscente, pec, mail_alt, denominazione_ipa,
    tipologia_amm, sito_istituzionale, aggiornato_il,
    (data_termine_contrattuale - current_date) as giorni_alla_scadenza
from radar.scadenze
where data_termine_contrattuale >= current_date;

create view radar.v_scadenze_90gg as
select * from radar.v_scadenze
where giorni_alla_scadenza <= 90
order by giorni_alla_scadenza;

-- la vista che interroga n8n: una riga per lead, senza join da scrivere
create view radar.v_lead_90gg as
select
    cig, giorni_alla_scadenza, data_termine_contrattuale,
    coalesce(denominazione_ipa, ente) as ente,
    provincia, tipologia_amm, oggetto_lotto, cpv_norm,
    importo_aggiudicazione, fornitore_uscente, n_fornitori,
    pec, mail_alt, sito_istituzionale
from radar.v_scadenze
where giorni_alla_scadenza <= 90 and pec is not null
order by importo_aggiudicazione desc nulls last;

select count(*) as scadenze from radar.v_scadenze;
```

Lo script è **rieseguibile**: `add column if not exists` e `drop view if exists` sono
idempotenti. Il conteggio finale sarà 0 finché non gira `push_supabase.py`.

I contatti sono **denormalizzati dentro `radar.scadenze`** invece che in una tabella
separata: n8n legge una riga sola e ha il lead completo, senza join da scrivere.

⚠️ **Non vengono caricati nomi di persone.** IndicePA espone anche `nome_resp`,
`cogn_resp` e `titolo_resp`, ma sono dati personali e il loro uso per comunicazioni
commerciali richiede una base giuridica: è il task R3 della roadmap, da chiudere prima.
La PEC dell'ente è invece un recapito organizzativo.

### Copertura misurata (R1.1)

| | |
|---|---|
| Enti del verticale agganciati a IndicePA | **89,4%** (18.711 su 20.928) |
| Match pesato sui lotti | 92,3% |
| Enti agganciati con PEC | 100% |
| **Scadenze a 12 mesi con PEC ricavabile** | **93,9%** (5.007 su 5.331) |

Il 10,6% non agganciato non è rumore: sono **società partecipate e comparto difesa**
(Trenitalia, Terna, CDP, Ferservizi, FS Technology, Enel Global Services, Stato Maggiore
Difesa). IndicePA elenca amministrazioni pubbliche, non società per azioni a controllo
pubblico. Gap spiegabile, e comunque fuori dal mercato addressabile.

---

## Promemoria sui limiti Free

| Limite | Valore | Ci tocca? |
|---|---|---|
| Database in sola lettura | oltre 500 MB | No: il livello di servizio sta in ~30 MB |
| Sospensione per inattività | 7 giorni | No: il workflow giornaliero lo tiene sveglio |
| Ripristino dopo sospensione | entro 90 giorni | Da sapere se il progetto resta fermo a lungo |
| Backup scaricabili | non disponibili sul Free | Irrilevante: la fonte di verità è `radar.db` in locale, ricostruibile |

L'ultima riga è il punto: su Supabase ci sta **solo dato derivato**. Se il progetto
si perde, si rigenera con un push. Nessun dato originale vive lì.
