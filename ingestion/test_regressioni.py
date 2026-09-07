"""Test sui punti che si sono gia' rotti (task R23).

NON test in generale: test sui **bug veri di questo progetto**, perche' sono
quelli che tornano. Ogni classe qui sotto dice quale guasto e' successo, cosa
era costato, e cosa deve restare vero perche' non succeda di nuovo.

Un test che non protegge da un errore osservato e' un test che invecchia
male: dice che il codice fa quello che fa, e passa anche quando il
comportamento e' sbagliato. Qui ogni caso ha una data e una storia.

Uso:
    python test_regressioni.py            # tutti
    python test_regressioni.py -v         # con il nome di ogni caso
    python test_regressioni.py Categorie  # una classe sola

Solo libreria standard. I casi che hanno bisogno del database o della rete si
saltano da soli quando non ci sono: devono poter girare su una macchina
appena clonata.
"""

import io
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, QUI)
DB = os.path.join(QUI, "radar.db")


def serve_db():
    return unittest.skipUnless(os.path.exists(DB), "radar.db non presente")


# =====================================================================
class Categorie(unittest.TestCase):
    """Un \\b di troppo aveva dimezzato la copertura.

    Il guasto: una parola chiave scritta con i confini di parola non
    matchava piu' dentro le sigle e le parole composte che ANAC usa davvero
    ("SW", "gestionale/software"). La copertura era scesa senza che nessun
    errore lo dicesse — e' il tipo di bug che non si vede, si misura.
    """

    @classmethod
    def setUpClass(cls):
        import categorie
        cls.c = categorie

    def test_oggetti_che_devono_classificare(self):
        # Oggetti veri, presi dal database. Se uno di questi torna None,
        # qualcuno ha stretto una regex.
        casi = [
            ("MANUTENZIONE SOFTWARE GESTIONALE PROTOCOLLO", None),
            ("SERVIZIO DI ASSISTENZA E MANUTENZIONE SOFTWARE 'HUMAN 10'", None),
            ("RINNOVO LICENZE MICROSOFT 365", None),
            ("SERVIZIO SOC SECURITY OPERATION CENTER E ANTIVIRUS", None),
            ("FORNITURA DI CONNETTIVITA' IN FIBRA PER LE SEDI", None),
            ("SVILUPPO DI PERSONALIZZAZIONI APPLICATIVE", None),
        ]
        for oggetto, _ in casi:
            with self.subTest(oggetto=oggetto[:40]):
                cat = self.c.per_regex(oggetto)
                self.assertIsNotNone(
                    cat, f"non classificato piu': {oggetto!r}")

    def test_override_prima_del_cpv(self):
        """Un abbonamento a una banca dati ha CPV 'analytics' ma non lo e'.

        L'override esiste apposta: se qualcuno lo togliesse, questi
        finirebbero in "Dati e analytics" e il punteggio dei lead cambierebbe
        senza che nessuno se ne accorga.
        """
        cat, metodo = self.c.classifica(
            "72320000", "ABBONAMENTO ANNUALE BANCA DATI UPTODATE")
        self.assertEqual(metodo, "override", f"e' finito in {cat} via {metodo}")

    def test_cpv_generico_non_classifica(self):
        """I CPV generici non devono dare una categoria: darebbero una
        precisione che il dato non ha."""
        for cpv in list(self.c.CPV_GENERICI)[:5]:
            self.assertIsNone(self.c.per_cpv(cpv), f"CPV generico {cpv}")

    @serve_db()
    def test_copertura_non_crolla(self):
        """La misura che il \\b aveva fatto scendere. Soglia sotto il valore
        misurato, non uguale: deve segnalare un crollo, non un'oscillazione."""
        cx = sqlite3.connect(DB)
        righe = cx.execute(
            "SELECT cod_cpv, oggetto_lotto FROM cig "
            "WHERE oggetto_lotto IS NOT NULL LIMIT 20000").fetchall()
        cx.close()
        if len(righe) < 1000:
            self.skipTest("troppe poche righe per una misura sensata")
        ok = sum(1 for cpv, ogg in righe if self.c.classifica(cpv, ogg)[0])
        q = ok / len(righe)
        self.assertGreater(
            q, 0.75,
            f"copertura scesa al {q*100:.1f}%: qualcuno ha stretto una regex")


