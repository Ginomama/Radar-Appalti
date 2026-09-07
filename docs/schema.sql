-- =============================================================================
-- Radar Appalti Pubblici — schema database
-- =============================================================================
-- Progettato sulle colonne MISURATE il 2026-08-23, non sulla documentazione ANAC.
-- Tassi di riempimento e copertura dei join in docs/fonti-dati.md.
--
-- Target: PostgreSQL 15+ (Supabase). Per la fase di sviluppo locale su DuckDB
-- vedi le note in fondo al file.
--
-- Principi (dal CLAUDE.md di progetto):
--   - il CIG e' la chiave naturale: un rerun dello stesso mese non duplica
--   - cod_cpv va normalizzato: la stessa colonna contiene "48000000-8" e "72253000"
--   - per i valori economici si usa importo_lotto, mai la somma di
--     importo_complessivo_gara riga per riga (gonfia del 115%)
--   - i buchi nella sequenza dei dump sono un dato di prodotto: si registrano
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS radar;
SET search_path TO radar, public;


-- =============================================================================
-- 1. BANDI — dataset ANAC "cig", delta mensili YYYYMM01-cig_csv.zip
-- =============================================================================
-- Il CSV ha 61 colonne. Qui ne teniamo 31: sono escluse quelle misurate vuote o
-- prive di contenuto informativo. Motivo dell'esclusione accanto a ciascuna:
--   CIG_COLLEGAMENTO           0,0% compilata
--   stato                      sempre 'ATTIVO', nessuna informazione
--   COD_/MOTIVO_CANCELLAZIONE, DATA_CANCELLAZIONE, *_DELEGA*, *_PROG_ESTERNA,
--   COD_MODALITA_INDIZIONE_*, IPOTESI_COLLEGAMENTO, TIPO_APPALTO_RISERVATO
--                              quasi sempre vuote sul verticale IT
-- DURATA_PREVISTA e' tenuta solo per tracciabilita': NON usarla per le scadenze
-- (1,1% compilata, e mescola mesi e giorni nella stessa colonna).

