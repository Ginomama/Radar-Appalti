#!/usr/bin/env python3
"""
Job schedulati del Radar (task R9).

Il problema che risolve: ogni pezzo del sistema funziona lanciato a mano, e
finche' si lancia a mano il sistema non esiste. Un radar che qualcuno deve
ricordarsi di accendere e' una cartella di script, non un prodotto.

Tre ritmi, perche' le cose non scadono tutte insieme:

  --giornaliero   le ricevute PEC arrivano in minuti: leggerle una volta al
                  mese vorrebbe dire scoprire a fine mese che meta' delle PEC
                  non sono mai state consegnate. Ci sta anche il backup delle
                  tabelle operative (invio, notificato: le uniche che nessuno
                  puo' rigenerare) e la notifica Telegram dei lead nuovi.

  --mensile       ANAC pubblica i delta il primo del mese. Scarica, ingerisce,
                  ricalcola e ripubblica su Supabase, con backup completo
                  prima di toccare qualsiasi cosa.

  --feriale       Lun-Ven, invia le PEC in coda (R31): preavviso Telegram con
                  finestra STOP, poi spedisce se nessuno la ferma. Solo nei
                  giorni feriali perche' un protocollo non legge il sabato.

Vincolo architetturale: **non puo' girare su n8n Cloud**. Il WAF di ANAC
risponde 403 agli IP dei cloud provider, quindi il download deve partire da
una linea residenziale. Da qui l'Utilita' di pianificazione di Windows.

Regole di questo file:

  - nessuno step blocca uno step indipendente. Se il backup fallisce, la
    lettura delle ricevute parte lo stesso. Se l'ingest fallisce, il push su
    Supabase NON parte: pubblicherebbe dati a meta'.
  - ogni output passa da maschera() prima di finire nel log: un traceback di
    psycopg o di smtplib contiene la DSN completa, password inclusa.
  - se qualcosa fallisce lo si viene a sapere. Un job schedulato che fallisce
    in silenzio e' peggio di un job che non esiste, perche' si continua a
    credere che i dati siano freschi.

Uso:
    python job.py --giornaliero
    python job.py --mensile
    python job.py --feriale
    python job.py --prova --mensile      # elenca gli step, non esegue niente
    python job.py --installa             # crea le tre attivita' pianificate
    python job.py --disinstalla
    python job.py --stato                # ultimi esiti + stato attivita'

Dipendenze: solo stdlib (i singoli step hanno le loro).
"""

import argparse
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from push_supabase import leggi_dsn

QUI = os.path.dirname(os.path.abspath(__file__))
LOGDIR = os.path.join(QUI, "logs")

# Quanti giorni di log tenere. Sessanta bastano per due cicli mensili interi:
# se un delta di due mesi fa e' andato storto, il log c'e' ancora.
GIORNI_LOG = 60

# Nomi delle attivita' in Utilita' di pianificazione. Il prefisso serve a
# ritrovarle: `schtasks /query /tn Radar\` le elenca tutte e due.
CARTELLA_TASK = "Radar"
TASKS = {
    "giornaliero": dict(nome=f"{CARTELLA_TASK}\\radar-giornaliero",
                        quando=["/sc", "daily", "/st", "08:30"],
                        descr="ricevute PEC, backup operative, Telegram"),
    "mensile":     dict(nome=f"{CARTELLA_TASK}\\radar-mensile",
                        # Il giorno 3 e non il 1: ANAC pubblica il primo del
                        # mese ma non sempre a mezzanotte, e un 404 al primo
                        # tentativo costa un mese di ritardo.
                        quando=["/sc", "monthly", "/d", "3", "/st", "07:00"],
                        descr="delta ANAC, bulk, push Supabase, backup completo"),
    "feriale":     dict(nome=f"{CARTELLA_TASK}\\radar-feriale",
                        # Solo Lun-Ven: invio_automatico.py ricontrolla da
                        # solo il giorno (difesa in profondita', non fiducia
                        # cieca nello scheduler) ma il trigger resta la prima
                        # barriera.
                        quando=["/sc", "weekly", "/d", "MON,TUE,WED,THU,FRI",
                                "/st", "08:00"],
                        descr="invio automatico PEC in coda, con finestra STOP (R31)"),
}

