-- =============================================================================
-- Radar Appalti — schema SQLite (fase di sviluppo locale)
-- =============================================================================
-- Rispecchia docs/schema.sql, che resta la fonte di verita' per Postgres/Supabase.
-- Le colonne sono le STESSE: scripts/check_schema_drift.py verifica che non divergano.
--
-- Differenze rispetto alla versione Postgres, tutte meccaniche:
--   numeric(18,2)                    -> REAL
--   timestamptz DEFAULT now()        -> TEXT DEFAULT CURRENT_TIMESTAMP
--   bigint GENERATED ... IDENTITY    -> INTEGER PRIMARY KEY AUTOINCREMENT
--   split_part(cod_cpv,'-',1)        -> substr(...) con instr(), SQLite non ha split_part
--   (data - CURRENT_DATE)            -> julianday(data) - julianday('now')
-- =============================================================================

PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;

-- ---------------------------------------------------------------- 1. BANDI
CREATE TABLE IF NOT EXISTS cig (
    cig                                 TEXT PRIMARY KEY,
    cig_accordo_quadro                  TEXT,
    numero_gara                         TEXT NOT NULL,
    oggetto_gara                        TEXT,
    oggetto_lotto                       TEXT,
    oggetto_principale_contratto        TEXT,

    importo_lotto                       REAL,
    importo_complessivo_gara            REAL,   -- ripetuto su ogni lotto: NON sommare
    importo_sicurezza                   REAL,
    n_lotti_componenti                  INTEGER,

    provincia                           TEXT,
    luogo_istat                         TEXT,
    sezione_regionale                   TEXT,

    cf_amministrazione_appaltante       TEXT,   -- 99,99%: 1 riga su 14.298 e vuota nel delta 2026-08
    denominazione_amministrazione_appaltante TEXT,
    codice_ausa                         TEXT,

    cod_cpv                             TEXT NOT NULL,
    descrizione_cpv                     TEXT,
    flag_prevalente                     TEXT,
    settore                             TEXT,

    data_pubblicazione                  TEXT,   -- ISO YYYY-MM-DD
    data_scadenza_offerta               TEXT,

    tipo_scelta_contraente              TEXT,
    modalita_realizzazione              TEXT,
    strumento_svolgimento               TEXT,
    flag_urgenza                        TEXT,

    cod_esito                           TEXT,
    esito                               TEXT,
    data_comunicazione_esito            TEXT,

    flag_pnrr_pnc                       TEXT,
    durata_prevista                     TEXT,   -- inservibile: 1,1% e unita' mescolate

    -- cod_cpv senza check digit: la sorgente contiene "48000000-8" e "72253000"
    cpv_norm TEXT GENERATED ALWAYS AS (
        substr(cod_cpv, 1, instr(cod_cpv || '-', '-') - 1)
    ) STORED,
    src_file    TEXT,
    ingerito_il TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_cig_cpv_norm      ON cig (cpv_norm);
CREATE INDEX IF NOT EXISTS ix_cig_provincia     ON cig (provincia);
CREATE INDEX IF NOT EXISTS ix_cig_cf_sa         ON cig (cf_amministrazione_appaltante);
CREATE INDEX IF NOT EXISTS ix_cig_pubblicazione ON cig (data_pubblicazione);
CREATE INDEX IF NOT EXISTS ix_cig_numero_gara   ON cig (numero_gara);

-- ------------------------------------------------------- 2. AGGIUDICAZIONI
CREATE TABLE IF NOT EXISTS aggiudicazioni (
    id_aggiudicazione           TEXT PRIMARY KEY,
    cig                         TEXT NOT NULL,
    data_aggiudicazione_definitiva TEXT,
    data_comunicazione_esito    TEXT,
    cod_esito                   TEXT,
    esito                       TEXT,
    criterio_aggiudicazione     TEXT,

    importo_aggiudicazione      REAL,
    ribasso_aggiudicazione      REAL,
    massimo_ribasso             REAL,
    minimo_ribasso              REAL,

    num_imprese_offerenti       INTEGER,
    num_imprese_invitate        INTEGER,
    num_imprese_richiedenti     INTEGER,
    numero_offerte_ammesse      INTEGER,
    numero_offerte_escluse      INTEGER,
    n_manif_interesse           INTEGER,

    asta_elettronica            TEXT,
    flag_subappalto             TEXT,
    flag_proc_accelerata        TEXT,
    prestazioni_comprese        TEXT,

    src_file    TEXT,
    ingerito_il TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_agg_cig  ON aggiudicazioni (cig);
CREATE INDEX IF NOT EXISTS ix_agg_data ON aggiudicazioni (data_aggiudicazione_definitiva);

-- -------------------------------------------------------- 3. AGGIUDICATARI
CREATE TABLE IF NOT EXISTS aggiudicatari (
    id_aggiudicazione   TEXT NOT NULL,
    codice_fiscale      TEXT NOT NULL,
    cig                 TEXT NOT NULL,
    denominazione       TEXT,
    ruolo               TEXT,
    tipo_soggetto       TEXT,

    src_file    TEXT,
    ingerito_il TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id_aggiudicazione, codice_fiscale)
);

CREATE INDEX IF NOT EXISTS ix_aggt_cig ON aggiudicatari (cig);
CREATE INDEX IF NOT EXISTS ix_aggt_cf  ON aggiudicatari (codice_fiscale);

-- ------------------------------------------------------ 4. AVVIO CONTRATTO
CREATE TABLE IF NOT EXISTS avvio_contratto (
    id_aggiudicazione               TEXT PRIMARY KEY,
    cig                             TEXT NOT NULL,
    data_stipula_contratto          TEXT,
    data_esecutivita_contratto      TEXT,
    data_termine_contrattuale       TEXT,   -- il motore Scadenze sta qui
    data_inizio_effettiva           TEXT,
    data_verbale_prima_consegna     TEXT,
    data_verbale_consegna_definitiva TEXT,
    consegna_frazionata             TEXT,
    consegna_sotto_riserva          TEXT,

    src_file    TEXT,
    ingerito_il TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_avvio_cig     ON avvio_contratto (cig);
CREATE INDEX IF NOT EXISTS ix_avvio_termine ON avvio_contratto (data_termine_contrattuale);

-- --------------------------------------------------------- 5. INGESTION LOG
-- I buchi nella sequenza dei dump sono un dato di prodotto, non un errore.
CREATE TABLE IF NOT EXISTS ingestion_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset         TEXT NOT NULL,
    risorsa         TEXT NOT NULL,
    periodo         TEXT,
    esito           TEXT NOT NULL,   -- ok | assente_404 | errore | zip_corrotto
    http_status     INTEGER,
    byte_scaricati  INTEGER,
    righe_lette     INTEGER,
    righe_upsert    INTEGER,
    last_modified   TEXT,            -- freschezza reale dichiarata dalla fonte
    iniziato_il     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finito_il       TEXT,
    note            TEXT,
    UNIQUE (dataset, risorsa)
);

-- ------------------------------------------------- 6. CONTATTI ENTI (IndicePA)
-- Task R1. Chiave: codice fiscale normalizzato. Match misurato 89,4% sugli enti
-- e 93,9% sulle scadenze a 12 mesi (R1.1, 2026-08-26).
--
-- nome_resp / cogn_resp / titolo_resp NON vengono memorizzati: sono dati
-- personali e il loro uso e' subordinato alla verifica R3. La PEC dell'ente e'
-- un recapito organizzativo, quella si tiene.
CREATE TABLE IF NOT EXISTS ente_contatti (
    cf_ente             TEXT PRIMARY KEY,   -- normalizzato, join con cig.cf_amministrazione_appaltante
    cod_amm             TEXT,
    denominazione_ipa   TEXT,               -- piu' pulita di quella ANAC
    pec                 TEXT,
    mail_alt            TEXT,
    sito_istituzionale  TEXT,
    comune              TEXT,
    provincia           TEXT,
    regione             TEXT,
    cap                 TEXT,
    indirizzo           TEXT,
    tipologia_amm       TEXT,
    tipologia_istat     TEXT,
    aggiornato_il       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_contatti_provincia ON ente_contatti (provincia);
CREATE INDEX IF NOT EXISTS ix_contatti_tipologia ON ente_contatti (tipologia_amm);

-- Scadenze con il recapito attaccato: e' questa che diventa un lead.
-- Colonne elencate, non "s.*". In Postgres la stella dentro una vista viene
-- espansa e congelata alla creazione: aggiungere colonne alla tabella non le fa
-- comparire nella vista, e CREATE OR REPLACE non basta a correggerla. Ci e'
-- costato un errore su Supabase il 2026-08-26. Qui si tiene la stessa forma per
-- non ricopiare l'abitudine sbagliata dall'altra parte.
CREATE VIEW IF NOT EXISTS v_scadenze_contattabili AS
SELECT
    s.cig, s.oggetto_lotto, s.cpv_norm, s.provincia, s.ente,
    s.cf_amministrazione_appaltante, s.data_stipula_contratto,
    s.data_termine_contrattuale, s.giorni_alla_scadenza,
    s.importo_aggiudicazione, s.fornitore_uscente, s.n_fornitori,
    s.cf_fornitore_uscente,
    k.pec,
    k.mail_alt,
    k.denominazione_ipa,
    k.tipologia_amm,
    k.sito_istituzionale,
    g.categoria,
    g.metodo AS categoria_metodo,
    -- Il CF a 16 caratteri e' il formato delle persone fisiche: ditte
    -- individuali e professionisti. Sono 2.464 su 22.018 aggiudicatari.
    -- Il flag serve a poterli escludere dall'outreach in un colpo solo (R3).
    CASE WHEN length(trim(s.cf_fornitore_uscente)) = 16 THEN 1 ELSE 0 END
        AS fornitore_persona_fisica
FROM v_scadenze_prossime s
LEFT JOIN ente_contatti k ON k.cf_ente = s.cf_amministrazione_appaltante
LEFT JOIN categoria g     ON g.cig = s.cig;

-- --------------------------------------------- 7. CATEGORIA FUNZIONALE (R2)
-- Popolata da categorie.py. Copertura misurata 92,0% senza LLM:
-- 64,5% dal CPV (costo zero) + 27,6% da regex sull'oggetto.
CREATE TABLE IF NOT EXISTS categoria (
    cig           TEXT PRIMARY KEY,
    categoria     TEXT,
    metodo        TEXT,           -- cpv | regex | NULL
    aggiornato_il TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_cat ON categoria (categoria);

-- ================================================================== VISTE

-- Una riga per GARA, non per lotto: evita il gonfiaggio del 115%.
CREATE VIEW IF NOT EXISTS v_gara AS
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

-- Motore INTELLIGENCE
CREATE VIEW IF NOT EXISTS v_aggiudicazione_completa AS
SELECT
    c.cig, c.numero_gara, c.oggetto_lotto, c.cpv_norm, c.descrizione_cpv,
    c.provincia, c.cf_amministrazione_appaltante,
    c.denominazione_amministrazione_appaltante AS ente,
    c.data_pubblicazione,
    c.importo_lotto            AS importo_base,
    a.importo_aggiudicazione, a.ribasso_aggiudicazione,
    a.num_imprese_offerenti, a.criterio_aggiudicazione,
    a.data_aggiudicazione_definitiva,
    t.codice_fiscale           AS cf_vincitore,
    t.denominazione            AS vincitore,
    t.tipo_soggetto, c.flag_pnrr_pnc
FROM cig c
JOIN aggiudicazioni a ON a.cig = c.cig
LEFT JOIN aggiudicatari t ON t.id_aggiudicazione = a.id_aggiudicazione;

-- Motore SCADENZE — guarda in avanti, la latenza della fonte non lo tocca.
--
-- UNA RIGA PER CONTRATTO, non per fornitore. Il 9% dei CIG ha piu' di un
-- aggiudicatario (RTI) e sono i grandi: la versione riga-per-fornitore
-- gonfiava il valore del 99,8% (12.078 M invece di 6.045 M). I membri del
-- raggruppamento vengono concatenati in fornitore_uscente.
CREATE VIEW IF NOT EXISTS v_scadenze_prossime AS
SELECT
    c.cig, c.oggetto_lotto, c.cpv_norm, c.provincia,
    c.denominazione_amministrazione_appaltante AS ente,
    c.cf_amministrazione_appaltante,
    v.data_stipula_contratto, v.data_termine_contrattuale,
    CAST(julianday(v.data_termine_contrattuale) - julianday('now') AS INTEGER)
                               AS giorni_alla_scadenza,
    max(a.importo_aggiudicazione) AS importo_aggiudicazione,
    group_concat(DISTINCT t.denominazione)  AS fornitore_uscente,
    count(DISTINCT t.codice_fiscale)        AS n_fornitori,
    min(t.codice_fiscale)                   AS cf_fornitore_uscente
FROM avvio_contratto v
JOIN cig c ON c.cig = v.cig
LEFT JOIN aggiudicazioni a ON a.id_aggiudicazione = v.id_aggiudicazione
LEFT JOIN aggiudicatari t  ON t.id_aggiudicazione = v.id_aggiudicazione
WHERE v.data_termine_contrattuale >= date('now')
GROUP BY c.cig;

-- Profilo COMPETITOR
CREATE VIEW IF NOT EXISTS v_competitor AS
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

-- Profilo ENTE
CREATE VIEW IF NOT EXISTS v_ente AS
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
