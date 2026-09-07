-- =============================================================================
-- Radar Appalti — schema del livello di servizio su Supabase
-- =============================================================================
-- Applicato automaticamente da push_supabase.py prima di ogni push.
--
-- PERCHE' ESISTE QUESTO FILE. Le viste Postgres congelano l'elenco delle colonne
-- alla creazione: dopo un ALTER TABLE ADD COLUMN non vedono la colonna nuova, e
-- CREATE OR REPLACE non basta a correggerle quando la posizione delle colonne
-- cambia. Gestendole a mano nel dashboard il problema si e' presentato due volte
-- (denominazione_ipa il 2026-08-26, poi categoria lo stesso giorno). Qui le
-- viste vengono ricreate a ogni push, quindi non possono restare indietro
-- rispetto al codice che le popola.
--
-- Tutto e' idempotente: ADD COLUMN IF NOT EXISTS e DROP VIEW IF EXISTS.
-- =============================================================================

-- Colonne aggiunte dopo il setup iniziale. Elencarle qui invece che nel
-- dashboard significa che un database nuovo si allinea da solo.
ALTER TABLE radar.scadenze
    ADD COLUMN IF NOT EXISTS pec                text,
    ADD COLUMN IF NOT EXISTS mail_alt           text,
    ADD COLUMN IF NOT EXISTS denominazione_ipa  text,
    ADD COLUMN IF NOT EXISTS tipologia_amm      text,
    ADD COLUMN IF NOT EXISTS sito_istituzionale text,
    ADD COLUMN IF NOT EXISTS categoria          text,
    ADD COLUMN IF NOT EXISTS fornitore_persona_fisica integer;

CREATE INDEX IF NOT EXISTS ix_scad_categoria ON radar.scadenze (categoria);
CREATE INDEX IF NOT EXISTS ix_scad_tipologia ON radar.scadenze (tipologia_amm);

-- Stato delle notifiche gia' inviate. Sta qui e non in locale cosi' sopravvive
-- a un cambio di macchina, ed e' leggibile sia da notifica.py sia dai workflow
-- n8n: sono due implementazioni dello stesso R4 e devono condividere lo stato,
-- altrimenti attivandole entrambe riceveresti tutto due volte.
CREATE TABLE IF NOT EXISTS radar.notificato (
    cig        text NOT NULL,
    canale     text NOT NULL,          -- telegram | email
    inviato_il timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (cig, canale)
);

-- Tracciamento degli invii PEC. Senza questo, fra tre settimane non si
-- distingue "non hanno risposto" da "non l'ho mandata" — e sono due
-- conclusioni opposte: la prima dice che il messaggio non funziona, la
-- seconda che il lavoro non e' finito.
CREATE TABLE IF NOT EXISTS radar.invio (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lotto        text NOT NULL,          -- 'pec' | 'pec-marche' | ...
    progressivo  integer NOT NULL,       -- il numero nel nome del file
    file         text NOT NULL,
    ente         text,
    provincia    text,
    pec          text NOT NULL,
    cig_inclusi  text,
    n_contratti  integer,
    stato        text NOT NULL DEFAULT 'da_inviare',
        -- da_inviare | inviata | risposta | nessuna_risposta | non_consegnata | chiusa
    inviata_il   date,
    risposta_il  date,
    note         text,
    creata_il    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (lotto, progressivo)
);

CREATE INDEX IF NOT EXISTS ix_invio_stato ON radar.invio (stato);

-- Esito tecnico dell'invio, letto dalle ricevute PEC (task R15). Senza queste
-- colonne una PEC mai consegnata resta 'inviata' per sempre e si aspetta una
-- risposta che non puo' arrivare: casella piena, indirizzo dismesso, dominio
-- che rifiuta. La ricevuta di consegna e' anche la prova legale che l'ente ha
-- ricevuto, e va conservata.
ALTER TABLE radar.invio
    ADD COLUMN IF NOT EXISTS message_id     text,
    ADD COLUMN IF NOT EXISTS accettata_il   timestamptz,
    ADD COLUMN IF NOT EXISTS consegnata_il  timestamptz,
    ADD COLUMN IF NOT EXISTS errore_consegna text,
    -- Sollecito (task R16): un solo secondo contatto per ente, mai due.
    ADD COLUMN IF NOT EXISTS sollecitata_il date;

-- Il message_id e' la chiave con cui le ricevute ritrovano la riga.
CREATE INDEX IF NOT EXISTS ix_invio_msgid ON radar.invio (message_id);

-- RLS anche qui. Le quattro tabelle del primo setup ce l'hanno, invio e
-- notificato sono nate dopo e ne erano rimaste scoperte: non sfruttabile
-- finche' lo schema radar resta fuori da PostgREST, ma e' una difesa che
-- deve valere per tutte, non per alcune.
ALTER TABLE radar.invio      ENABLE ROW LEVEL SECURITY;
ALTER TABLE radar.notificato ENABLE ROW LEVEL SECURITY;