# ------------------------------------------------------------------ step
#
# blocca: se questo step fallisce, salta gli step che lo dichiarano in
# 'dipende'. Tutto il resto prosegue.

PIANI = {
    "giornaliero": [
        dict(id="backup",  argv=["backup.py"],
             descr="backup tabelle operative", minuti=10,
             richiede=("DSN",)),
        dict(id="ricevute", argv=["pec_imap.py", "--leggi"],
             descr="lettura ricevute PEC", minuti=15,
             richiede=("DSN", "PEC_USER", "PEC_PASSWORD")),
        # Stessa casella IMAP di 'ricevute', ma guarda la posta vera invece
        # delle ricevute tecniche (R37): avvisa su Telegram se e' arrivata
        # una risposta a un invio ancora senza esito, cosi' non serve piu'
        # controllare la casella a mano ogni giorno.
        dict(id="risposte", argv=["pec_imap.py", "--risposte", "--telegram"],
             descr="avviso risposte PEC in arrivo (R37)", minuti=15,
             richiede=("DSN", "PEC_USER", "PEC_PASSWORD",
                       "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")),
        # Dopo 'ricevute' e non prima: l'anti-duplicato guarda chi e' gia'
        # in radar.invio, e vale la pena farlo con lo stato di oggi, non con
        # quello di ieri sera. Soglia e tetto sono in auto_genera.py, non
        # qui: sono una scelta commerciale (R30), non l'orario di un job.
        dict(id="auto-genera", argv=["auto_genera.py"],
             descr="bozze PEC automatiche per i lead migliori (R30)", minuti=10,
             richiede=("DSN",)),
        # TED e' nel giornaliero e non nel mensile: una gara aperta ha una
        # scadenza per presentare offerta, e scoprirla il mese dopo vuol dire
        # scoprirla quando e' chiusa. Costa una chiamata HTTP.
        dict(id="ted",     argv=["ted.py", "--ingest", "--giorni", "7"],
             descr="gare europee aperte (R5)", minuti=10),
        # Dopo 'ted' perche' valuta solo bandi gia' ingeriti (verdetto IS
        # NULL): un bando nuovo di oggi va prima scritto in ted_avviso, poi
        # giudicato. Non blocca 'ted-push': un bando senza parere oggi resta
        # visibile lo stesso, si rivaluta al giro dopo.
        dict(id="ted-verdetto", argv=["ted_verdetto.py"],
             descr="parere AI su bandi TED nuovi (R34)", minuti=10,
             dipende="ted", richiede=("ANTHROPIC_API_KEY",)),
        # Fino al 2026-09-19 radar.ted su Supabase si aggiornava solo col
        # push mensile: il ted locale sopra girava ogni giorno ma restava
        # chiuso qui, e la console vedeva bandi vecchi anche un mese. Un
        # push mirato (--solo, non l'intera MAPPA) tiene il pannello fresco
        # senza aspettare il resto della pipeline ANAC.
        dict(id="ted-push", argv=["push_supabase.py", "--solo", "ted_avviso"],
             descr="pubblica i bandi TED su Supabase (R5)", minuti=5,
             dipende="ted", richiede=("DSN",)),
        # Stessa logica di TED, altra fonte (R40): sotto soglia, una regione
        # sola. Nessuna chiave richiesta, e' scraping non un'API a pagamento.
        dict(id="suam",     argv=["suam.py", "--ingest"],
             descr="bandi SUAM Marche aperti (R40)", minuti=5),
        # Dopo 'suam', prima di 'suam-push': stesso motivo di ted-verdetto,
        # ma qui c'e' anche --telegram, perche' l'utente ha chiesto di
        # essere avvisato sui bandi fattibili, non solo di vederli in
        # console (TED non lo fa ancora). Un bando senza parere oggi resta
        # comunque visibile, si rivaluta al giro dopo.
        dict(id="suam-verdetto", argv=["suam_verdetto.py", "--telegram"],
             descr="parere AI + avviso Telegram sui bandi SUAM fattibili", minuti=10,
             dipende="suam", richiede=("ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN",
                                        "TELEGRAM_CHAT_ID")),
        dict(id="suam-push", argv=["push_supabase.py", "--solo", "suam_avviso"],
             descr="pubblica i bandi SUAM su Supabase (R40)", minuti=5,
             dipende="suam", richiede=("DSN",)),
        dict(id="telegram", argv=["notifica.py", "--telegram"],
             descr="notifica lead nuovi", minuti=5,
             richiede=("DSN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")),
    ],
    "feriale": [
        # Timeout largo apposta: 25 minuti di finestra STOP (default) piu'
        # il tempo di inviare fino a --tetto PEC. Se la finestra viene
        # allargata da riga di comando, va allargato anche questo.
        dict(id="invio-auto", argv=["invio_automatico.py"],
             descr="invio PEC in coda con finestra STOP (R31)", minuti=40,
             richiede=("DSN", "PEC_USER", "PEC_PASSWORD",
                       "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")),
    ],
    "mensile": [
        # Primo di tutto, e dura secondi: i test di regressione girano PRIMA
        # delle quattro ore di ingestione, non dopo. Se una regex di
        # categorie.py e' stata stretta, meglio saperlo adesso che dopo aver
        # riscritto il database con una classificazione dimezzata.
        dict(id="test",    argv=["test_regressioni.py"],
             descr="test sui guasti gia' successi (R23)", minuti=2),
        dict(id="sicurezza", argv=["sicurezza.py", "--breve"],
             descr="controlli RLS e privilegi (R13)", minuti=2,
             richiede=("DSN",)),
        dict(id="backup",  argv=["backup.py", "--completo"],
             descr="backup completo (operative + SQLite)", minuti=30,
             richiede=("DSN",)),
        # Prima del delta, non dopo: uno ZIP troncato in cache passa il test
        # 'esiste ed e' grande' e viene riusato all'infinito. E' costato
        # 47.038 CIG mancanti, scoperti solo per caso.
        dict(id="cache",   argv=["ingest.py", "--verifica-cache", "--riscarica"],
             descr="controllo integrita' degli ZIP in cache", minuti=20),
        dict(id="delta",   argv=["ingest.py", "--delta", "ultimi:1"],
             descr="delta mensile CIG da ANAC", minuti=90),
        dict(id="bulk",    argv=["ingest.py", "--bulk"],
             descr="aggiudicazioni, aggiudicatari, contratti", minuti=180,
             dipende="delta"),
        # Dopo il bulk e prima del push: l'esito si calcola sugli aggiudicatari
        # appena arrivati, e va su Supabase nello stesso giro.
        dict(id="esito",   argv=["esito.py", "--misura"],
             descr="che fine hanno fatto le scadenze (R17)", minuti=60,
             dipende="bulk"),
        # Ricalibrare ogni mese non e' zelo: i moltiplicatori vengono da
        # 'esito', che il passo prima ha appena riscritto. Lasciare i pesi
        # vecchi vorrebbe dire ordinare i lead di domani con la statistica
        # dell'anno scorso.
        dict(id="categorie", argv=["categorie.py", "--applica"],
             descr="classificazione funzionale dei contratti", minuti=20,
             dipende="bulk"),
        dict(id="punteggio", argv=["punteggio.py", "--calibra"],
             descr="ricalibrazione dei pesi del punteggio (R18)", minuti=45,
             dipende="esito"),
        dict(id="lead",    argv=["punteggio.py", "--applica"],
             descr="punteggio ai lead correnti", minuti=30,
             dipende="punteggio"),
        # Dopo 'bulk' (aggiudicatari/aggiudicazioni freschi), indipendente da
        # punteggio/lead: non serve il punteggio dei lead per sapere chi ha
        # gia' in mano un ente.
        dict(id="intelligence", argv=["intelligence.py", "--calcola"],
             descr="chi presidia quale ente (R36)", minuti=15,
             dipende="bulk"),
        dict(id="push",    argv=["push_supabase.py"],
             descr="pubblicazione su Supabase", minuti=30,
             dipende="lead", richiede=("DSN",)),
        # Indipendente dal resto del piano apposta: enti come Provincia di
        # Brescia non hanno un canale di richiesta diretta e vanno
        # ricontrollati a mano (R32). Non blocca ne' e' bloccato da nulla.
        dict(id="promemoria", argv=["promemoria_enti.py"],
             descr="promemoria enti da controllare a mano (R32)", minuti=2,
             richiede=("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")),
    ],
}