# =====================================================================
class ParsingEnv(unittest.TestCase):
    """Il parsing di .env.local con chiave ripetuta. Ci e' costato una serata.

    Il file puo' contenere la DSN come riga nuda oppure con la chiave davanti,
    e puo' contenere la chiave PIU' DI UNA VOLTA (una vecchia commentata male,
    una nuova). Prendere l'ultima invece della prima significa connettersi al
    database sbagliato senza nessun errore.
    """

    def scrivi(self, testo):
        import push_supabase as ps
        f = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False,
                                        encoding="utf-8")
        f.write(testo)
        f.close()
        self.addCleanup(os.unlink, f.name)
        self.addCleanup(setattr, ps, "ENVFILE", ps.ENVFILE)
        ps.ENVFILE = f.name
        os.environ.pop("RADAR_SUPABASE_DSN", None)
        return ps

    def test_riga_nuda(self):
        ps = self.scrivi("postgresql://u:p@host:5432/db\n")
        self.assertEqual(ps.leggi_dsn(), "postgresql://u:p@host:5432/db")

    def test_con_chiave(self):
        ps = self.scrivi('RADAR_SUPABASE_DSN="postgresql://u:p@h:5432/db"\n')
        self.assertEqual(ps.leggi_dsn(), "postgresql://u:p@h:5432/db")

    def test_bom_del_blocco_note(self):
        """Il Blocco note di Windows aggiunge il BOM senza dirlo, e la prima
        riga diventa '\\ufeffRADAR_...' che non matcha piu' niente."""
        ps = self.scrivi("﻿postgresql://u:p@h:5432/db\n")
        self.assertEqual(ps.leggi_dsn(), "postgresql://u:p@h:5432/db")

    def test_commenti_e_righe_vuote_ignorati(self):
        ps = self.scrivi("# vecchia\n\n#postgresql://vecchio:x@h/db\n"
                         "postgresql://nuovo:p@h:5432/db\n")
        self.assertIn("nuovo", ps.leggi_dsn())

    def test_prima_occorrenza_vince(self):
        """Chiave ripetuta: si prende la prima, e comunque MAI una commentata."""
        ps = self.scrivi("postgresql://primo:p@h:5432/db\n"
                         "postgresql://secondo:p@h:5432/db\n")
        self.assertIn("primo", ps.leggi_dsn())


# =====================================================================
class Mascheramento(unittest.TestCase):
    """La password non deve uscire da nessun messaggio d'errore.

    Un traceback di psycopg contiene la stringa di connessione per intero, e
    console_live la rimanda al browser. E' l'unico segreto del sistema.
    """

    def setUp(self):
        import push_supabase as ps
        self.ps = ps
        self.dsn = "postgresql://postgres:SuperSegreta123@db.abc.supabase.co:5432/postgres"

    def test_dsn_intera(self):
        t = self.ps.maschera(f"errore: {self.dsn} non raggiungibile", self.dsn)
        self.assertNotIn("SuperSegreta123", t)

    def test_solo_la_password(self):
        """Il caso vero: il traceback cita la password da sola, non la DSN."""
        t = self.ps.maschera("password authentication failed for "
                             "SuperSegreta123", self.dsn)
        self.assertNotIn("SuperSegreta123", t)

    def test_senza_dsn_non_esplode(self):
        self.assertEqual(self.ps.maschera("ciao", None), "ciao")