-- Ordine di drop = inverso delle dipendenze.
DROP VIEW IF EXISTS radar.v_lead_90gg;
DROP VIEW IF EXISTS radar.v_scadenze_90gg;
DROP VIEW IF EXISTS radar.v_scadenze;

-- Colonne esplicite, mai "*": la stella dentro una vista viene espansa e
-- congelata alla creazione.
CREATE VIEW radar.v_scadenze AS
SELECT
    cig, oggetto_lotto, cpv_norm, categoria, provincia, ente, cf_ente,
    tipologia_amm, denominazione_ipa, pec, mail_alt, sito_istituzionale,
    data_stipula_contratto, data_termine_contrattuale,
    importo_aggiudicazione, fornitore_uscente, n_fornitori,
    cf_fornitore_uscente, fornitore_persona_fisica, aggiornato_il,
    punteggio, prob_apertura, valore_atteso,
    (data_termine_contrattuale - CURRENT_DATE) AS giorni_alla_scadenza
FROM radar.scadenze
WHERE data_termine_contrattuale >= CURRENT_DATE;

CREATE VIEW radar.v_scadenze_90gg AS
SELECT
    cig, oggetto_lotto, cpv_norm, categoria, provincia, ente, cf_ente,
    tipologia_amm, denominazione_ipa, pec, mail_alt, sito_istituzionale,
    data_stipula_contratto, data_termine_contrattuale,
    importo_aggiudicazione, fornitore_uscente, n_fornitori,
    cf_fornitore_uscente, fornitore_persona_fisica, giorni_alla_scadenza,
    punteggio, prob_apertura, valore_atteso
FROM radar.v_scadenze
WHERE giorni_alla_scadenza <= 90
-- R18: prima i lead che valgono, non i primi a scadere. Ordinare per data
-- tratta uguale una scadenza che diventera' una gara e una che verra'
-- rinnovata in silenzio allo stesso fornitore.
ORDER BY punteggio DESC NULLS LAST, giorni_alla_scadenza;

-- La vista che interroga n8n: una riga per lead, nessun join da scrivere.
CREATE VIEW radar.v_lead_90gg AS
SELECT
    cig,
    giorni_alla_scadenza,
    data_termine_contrattuale,
    COALESCE(denominazione_ipa, ente) AS ente,
    provincia,
    tipologia_amm,
    categoria,
    oggetto_lotto,
    cpv_norm,
    importo_aggiudicazione,
    fornitore_uscente,
    n_fornitori,
    fornitore_persona_fisica,
    pec,
    mail_alt,
    sito_istituzionale,
    punteggio,
    prob_apertura,
    valore_atteso
FROM radar.v_scadenze
WHERE giorni_alla_scadenza <= 90
  AND pec IS NOT NULL
ORDER BY punteggio DESC NULLS LAST, importo_aggiudicazione DESC NULLS LAST;

-- ---------------------------------------------------------------- R17: esito
-- Che fine ha fatto la scadenza. Calcolato in locale da esito.py e spedito
-- come dato derivato, come tutto il resto: su Supabase non arrivano grezzi.
CREATE TABLE IF NOT EXISTS radar.esito (
    cig                   text PRIMARY KEY,
    cf_ente               text,
    ente                  text,
    provincia             text,
    oggetto               text,
    cod_cpv               text,
    data_termine          date,
    importo               numeric,
    fornitore_uscente     text,
    cig_seguito           text,
    tipo_scelta_seguito   text,
    fornitore_subentrante text,
    esito                 text NOT NULL,
    -- separata dall'esito di proposito: un rinnovo allo stesso fornitore dopo
    -- una gara aperta e' un mercato contendibile in cui abbiamo perso, non una
    -- porta chiusa. Sono due domande diverse.
    contendibile          smallint,
    -- confidenza del match sull'oggetto: sotto 0,60 va guardato a occhio
    punteggio             real
);
CREATE INDEX IF NOT EXISTS ix_esito_esito ON radar.esito (esito);
CREATE INDEX IF NOT EXISTS ix_esito_ente  ON radar.esito (cf_ente);

-- Il tasso di porta aperta per ente: quanto conviene insistere su questo
-- committente. Alimenta il lead scoring (R18).
CREATE OR REPLACE VIEW radar.v_ente_apertura AS
SELECT cf_ente,
       max(ente)                                                AS ente,
       max(provincia)                                           AS provincia,
       count(*)                                                 AS scadenze_osservate,
       count(*) FILTER (WHERE esito = 'nuovo_fornitore'
                           OR contendibile = 1)                 AS porte_aperte,
       round(100.0 * count(*) FILTER (WHERE esito = 'nuovo_fornitore'
                                         OR contendibile = 1)
             / NULLIF(count(*), 0), 1)                          AS quota_aperta
FROM radar.esito
GROUP BY cf_ente
HAVING count(*) >= 3;