def conf(chiave):
    """Legge da .env.local o dall'ambiente. Mai obbligatoria: qui nessuna
    credenziale e' indispensabile, il job gira comunque."""
    v = os.environ.get(chiave)
    if v:
        return v.strip()
    envfile = os.path.join(QUI, ".env.local")
    if not os.path.exists(envfile):
        return None
    trovato = None
    with open(envfile, encoding="utf-8") as f:
        for riga in f:
            riga = riga.strip()
            if riga.startswith("#") or "=" not in riga:
                continue
            k, _, val = riga.partition("=")
            if k.strip() == chiave:
                trovato = val.strip().strip('"').strip("'")   # ultima vince
    return trovato or None


def segreti():
    """Tutto quello che non deve finire in un log, in chiaro.

    Anche i pezzi della DSN separatamente: un traceback di psycopg puo'
    stampare la password da sola, fuori dall'URL."""
    fuori = []
    dsn = leggi_dsn()                # riga nuda in .env.local, senza '='
    if dsn:
        fuori.append(dsn)
        m = re.match(r"[^:]+://([^:@/]+):([^@]+)@", dsn)
        if m:
            fuori.extend(m.groups())
    for k in ("PEC_PASSWORD", "TELEGRAM_BOT_TOKEN", "SMTP_PASSWORD",
              "RADAR_SUPABASE_DSN"):
        v = conf(k)
        if v:
            fuori.append(v)
    # Sotto i sei caratteri il rischio si ribalta: mascherare una stringa
    # cortissima sfregia il log senza proteggere niente.
    return [s for s in fuori if len(s) >= 6]