# =====================================================================
class FormatoPEC(unittest.TestCase):
    """Il formato dei file .txt letti da pec_smtp.leggi_messaggio().

    Il file e' la fonte di verita' del testo che parte. Se il formato cambia
    da una parte e non dall'altra, si scopre al momento dell'invio — cioe'
    con una PEC in mano.
    """

    def setUp(self):
        import pec_smtp
        self.p = pec_smtp
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: None)
        self.old = self.p.DOCS
        self.p.DOCS = self.dir
        self.addCleanup(setattr, self.p, "DOCS", self.old)
        os.makedirs(os.path.join(self.dir, "prova"), exist_ok=True)

    def scrivi(self, testo, nome="01-x.txt"):
        with io.open(os.path.join(self.dir, "prova", nome), "w",
                     encoding="utf-8") as f:
            f.write(testo)
        return nome

    def test_giro_completo(self):
        n = self.scrivi("A:       ente@pec.it\nOGGETTO: Un oggetto\n\n"
                        + "-" * 70 + "\n\nSpett.le ENTE\n\ncorpo\n")
        dest, ogg, corpo = self.p.leggi_messaggio("prova", n)
        self.assertEqual(dest, "ente@pec.it")
        self.assertEqual(ogg, "Un oggetto")
        self.assertIn("Spett.le ENTE", corpo)

    def test_oggetto_con_trattini(self):
        """L'oggetto contiene gia' dei trattini lunghi: il separatore non
        deve essere confuso con quelli."""
        n = self.scrivi("A:       e@pec.it\nOGGETTO: Ufficio X — iscrizione — IT\n\n"
                        + "-" * 70 + "\n\ncorpo\n", "02.txt")
        _, ogg, _ = self.p.leggi_messaggio("prova", n)
        self.assertEqual(ogg, "Ufficio X — iscrizione — IT")

    def test_formato_sbagliato_e_un_errore_chiaro(self):
        n = self.scrivi("ciao come va", "03.txt")
        with self.assertRaises(ValueError):
            self.p.leggi_messaggio("prova", n)

    def test_file_mancante_dice_come_rigenerarlo(self):
        with self.assertRaises(FileNotFoundError) as e:
            self.p.leggi_messaggio("prova", "non-esiste.txt")
        self.assertIn("genera_pec", str(e.exception))

    def test_segnaposto_bloccano_l_invio(self):
        """Se MITTENTE non e' compilato il testo esce con [INSERISCI ...]:
        deve fermarsi prima di partire, non dopo."""
        problemi = self.p.verifica("e@pec.it", "oggetto",
                                   "corpo con [INSERISCI NOME]", "e@pec.it")
        self.assertTrue(any("segnaposto" in p for p in problemi))

    def test_destinatario_diverso_da_quello_registrato(self):
        """Rigenerare a meta' un lotto lascia file e righe disallineati: la
        PEC partirebbe a un ente e risulterebbe mandata a un altro."""
        problemi = self.p.verifica("uno@pec.it", "o", "c", "due@pec.it")
        self.assertTrue(problemi)