-- ------------------------------------------------------------- R18: punteggio
-- Il punteggio del lead, calcolato in locale da punteggio.py.
ALTER TABLE radar.scadenze
    ADD COLUMN IF NOT EXISTS punteggio     smallint,
    ADD COLUMN IF NOT EXISTS prob_apertura real,
    ADD COLUMN IF NOT EXISTS valore_atteso numeric;
CREATE INDEX IF NOT EXISTS ix_scadenze_punteggio
    ON radar.scadenze (punteggio DESC NULLS LAST);


-- ---------------------------------------------------------------- R25: funnel
-- Il tracciamento si fermava a 'risposta', e "tasso di risposta 8%" non dice
-- se il canale e' redditizio. Servono gli stati fino al fatturato.
--
-- I nomi sono quelli degli stage GoHighLevel gia' in uso sui clienti. Non e'
-- pedanteria: se un domani questo funnel passa nel CRM, l'import e' un
-- copia-incolla invece che una mappatura da riscrivere.
ALTER TABLE radar.invio
    ADD COLUMN IF NOT EXISTS discovery_fissata_il date,
    ADD COLUMN IF NOT EXISTS discovery_fatta_il   date,
    ADD COLUMN IF NOT EXISTS offerta_il           date,
    ADD COLUMN IF NOT EXISTS vendita_il           date,
    ADD COLUMN IF NOT EXISTS persa_il             date,
    ADD COLUMN IF NOT EXISTS motivo_perdita       text,
    -- Senza l'importo, "3 vendite" non dice se il canale ripaga il lavoro.
    ADD COLUMN IF NOT EXISTS valore_offerta       numeric,
    ADD COLUMN IF NOT EXISTS valore_vendita       numeric;

CREATE INDEX IF NOT EXISTS ix_invio_stato ON radar.invio (stato);

-- Il funnel in una riga per lotto: quanti ne sono passati per ogni stadio.
-- Cumulativo, non per stato corrente: un contatto arrivato a 'Vendita' e'
-- passato anche da 'Offerta', e contarlo solo nell'ultimo stadio farebbe
-- sembrare vuoti quelli prima.
CREATE OR REPLACE VIEW radar.v_funnel AS
SELECT
    lotto,
    count(*)                                             AS destinatari,
    count(*) FILTER (WHERE inviata_il IS NOT NULL)       AS inviate,
    count(*) FILTER (WHERE consegnata_il IS NOT NULL)    AS consegnate,
    count(*) FILTER (WHERE risposta_il IS NOT NULL)      AS risposte,
    count(*) FILTER (WHERE discovery_fissata_il IS NOT NULL) AS discovery_fissate,
    count(*) FILTER (WHERE discovery_fatta_il IS NOT NULL)   AS discovery_fatte,
    count(*) FILTER (WHERE offerta_il IS NOT NULL)       AS offerte,
    count(*) FILTER (WHERE vendita_il IS NOT NULL)       AS vendite,
    count(*) FILTER (WHERE persa_il IS NOT NULL)         AS perse,
    sum(valore_offerta) FILTER (WHERE offerta_il IS NOT NULL) AS valore_offerto,
    sum(valore_vendita) FILTER (WHERE vendita_il IS NOT NULL) AS valore_vinto
FROM radar.invio
GROUP BY lotto;


-- --------------------------------------------------- R13: irrobustimento
-- Misurato il 2026-09-07 con ingestion/sicurezza.py. Due cose vere, nessuna
-- delle quali si vede aprendo la console di Supabase.
--
-- 1. radar.esito era nata (R17) senza RLS, mentre tutte le altre ce l'hanno.
--    E' esattamente il modo in cui queste cose si degradano: non si spegne
--    una protezione, si crea una tabella nuova e ci si dimentica.
ALTER TABLE radar.esito ENABLE ROW LEVEL SECURITY;

-- 2. Il trabocchetto meno visibile. Una vista, per default, legge coi diritti
--    di CHI L'HA CREATA — qui 'postgres', che ha bypassrls. Quindi si accende
--    RLS su tutte le tabelle, si e' convinti di essere a posto, e una singola
--    vista esposta continua a servire tutto a chiunque. security_invoker la
--    fa leggere coi diritti di chi la chiama.
--
--    Non cambia niente per noi: la DSN e' 'postgres', che RLS lo scavalca
--    comunque. Serve il giorno in cui qualcosa legge con la chiave anon.
ALTER VIEW radar.v_scadenze       SET (security_invoker = on);
ALTER VIEW radar.v_scadenze_90gg  SET (security_invoker = on);
ALTER VIEW radar.v_lead_90gg      SET (security_invoker = on);
ALTER VIEW radar.v_ente_apertura  SET (security_invoker = on);
ALTER VIEW radar.v_funnel         SET (security_invoker = on);

-- Nessuna policy, di proposito: RLS attivo senza policy significa che non
-- legge nessuno tranne chi ha bypassrls. Finche' l'unico accesso e' la chiave
-- di servizio, e' la configurazione piu' restrittiva possibile. Le policy
-- serviranno il giorno in cui si esporra' qualcosa, non prima: scriverle ora
-- vorrebbe dire indovinare a chi dare accesso.