def presente(chiave):
    """C'e' la credenziale? 'DSN' e' un caso a se': in .env.local sta come
    riga nuda, non come CHIAVE=valore."""
    return bool(leggi_dsn()) if chiave == "DSN" else bool(conf(chiave))


def mancanti(step):
    return [k for k in step.get("richiede", ()) if not presente(k)]


def maschera(testo, chiavi):
    for s in chiavi:
        testo = testo.replace(s, "***")
    return testo


# --------------------------------------------------------------- logging
def apri_log(ritmo):
    os.makedirs(LOGDIR, exist_ok=True)
    p = os.path.join(LOGDIR, f"{ritmo}-{datetime.now():%Y-%m-%d}.log")
    return open(p, "a", encoding="utf-8"), p


def ruota_log():
    """Cancella i log piu' vecchi di GIORNI_LOG giorni.

    Sulla data nel nome, non sull'mtime: un file toccato per sbaglio non deve
    sopravvivere all'infinito."""
    if not os.path.isdir(LOGDIR):
        return 0
    taglio = (datetime.now() - timedelta(days=GIORNI_LOG)).date()
    tolti = 0
    for f in os.listdir(LOGDIR):
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})\.log$", f)
        if not m:
            continue
        try:
            d = datetime(*map(int, m.groups())).date()
        except ValueError:
            continue
        if d < taglio:
            os.remove(os.path.join(LOGDIR, f))
            tolti += 1
    return tolti


def avvisa(testo):
    """Telegram, se configurato. Non solleva mai: e' l'avviso di un guasto,
    non deve diventare un secondo guasto."""
    token, chat = conf("TELEGRAM_BOT_TOKEN"), conf("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    dati = urllib.parse.urlencode(
        {"chat_id": chat, "text": testo[:3900]}).encode()
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=dati)
        with urllib.request.urlopen(req, timeout=30):
            return True
    except Exception:
        return False


# ------------------------------------------------------------- esecuzione
def esegui(step, log, chiavi):
    """Lancia uno step. Ritorna (ok, riassunto)."""
    argv = [sys.executable, os.path.join(QUI, step["argv"][0])] + step["argv"][1:]
    t0 = time.time()
    try:
        r = subprocess.run(argv, cwd=QUI, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=step["minuti"] * 60)
        uscita, out = r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        uscita, out = -1, f"TIMEOUT dopo {step['minuti']} minuti"
    except Exception as e:
        uscita, out = -1, f"{type(e).__name__}: {e}"

    dur = time.time() - t0
    out = maschera(out, chiavi).strip()
    log.write(f"\n{'-' * 70}\n[{datetime.now():%H:%M:%S}] {step['id']} — "
              f"{step['descr']}\n$ {' '.join(step['argv'])}\n{'-' * 70}\n")
    log.write(out + "\n")
    log.write(f"[esito] uscita={uscita} durata={dur / 60:.1f} min\n")
    log.flush()

    if uscita == 0:
        return True, f"  ok    {step['id']:9s} {dur / 60:5.1f} min"
    # L'ultima riga con del testo e' quasi sempre il messaggio dell'errore.
    ultima = next((r for r in reversed(out.splitlines()) if r.strip()), "")
    return False, (f"  FALLITO {step['id']:9s} (uscita {uscita}) "
                   f"{ultima[:110]}")