# =====================================================================
class MediaCheIgnoraIZeri(unittest.TestCase):
    """Due volte lo stesso errore, con due facce.

    (1) La media del ribasso escludeva gli zeri e dava un ribasso medio piu'
        alto del vero.
    (2) In esito.py, avg(contendibile) dava 26,40% invece di 4,72%: la
        colonna e' NULL per le righe senza seguito, e avg() i NULL li salta.
        Una WHERE su NULL e' falsa, un avg() su NULL e' assenza — e la
        differenza fra le due e' un fattore cinque sul numero che il prodotto
        vende.

    Il test e' su SQL vero, in SQLite in memoria: e' li' che il
    comportamento sta, non nel Python.
    """

    def setUp(self):
        self.cx = sqlite3.connect(":memory:")
        self.cx.executescript("""
            CREATE TABLE t (id INTEGER, contendibile INTEGER, ribasso REAL);
            INSERT INTO t VALUES (1,1,10.0),(2,0,0.0),(3,NULL,NULL),
                                 (4,0,0.0),(5,NULL,NULL);
        """)

    def test_avg_salta_i_null(self):
        """La prova che il bug esiste: e' cosi' che si comporta SQL."""
        ingenuo = self.cx.execute("SELECT avg(contendibile) FROM t").fetchone()[0]
        corretto = self.cx.execute(
            "SELECT avg(coalesce(contendibile,0)) FROM t").fetchone()[0]
        self.assertAlmostEqual(ingenuo, 1 / 3)      # 33%: sbagliato
        self.assertAlmostEqual(corretto, 1 / 5)     # 20%: giusto
        self.assertNotAlmostEqual(ingenuo, corretto)

    def test_esito_usa_coalesce(self):
        """Che il codice vero lo faccia, non solo che SQL si comporti cosi'."""
        with io.open(os.path.join(QUI, "esito.py"), encoding="utf-8") as f:
            src = f.read()
        for pezzo in ("avg(contendibile)", "avg( contendibile)"):
            self.assertNotIn(
                pezzo, src,
                "avg(contendibile) senza coalesce: i NULL vengono saltati e "
                "il tasso base torna a essere cinque volte piu' alto del vero")

    def test_media_ribasso_include_gli_zeri(self):
        tutti = self.cx.execute(
            "SELECT avg(ribasso) FROM t WHERE ribasso IS NOT NULL").fetchone()[0]
        senza_zeri = self.cx.execute(
            "SELECT avg(ribasso) FROM t WHERE ribasso > 0").fetchone()[0]
        self.assertLess(tutti, senza_zeri)


# =====================================================================
class AggregazionePerCIG(unittest.TestCase):
    """Gli RTI gonfiavano il valore del 99,8%.

    Un contratto vinto da un raggruppamento ha una riga per impresa. Sommare
    gli importi senza aggregare prima per CIG conta lo stesso contratto tante
    volte quante sono le imprese — e su un RTI da otto imprese il valore
    diventa otto volte quello vero.
    """

    def setUp(self):
        self.cx = sqlite3.connect(":memory:")
        self.cx.executescript("""
            CREATE TABLE agg (cig TEXT, impresa TEXT, importo REAL);
            INSERT INTO agg VALUES
              ('AAA','uno',1000),('AAA','due',1000),('AAA','tre',1000),
              ('BBB','solo',500);
        """)

    def test_somma_ingenua_gonfia(self):
        ingenua = self.cx.execute("SELECT sum(importo) FROM agg").fetchone()[0]
        corretta = self.cx.execute(
            "SELECT sum(i) FROM (SELECT cig, max(importo) i FROM agg "
            "GROUP BY cig)").fetchone()[0]
        self.assertEqual(ingenua, 3500)
        self.assertEqual(corretta, 1500)

    @serve_db()
    def test_niente_cig_doppi_nelle_scadenze(self):
        """Sul database vero: un CIG non deve comparire due volte fra le
        scadenze, o il valore totale e' gonfio."""
        cx = sqlite3.connect(DB)
        try:
            n = cx.execute(
                "SELECT count(*) FROM (SELECT cig FROM punteggio "
                "GROUP BY cig HAVING count(*) > 1)").fetchone()[0]
        except sqlite3.OperationalError:
            self.skipTest("tabella punteggio non presente")
        finally:
            cx.close()
        self.assertEqual(n, 0, f"{n} CIG duplicati: il valore e' gonfio")