CREATE TABLE IF NOT EXISTS cig (
    cig                                 text PRIMARY KEY,
    cig_accordo_quadro                  text,
    numero_gara                         text NOT NULL,      -- chiave per deduplicare i multi-lotto
    oggetto_gara                        text,
    oggetto_lotto                       text,
    oggetto_principale_contratto        text,

    -- economics: usare importo_lotto per le somme
    importo_lotto                       numeric(18,2),
    importo_complessivo_gara            numeric(18,2),      -- ripetuto su ogni lotto: NON sommare
    importo_sicurezza                   numeric(18,2),
    n_lotti_componenti                  integer,

    -- territorio
    provincia                           text,
    luogo_istat                         text,
    sezione_regionale                   text,

    -- stazione appaltante
    cf_amministrazione_appaltante       text,               -- 99,99% compilata: chiave di aggregazione, ma NON NULL la spezza
    denominazione_amministrazione_appaltante text,
    codice_ausa                         text,

    -- classificazione
    cod_cpv                             text NOT NULL,
    descrizione_cpv                     text,
    flag_prevalente                     text,
    settore                             text,

    -- tempi
    data_pubblicazione                  date,
    data_scadenza_offerta               date,

    -- procedura
    tipo_scelta_contraente              text,
    modalita_realizzazione              text,
    strumento_svolgimento               text,
    flag_urgenza                        text,

    -- esito (94,9% compilato sul verticale IT; il resto sono gare non aggiudicate)
    cod_esito                           text,
    esito                               text,
    data_comunicazione_esito            date,

    flag_pnrr_pnc                       text,               -- 100% compilata
    durata_prevista                     text,               -- ⚠️ inservibile, vedi sopra

    -- normalizzazione + tracciabilita' ingestion
    cpv_norm  text GENERATED ALWAYS AS (split_part(cod_cpv, '-', 1)) STORED,
    src_file  text,                                         -- es. '20260801-cig_csv'
    ingerito_il timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN cig.importo_complessivo_gara IS
    'Ripetuto identico su ogni lotto della stessa gara. Sommarlo riga per riga gonfia del 115%. Usare importo_lotto o deduplicare su numero_gara.';
COMMENT ON COLUMN cig.durata_prevista IS
    'NON usare per le scadenze: 1,1% compilata e unita di misura mescolate (36.0 mesi vs 1095.0 giorni). Usare avvio_contratto.data_termine_contrattuale.';
COMMENT ON COLUMN cig.cpv_norm IS
    'cod_cpv senza check digit. La sorgente contiene sia "48000000-8" sia "72253000".';

CREATE INDEX IF NOT EXISTS ix_cig_cpv_norm      ON cig (cpv_norm);
CREATE INDEX IF NOT EXISTS ix_cig_provincia     ON cig (provincia);
CREATE INDEX IF NOT EXISTS ix_cig_cf_sa         ON cig (cf_amministrazione_appaltante);
CREATE INDEX IF NOT EXISTS ix_cig_pubblicazione ON cig (data_pubblicazione);
CREATE INDEX IF NOT EXISTS ix_cig_numero_gara   ON cig (numero_gara);


-- =============================================================================
-- 2. AGGIUDICAZIONI — dataset ANAC "aggiudicazioni" (bulk, ~151 MB)
-- =============================================================================
-- Join sul CIG al 95,0%. Qui stanno ribasso e importo aggiudicato: il dataset
-- bandi NON li contiene.

CREATE TABLE IF NOT EXISTS aggiudicazioni (
    id_aggiudicazione           text PRIMARY KEY,
    cig                         text NOT NULL,
    data_aggiudicazione_definitiva date,
    data_comunicazione_esito    date,
    cod_esito                   text,
    esito                       text,
    criterio_aggiudicazione     text,

    importo_aggiudicazione      numeric(18,2),
    ribasso_aggiudicazione      numeric(10,4),
    massimo_ribasso             numeric(10,4),
    minimo_ribasso              numeric(10,4),

    -- quanto era contesa la gara
    num_imprese_offerenti       integer,
    num_imprese_invitate        integer,
    num_imprese_richiedenti     integer,
    numero_offerte_ammesse      integer,
    numero_offerte_escluse      integer,
    n_manif_interesse           integer,

    asta_elettronica            text,
    flag_subappalto             text,
    flag_proc_accelerata        text,
    prestazioni_comprese        text,

    src_file    text,
    ingerito_il timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_agg_cig  ON aggiudicazioni (cig);
CREATE INDEX IF NOT EXISTS ix_agg_data ON aggiudicazioni (data_aggiudicazione_definitiva);


-- =============================================================================
-- 3. AGGIUDICATARI — dataset ANAC "aggiudicatari" (bulk, ~151 MB)
-- =============================================================================
-- Join sul CIG al 94,8%. Piu' righe per CIG in caso di RTI: la PK e' composta.

CREATE TABLE IF NOT EXISTS aggiudicatari (
    id_aggiudicazione   text NOT NULL,
    codice_fiscale      text NOT NULL,
    cig                 text NOT NULL,
    denominazione       text,
    ruolo               text,           -- valorizzato per i raggruppamenti
    tipo_soggetto       text,

    src_file    text,
    ingerito_il timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (id_aggiudicazione, codice_fiscale)
);

CREATE INDEX IF NOT EXISTS ix_aggt_cig ON aggiudicatari (cig);
CREATE INDEX IF NOT EXISTS ix_aggt_cf  ON aggiudicatari (codice_fiscale);


-- =============================================================================
-- 4. AVVIO CONTRATTO — dataset ANAC "avvio-contratto" (~43 MB)
-- =============================================================================
-- Il motore Scadenze sta qui. data_termine_contrattuale e' compilata all'83,6%
-- ed e' una data vera, non una durata da interpretare.

CREATE TABLE IF NOT EXISTS avvio_contratto (
    id_aggiudicazione               text PRIMARY KEY,
    cig                             text NOT NULL,
    data_stipula_contratto          date,
    data_esecutivita_contratto      date,
    data_termine_contrattuale       date,       -- ⭐ la colonna che regge il motore Scadenze
    data_inizio_effettiva           date,
    data_verbale_prima_consegna     date,
    data_verbale_consegna_definitiva date,
    consegna_frazionata             text,
    consegna_sotto_riserva          text,

    src_file    text,
    ingerito_il timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_avvio_cig     ON avvio_contratto (cig);
CREATE INDEX IF NOT EXISTS ix_avvio_termine ON avvio_contratto (data_termine_contrattuale);


-- =============================================================================
-- 5. INGESTION LOG — i buchi nella sequenza sono un dato di prodotto
-- =============================================================================
-- Regola del CLAUDE.md: registrare per ogni periodo se il dump esiste. Serve a
-- sapere cosa il prodotto puo' promettere, non solo a fare debug.

CREATE TABLE IF NOT EXISTS ingestion_log (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset         text NOT NULL,          -- 'cig' | 'aggiudicazioni' | ...
    risorsa         text NOT NULL,          -- '20260801-cig_csv'
    periodo         text,                   -- '2026-08' dove applicabile
    esito           text NOT NULL,          -- 'ok' | 'assente_404' | 'errore' | 'zip_corrotto'
    http_status     integer,
    byte_scaricati  bigint,
    righe_lette     bigint,
    righe_upsert    bigint,
    last_modified   timestamptz,            -- header HTTP: freschezza reale della fonte
    iniziato_il     timestamptz NOT NULL DEFAULT now(),
    finito_il       timestamptz,
    note            text,

    UNIQUE (dataset, risorsa)
);


-- =============================================================================
-- 6. CONTATTI ENTI — dataset IndicePA "amministrazioni"
-- =============================================================================
-- Task R1. Chiave: codice fiscale. Match misurato 89,4% sugli enti, 92,3%
-- pesato sui lotti, 93,9% sulle scadenze a 12 mesi (R1.1, 2026-08-26).
--
-- nome_resp / cogn_resp / titolo_resp NON sono memorizzati: dati personali,
-- uso subordinato alla verifica di liceita' R3. La PEC dell'ente e' un
-- recapito organizzativo e si tiene.

CREATE TABLE IF NOT EXISTS ente_contatti (
    cf_ente             text PRIMARY KEY,
    cod_amm             text,
    denominazione_ipa   text,
    pec                 text,
    mail_alt            text,
    sito_istituzionale  text,
    comune              text,
    provincia           text,
    regione             text,
    cap                 text,
    indirizzo           text,
    tipologia_amm       text,
    tipologia_istat     text,
    aggiornato_il       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_contatti_provincia ON ente_contatti (provincia);
CREATE INDEX IF NOT EXISTS ix_contatti_tipologia ON ente_contatti (tipologia_amm);


-- =============================================================================
-- VISTE
-- =============================================================================

-- Una riga per GARA, non per lotto. Usare questa per qualunque conteggio
-- economico: evita il gonfiaggio del 115%.
CREATE OR REPLACE VIEW v_gara AS
SELECT
    numero_gara,
    min(cf_amministrazione_appaltante)            AS cf_ente,
    min(denominazione_amministrazione_appaltante) AS ente,
    min(provincia)                                AS provincia,
    min(data_pubblicazione)                       AS data_pubblicazione,
    count(*)                                      AS n_lotti,
    sum(importo_lotto)                            AS importo_totale,
    min(cpv_norm)                                 AS cpv_norm
FROM cig
GROUP BY numero_gara;


-- Motore INTELLIGENCE: chi ha vinto cosa, con che ribasso, contro quanti.
CREATE OR REPLACE VIEW v_aggiudicazione_completa AS
SELECT
    c.cig,
    c.numero_gara,
    c.oggetto_lotto,
    c.cpv_norm,
    c.descrizione_cpv,
    c.provincia,
    c.cf_amministrazione_appaltante,
    c.denominazione_amministrazione_appaltante AS ente,
    c.data_pubblicazione,
    c.importo_lotto                            AS importo_base,
    a.importo_aggiudicazione,
    a.ribasso_aggiudicazione,
    a.num_imprese_offerenti,
    a.criterio_aggiudicazione,
    a.data_aggiudicazione_definitiva,
    t.codice_fiscale                           AS cf_vincitore,
    t.denominazione                            AS vincitore,
    t.tipo_soggetto,
    c.flag_pnrr_pnc
FROM cig c
JOIN aggiudicazioni a ON a.cig = c.cig
LEFT JOIN aggiudicatari t ON t.id_aggiudicazione = a.id_aggiudicazione;


-- Motore SCADENZE: il cuore del prodotto. Guarda in avanti, quindi la latenza
-- della fonte non lo tocca.
--
-- UNA RIGA PER CONTRATTO, non per fornitore. Il 9% dei CIG ha piu' di un
-- aggiudicatario (RTI) e sono i grandi: la versione riga-per-fornitore gonfiava
-- il valore del 99,8% (12.078 M invece di 6.045 M). I membri del raggruppamento
-- vengono concatenati in fornitore_uscente.
CREATE OR REPLACE VIEW v_scadenze_prossime AS
SELECT
    c.cig,
    c.oggetto_lotto,
    c.cpv_norm,
    c.provincia,
    c.denominazione_amministrazione_appaltante AS ente,
    c.cf_amministrazione_appaltante,
    v.data_stipula_contratto,
    v.data_termine_contrattuale,
    (v.data_termine_contrattuale - CURRENT_DATE)            AS giorni_alla_scadenza,
    max(a.importo_aggiudicazione)              AS importo_aggiudicazione,
    string_agg(DISTINCT t.denominazione, ' + ') AS fornitore_uscente,
    count(DISTINCT t.codice_fiscale)           AS n_fornitori,
    min(t.codice_fiscale)                      AS cf_fornitore_uscente
FROM avvio_contratto v
JOIN cig c ON c.cig = v.cig
LEFT JOIN aggiudicazioni a ON a.id_aggiudicazione = v.id_aggiudicazione
LEFT JOIN aggiudicatari t ON t.id_aggiudicazione = v.id_aggiudicazione
WHERE v.data_termine_contrattuale >= CURRENT_DATE
GROUP BY c.cig, c.oggetto_lotto, c.cpv_norm, c.provincia,
         c.denominazione_amministrazione_appaltante, c.cf_amministrazione_appaltante,
         v.data_stipula_contratto, v.data_termine_contrattuale;


-- Profilo COMPETITOR: chi domina un verticale/territorio e con che aggressivita'.
CREATE OR REPLACE VIEW v_competitor AS
SELECT
    t.codice_fiscale,
    max(t.denominazione)                AS vincitore,
    count(*)                            AS gare_vinte,
    sum(a.importo_aggiudicazione)       AS valore_vinto,
    -- 88% del verticale e' affidamento diretto, dove ribasso=0 e' corretto e non
    -- mancante. Mediarlo su tutto azzera il dato: si media solo sul competitivo.
    count(*) FILTER (WHERE a.ribasso_aggiudicazione > 0) AS gare_competitive,
    round(avg(a.ribasso_aggiudicazione) FILTER (WHERE a.ribasso_aggiudicazione > 0), 2)
                                        AS ribasso_medio_competitive,
    round(avg(a.num_imprese_offerenti) FILTER (WHERE a.num_imprese_offerenti >= 1), 1)
                                        AS concorrenti_medi,
    count(DISTINCT c.cf_amministrazione_appaltante) AS enti_serviti,
    count(DISTINCT c.provincia)         AS province_presidiate,
    min(c.data_pubblicazione)           AS prima_gara,
    max(c.data_pubblicazione)           AS ultima_gara
FROM aggiudicatari t
JOIN aggiudicazioni a ON a.id_aggiudicazione = t.id_aggiudicazione
JOIN cig c            ON c.cig = a.cig
GROUP BY t.codice_fiscale;


-- Profilo ENTE: quanto spende, su cosa, quanto e' contendibile.
CREATE OR REPLACE VIEW v_ente AS
SELECT
    c.cf_amministrazione_appaltante          AS cf_ente,
    max(c.denominazione_amministrazione_appaltante) AS ente,
    max(c.provincia)                         AS provincia,
    count(*)                                 AS lotti,
    count(DISTINCT c.numero_gara)            AS gare,
    sum(c.importo_lotto)                     AS importo_bandito,
    sum(a.importo_aggiudicazione)            AS importo_aggiudicato,
    -- come in v_competitor: mediare il ribasso su tutto lo azzera, perche' negli
    -- affidamenti diretti (88% del verticale) e' 0 a ragione. Si media sul competitivo.
    count(*) FILTER (WHERE a.ribasso_aggiudicazione > 0) AS gare_competitive,
    round(avg(a.ribasso_aggiudicazione) FILTER (WHERE a.ribasso_aggiudicazione > 0), 2)
                                             AS ribasso_medio_competitive,
    round(avg(a.num_imprese_offerenti) FILTER (WHERE a.num_imprese_offerenti >= 1), 1)
                                             AS concorrenti_medi,
    count(*) FILTER (WHERE c.flag_pnrr_pnc = '1') AS lotti_pnrr
FROM cig c
LEFT JOIN aggiudicazioni a ON a.cig = c.cig
GROUP BY c.cf_amministrazione_appaltante;


-- =============================================================================
-- IDEMPOTENZA — pattern di UPSERT
-- =============================================================================
-- Ogni delta mensile ANAC contiene sia record nuovi sia record vecchi aggiornati
-- (es. esito comunicato mesi dopo). Il rerun dello stesso file NON deve
-- duplicare, e un record aggiornato DEVE sovrascrivere. Da qui l'UPSERT:
--
--   INSERT INTO radar.cig (cig, numero_gara, ..., src_file)
--   VALUES (...)
--   ON CONFLICT (cig) DO UPDATE SET
--       esito                    = EXCLUDED.esito,
--       data_comunicazione_esito = EXCLUDED.data_comunicazione_esito,
--       importo_lotto            = EXCLUDED.importo_lotto,
--       src_file                 = EXCLUDED.src_file,
--       ingerito_il              = now();
--
-- Per aggiudicazioni / avvio_contratto: ON CONFLICT (id_aggiudicazione).
-- Per aggiudicatari:                    ON CONFLICT (id_aggiudicazione, codice_fiscale).
--
-- Caricare a lotti (batch) di qualche migliaio di righe, non riga per riga.


-- =============================================================================
-- FILTRO VERTICALE
-- =============================================================================
-- Il verticale e' un parametro, non una costante nel codice: e' il punto del
-- prodotto. Cambiare verticale = cambiare questi prefissi.
--
--   WHERE cpv_norm LIKE '72%' OR cpv_norm LIKE '48%'      -- servizi IT
--
-- Volumi attesi (misurati): ~85.000 gare/anno nazionali, ~10.200 Veneto+FVG.


-- =============================================================================
-- NOTE PER DUCKDB (fase di sviluppo locale, costo zero)
-- =============================================================================
-- Questo DDL gira su DuckDB con tre adattamenti:
--   1. CREATE SCHEMA / SET search_path  -> supportati, ma con un file singolo
--      si puo' semplicemente omettere lo schema.
--   2. timestamptz -> TIMESTAMP ; bigint GENERATED ALWAYS AS IDENTITY ->
--      usare una SEQUENCE, oppure BIGINT DEFAULT nextval('seq_ingestion').
--   3. (data_termine_contrattuale - CURRENT_DATE) ritorna un INTERVAL: avvolgere
--      in date_diff('day', CURRENT_DATE, data_termine_contrattuale).
--
-- DuckDB legge i CSV ANAC direttamente, utile per il backfill:
--   CREATE TABLE staging_cig AS
--   SELECT * FROM read_csv_auto('recon_out/zip/*.csv', delim=';', header=true);
--
-- Migrazione a Supabase quando n8n dovra' leggere: stesso DDL, piu' le RLS
-- policy. Non creare un progetto Supabase nuovo ($10/mese sul piano Pro):
-- usare questo schema 'radar' dentro un progetto esistente.