def gira(ritmo, prova=False):
    piano = PIANI[ritmo]
    if prova:
        print(f"piano '{ritmo}' — {len(piano)} step, nessuno eseguito:\n")
        for s in piano:
            manca = mancanti(s)
            nota = f"  (salta se fallisce '{s['dipende']}')" if s.get("dipende") else ""
            if manca:
                nota = f"  NON CONFIGURATO: manca {', '.join(manca)}"
            print(f"  {s['id']:9s} {' '.join(s['argv']):45s} "
                  f"max {s['minuti']:3d} min{nota}")
        print(f"\nlog in {LOGDIR}, rotazione a {GIORNI_LOG} giorni")
        return 0

    chiavi = segreti()
    log, percorso = apri_log(ritmo)
    inizio = datetime.now()
    log.write(f"\n{'=' * 70}\n=== {ritmo} — {inizio:%d/%m/%Y %H:%M:%S} ===\n"
              f"{'=' * 70}\n")

    esiti, falliti, saltati = [], set(), 0
    for s in piano:
        # Credenziali assenti non sono un guasto: e' una funzione non ancora
        # accesa. Trattarla da errore vorrebbe dire un allarme al giorno, e
        # un allarme che suona sempre non lo legge piu' nessuno.
        manca = mancanti(s)
        if manca:
            log.write(f"\n[non configurato] {s['id']}: manca "
                      f"{', '.join(manca)} in .env.local\n")
            esiti.append(f"  --    {s['id']:9s} non configurato "
                         f"(manca {', '.join(manca)})")
            saltati += 1
            continue
        if s.get("dipende") in falliti:
            msg = (f"  saltato {s['id']:9s} perche' '{s['dipende']}' e' "
                   f"fallito")
            log.write(f"\n[saltato] {s['id']}: dipende da {s['dipende']}\n")
            esiti.append(msg)
            falliti.add(s["id"])          # propaga a chi dipende da questo
            continue
        ok, riga = esegui(s, log, chiavi)
        esiti.append(riga)
        if not ok:
            falliti.add(s["id"])

    tolti = ruota_log()
    durata = (datetime.now() - inizio).total_seconds() / 60
    coda = f", {saltati} non configurati" if saltati else ""
    testa = (f"radar {ritmo}: {len(piano) - len(falliti) - saltati}/"
             f"{len(piano) - saltati} ok in {durata:.0f} min{coda}")
    corpo = "\n".join(esiti)
    log.write(f"\n{'=' * 70}\n{testa}\n{corpo}\n"
              f"log ruotati: {tolti}\n{'=' * 70}\n")
    log.close()

    print(testa)
    print(corpo)
    print(f"\nlog: {percorso}")

    if falliti:
        avvisa(f"⚠️ {testa}\n\n{corpo}\n\nlog: {percorso}")
    return 1 if falliti else 0