# =====================================================================
class DownloadTroncato(unittest.TestCase):
    """Un file scaricato a meta' veniva messo in cache come buono.

    Costo reale: due delta ANAC del 2026 sembravano "ZIP corrotto" e ci hanno
    mandato a cercare un problema da ANAC. Erano download interrotti: un ZIP
    troncato ha l'intestazione valida e la coda mancante, quindi si presenta
    come corrotto invece che come incompleto — e la cache lo promuoveva a
    buono al giro dopo. 47.038 CIG mancanti, e nessun errore.
    """

    def avvia_server(self, corpo, dichiara):
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(dichiara))
                self.end_headers()
                self.wfile.write(corpo)

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_port}/f.zip"

    def test_file_corto_non_resta_su_disco(self):
        import anac_http
        url = self.avvia_server(b"PK\x03\x04" + b"x" * 100, 100000)
        dest = os.path.join(tempfile.mkdtemp(), "f.zip")
        try:
            anac_http.scarica(url, dest)
        except Exception:
            pass    # che fallisca va bene; quello che conta e' cosa resta
        self.assertFalse(
            os.path.exists(dest),
            "il file troncato e' rimasto: al giro dopo la cache lo dara' per "
            "buono e mancheranno righe senza nessun errore")

    def test_file_intero_resta(self):
        import anac_http
        corpo = b"PK\x03\x04" + b"y" * 500
        url = self.avvia_server(corpo, len(corpo))
        dest = os.path.join(tempfile.mkdtemp(), "ok.zip")
        anac_http.scarica(url, dest)
        self.assertTrue(os.path.exists(dest))
        self.assertEqual(os.path.getsize(dest), len(corpo))


# =====================================================================
class CodiciFiscali(unittest.TestCase):
    """827 aggiudicatari avevano la P.IVA a 10 cifre invece di 11.

    ANAC ha la stessa impresa sotto CF diversi: lo zero iniziale si perde
    quando il campo passa da un foglio di calcolo. Senza normalizzare,
    'DROMEDIAN SRL' e 'DROMEDIAN S.R.L.' risultavano fornitori diversi e 41
    rinnovi su 660 venivano classificati come cambio di fornitore — cioe'
    come porte aperte che non erano aperte.
    """

    @classmethod
    def setUpClass(cls):
        import esito
        cls.e = esito

    def test_zero_iniziale(self):
        self.assertEqual(self.e.norm_cf("3015146"), self.e.norm_cf("00003015146"))

    def test_cf_persona_fisica_intatto(self):
        cf = "RSSMRA80A01H501U"
        self.assertEqual(self.e.norm_cf(cf), cf.upper())

    def test_forme_societarie_equivalenti(self):
        a = self.e.norm_nome("DROMEDIAN SRL")
        b = self.e.norm_nome("DROMEDIAN S.R.L.")
        self.assertEqual(a, b, "le forme societarie devono normalizzare uguale")

    def test_nomi_diversi_restano_diversi(self):
        self.assertNotEqual(self.e.norm_nome("ALFA SRL"),
                            self.e.norm_nome("BETA SRL"))


# =====================================================================
class Funnel(unittest.TestCase):
    """Il funnel conta in modo cumulativo, e i motivi sono una lista chiusa."""

    @classmethod
    def setUpClass(cls):
        import invii
        cls.i = invii

    def test_stadi_in_ordine(self):
        nomi = [n for n, _ in self.i.FUNNEL]
        self.assertEqual(nomi[0], "destinatari")
        self.assertEqual(nomi[-1], "Vendita")

    def test_ogni_stadio_ha_la_sua_colonna_data(self):
        """Senza date separate non si sa quanto si resta fermi in uno stadio,
        che e' l'unico modo per capire dove il funnel perde."""
        for etichetta, col in self.i.FUNNEL[1:]:
            self.assertTrue(col and col.endswith("_il"),
                            f"stadio {etichetta} senza colonna data")

    def test_motivi_lista_chiusa(self):
        """Con il testo libero, fra sei mesi 'gia' fornito' e 'hanno gia' un
        fornitore' sono due righe diverse in un conteggio."""
        self.assertIn("gia-fornitore", self.i.MOTIVI)
        for k in self.i.MOTIVI:
            self.assertNotIn(" ", k, f"chiave con spazi: {k}")

    def test_stati_e_date_allineati(self):
        """console_live e invii.py scrivono la stessa riga: se le due mappe
        stato->colonna divergessero, il funnel conterebbe due volte."""
        try:
            import console_live
        except ImportError:
            self.skipTest("console_live non importabile (manca psycopg)")
        self.assertEqual(self.i.DATE_STATO, console_live.DATE_STATO,
                         "le due mappe stato->colonna sono divergenti")