# ------------------------------------------------ Utilita' di pianificazione
def schtasks(*args):
    r = subprocess.run(["schtasks", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def sblocca_batteria(nome_task):
    """Toglie 'non partire a batteria' e accende il recupero dei giri persi.

    schtasks /create non ha un flag per queste due opzioni: la task nasce
    con DisallowStartIfOnBatteries=true e senza StartWhenAvailable. Non e'
    cosmesi: e' la causa vera, trovata il 2026-09-24, di due giorni di
    catch-up falliti in silenzio (errore Windows 0x800710E0, 'operatore o
    amministratore ha rifiutato la richiesta') su un portatile che al
    mattino spesso non e' attaccato alla corrente.

    Il modulo PowerShell ScheduledTasks (Set-ScheduledTask) da' 'Parametro
    non corretto' sul trigger mensile — un limite noto del provider CIM con
    CalendarTrigger di tipo ScheduleByMonth, che schtasks.exe scrive senza
    problemi ma la cmdlet piu' recente non sa rileggere in scrittura. Si usa
    invece la stessa API COM (Schedule.Service) su cui schtasks.exe si
    appoggia: legge e riscrive qualunque tipo di trigger allo stesso modo."""
    ps = (
        '$svc = New-Object -ComObject "Schedule.Service"; $svc.Connect(); '
        f'$folder = $svc.GetFolder("\\{CARTELLA_TASK}"); '
        f'$def = $folder.GetTask("{nome_task}").Definition; '
        "$def.Settings.DisallowStartIfOnBatteries = $false; "
        "$def.Settings.StopIfGoingOnBatteries = $false; "
        "$def.Settings.StartWhenAvailable = $true; "
        # 6 = TASK_CREATE_OR_UPDATE, 3 = TASK_LOGON_INTERACTIVE_TOKEN — stessi
        # valori con cui la task e' gia' stata creata da schtasks /create.
        f'$folder.RegisterTaskDefinition("{nome_task}", $def, 6, $null, $null, 3) | Out-Null'
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0, (r.stdout or "") + (r.stderr or "")


def installa():
    """Crea le tre attivita'. /f sovrascrive: reinstallare e' idempotente.

    Non si usa /ru SYSTEM: il job legge .env.local dal profilo dell'utente e
    scrive nella cartella di backup dell'utente. Girerebbe come SYSTEM senza
    trovare niente."""
    if os.name != "nt":
        print("installazione automatica solo su Windows. Su Linux: cron.")
        return 1
    ko = 0
    for ritmo, t in TASKS.items():
        # Le virgolette servono: il percorso contiene 'Progetti n8n Claude'.
        cmd = f'"{sys.executable}" "{os.path.join(QUI, "job.py")}" --{ritmo}'
        rc, out = schtasks("/create", "/tn", t["nome"], "/tr", cmd,
                           *t["quando"], "/f")
        if rc != 0:
            ko += 1
            print(f"  FALLITA {t['nome']}: {out.strip()[:160]}")
            continue
        nome_semplice = t["nome"].split("\\")[-1]
        ok_batt, out_batt = sblocca_batteria(nome_semplice)
        extra = "" if ok_batt else f"  (batteria non sbloccata: {out_batt.strip()[:120]})"
        print(f"  creata  {t['nome']:28s} — {t['descr']}{extra}")
    if ko:
        print("\nSe dice 'accesso negato': serve un terminale come "
              "amministratore.")
        return 1
    print("\nLe attivita' girano solo a PC acceso. Se il PC e' spento "
          "all'orario\nprevisto, Windows le recupera al successivo avvio, "
          "anche a batteria\n(non solo con l'alimentatore attaccato).")
    print("\nControlla con:  python job.py --stato")
    return 0


def disinstalla():
    for t in TASKS.values():
        rc, out = schtasks("/delete", "/tn", t["nome"], "/f")
        print(f"  {'rimossa' if rc == 0 else 'assente'}  {t['nome']}")
    return 0


# schtasks parla la lingua di Windows, non l'inglese: su una macchina italiana
# le etichette sono "Prossima esecuzione", "Ultimo esito". Si cerca per pezzo di
# stringa invece che per chiave esatta, cosi' funziona in entrambe le lingue.
ETICHETTE = {
    "prossima": ("next run time", "prossima esecuzione"),
    "ultima":   ("last run time", "ultima esecuzione"),
    "esito":    ("last result", "ultimo esito", "ultimo risultato"),
    "stato":    ("status", "stato attivita"),
    # "modalit" e non "modalita": schtasks stampa nella codepage della
    # console, non in UTF-8, e la "a" accentata arriva corrotta.
    "accesso":  ("logon mode", "modalit"),
}

# 267011 = "l'attivita' non e' mai stata eseguita". Windows lo restituisce come
# se fosse un errore; non lo e'.
MAI_ESEGUITA = ("267011", "0x41303")


def _campi(out):
    d = {}
    for riga in out.splitlines():
        k, _, v = riga.partition(":")
        k = k.strip().lower().replace("�", "").replace("à", "a")
        if v.strip():
            d[k] = v.strip()
    return d


def _leggi(campi, quale):
    for pezzo in ETICHETTE[quale]:
        for k, v in campi.items():
            if pezzo in k:
                return v
    return "?"


def stato_dict():
    """Stessa lettura di stato(), come dati invece che stampa.

    La console (R9+) la usa per dire se un task e' partito davvero — non se
    lo script e' andato a buon fine (quello lo dice gia' il file di log), ma
    se Windows lo ha lanciato per niente. E' la distinzione che ha trovato
    il guasto vero del 16/09: il task 'console-locale' esisteva ma girava
    solo con l'utente collegato, e si e' fermato al logout."""
    righe = []
    for ritmo, t in TASKS.items():
        rc, out = schtasks("/query", "/tn", t["nome"], "/v", "/fo", "list")
        if rc != 0:
            righe.append(dict(ritmo=ritmo, nome=t["nome"], installata=False))
            continue
        campi = _campi(out)
        esito = _leggi(campi, "esito")
        if any(m in esito for m in MAI_ESEGUITA):
            esito = "mai eseguita"
        elif esito == "0":
            esito = "ok"
        ultima = _leggi(campi, "ultima")
        if ultima.startswith("30/11/1999") or ultima.startswith("11/30/1999"):
            ultima = "mai"
        accesso = _leggi(campi, "accesso").lower()
        solo_interattivo = (("interattiv" in accesso or "interactive" in accesso)
                             and "background" not in accesso)
        righe.append(dict(ritmo=ritmo, nome=t["nome"], installata=True,
                          prossima=_leggi(campi, "prossima"), ultima=ultima,
                          esito=esito, solo_interattivo=solo_interattivo))
    return righe


def stato():
    print("=== attivita' pianificate ===\n")
    for ritmo, t in TASKS.items():
        rc, out = schtasks("/query", "/tn", t["nome"], "/v", "/fo", "list")
        if rc != 0:
            print(f"  {t['nome']:28s} NON INSTALLATA")
            continue
        campi = _campi(out)
        esito = _leggi(campi, "esito")
        if any(m in esito for m in MAI_ESEGUITA):
            esito = "mai eseguita"
        elif esito == "0":
            esito = "ok"
        ultima = _leggi(campi, "ultima")
        if ultima.startswith("30/11/1999") or ultima.startswith("11/30/1999"):
            ultima = "mai"
        print(f"  {t['nome']:28s} prossima {_leggi(campi, 'prossima')}")
        print(f"  {'':28s} ultima   {ultima}  esito {esito}")
        # "Solo interattivo" = gira solo con l'utente collegato. Con
        # "Esegui indipendentemente..." schtasks scrive "Interattivo/Background":
        # contiene "interattivo" anche lui, e il controllo di prima dava
        # l'allarme proprio quando il problema era stato risolto.
        accesso = _leggi(campi, "accesso").lower()
        if (("interattiv" in accesso or "interactive" in accesso)
                and "background" not in accesso):
            print(f"  {'':28s} gira solo con l'utente collegato")
    print(f"\n=== ultimi log ({LOGDIR}) ===\n")
    if not os.path.isdir(LOGDIR):
        print("  nessuno: il job non e' mai partito")
        return 0
    for f in sorted(os.listdir(LOGDIR))[-6:]:
        p = os.path.join(LOGDIR, f)
        riassunto = "?"
        with open(p, encoding="utf-8", errors="replace") as fh:
            for riga in fh:
                if riga.startswith("radar "):
                    riassunto = riga.strip()
        print(f"  {f:34s} {os.path.getsize(p):>7,} B  {riassunto}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--giornaliero", action="store_true")
    ap.add_argument("--mensile", action="store_true")
    ap.add_argument("--feriale", action="store_true")
    ap.add_argument("--prova", action="store_true",
                    help="elenca gli step senza eseguirli")
    ap.add_argument("--installa", action="store_true")
    ap.add_argument("--disinstalla", action="store_true")
    ap.add_argument("--stato", action="store_true")
    a = ap.parse_args()

    if a.installa:
        sys.exit(installa())
    if a.disinstalla:
        sys.exit(disinstalla())
    if a.stato:
        sys.exit(stato())
    if a.giornaliero:
        sys.exit(gira("giornaliero", a.prova))
    if a.mensile:
        sys.exit(gira("mensile", a.prova))
    if a.feriale:
        sys.exit(gira("feriale", a.prova))
    ap.print_help()


if __name__ == "__main__":
    main()