# =====================================================================
class DatiPersonali(unittest.TestCase):
    """R3 e liceita.md §7: certe colonne NON devono esistere.

    Non e' un test di correttezza, e' un test di disciplina: se un domani
    qualcuno carica i nominativi dei responsabili senza passare da
    docs/liceita.md, questo si accorge prima che i dati vadano in produzione.
    """

    def test_uffici_non_carica_colonne_personali(self):
        import uffici
        for c in ("nome_resp", "cogn_resp", "mail_resp", "tel_resp"):
            self.assertIn(c, uffici.ESCLUSE)

    @serve_db()
    def test_nessuna_colonna_personale_nel_db(self):
        cx = sqlite3.connect(DB)
        trovate = []
        for (tab,) in cx.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"):
            for r in cx.execute(f"PRAGMA table_info({tab})"):
                if r[1].lower() in ("nome_resp", "cogn_resp", "mail_resp",
                                    "tel_resp", "titolo_resp"):
                    trovate.append(f"{tab}.{r[1]}")
        cx.close()
        self.assertEqual(
            trovate, [],
            "colonne con dati personali nel database: vanno prima chiuse le "
            "tre domande di docs/liceita.md §7")


# =====================================================================
class UfficiRilevanti(unittest.TestCase):
    """I falsi amici: un ufficio che sembra informatico e non lo e'."""

    @classmethod
    def setUpClass(cls):
        import uffici
        cls.u = uffici

    def test_riconosce_gli_uffici_veri(self):
        for nome in ("Sistemi Informativi", "Servizi Informatici", "CED",
                     "Ufficio per la transizione al Digitale",
                     "Area ICT e Innovazione"):
            self.assertGreaterEqual(self.u.punteggio(nome), self.u.MIN_PUNTI,
                                    f"non riconosciuto: {nome}")

    def test_scarta_i_falsi_amici(self):
        """Senza questo, 'Anagrafe - servizi demografici informatizzati'
        entrava a pieni voti."""
        for nome in ("Ufficio Anagrafe e servizi demografici informatizzati",
                     "Servizio Tributi", "Ufficio Protocollo",
                     "Ragioneria e bilancio"):
            self.assertEqual(self.u.punteggio(nome), 0, f"accettato: {nome}")

    def test_solo_le_pec_contano(self):
        """Una casella ordinaria non e' un domicilio digitale: un ente non e'
        tenuto a leggerla."""
        self.assertIsNone(self.u.pec_ufficio(
            {"mail1": "info@comune.it", "tipo_mail1": "Altro"}))
        self.assertEqual(self.u.pec_ufficio(
            {"mail1": "info@comune.it", "tipo_mail1": "Altro",
             "mail2": "pec@comune.it", "tipo_mail2": "Pec"}), "pec@comune.it")
        self.assertIsNone(self.u.pec_ufficio(
            {"mail1": "null", "tipo_mail1": "Pec"}))


# =====================================================================
class DatiWebhookN8N(unittest.TestCase):
    """La regola del CLAUDE.md: i webhook n8n wrappano il payload in .body.

    Non e' codice di questo repo, ma e' l'errore che si ripete a ogni
    workflow nuovo e costa mezz'ora ogni volta. Il test guarda i JSON dei
    workflow, se ci sono.
    """

    def test_espressioni_webhook(self):
        d = os.path.join(QUI, "..", "workflows")
        if not os.path.isdir(d):
            self.skipTest("nessuna cartella workflows")
        sbagliate = []
        for nome in os.listdir(d):
            if not nome.endswith(".json"):
                continue
            with io.open(os.path.join(d, nome), encoding="utf-8") as f:
                t = f.read()
            if "webhook" in t.lower():
                import re
                for m in re.finditer(r"\{\{\s*\$json\.(?!body)(\w+)", t):
                    sbagliate.append(f"{nome}: $json.{m.group(1)}")
        self.assertEqual(sbagliate[:5], [],
                         "n8n wrappa il payload HTTP dentro .body")


if __name__ == "__main__":
    unittest.main(verbosity=2)
