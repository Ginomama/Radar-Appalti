#!/usr/bin/env python3
"""
Invio automatico delle PEC in coda, nei giorni feriali (R31).

Il problema che risolve: le bozze si accumulano (genera_pec.py, il bottone
console, auto_genera.py ogni notte) ma partire era sempre un click umano —
comodo a basso volume, un collo di bottiglia quando la coda cresce (169
righe 'da_inviare' su 19 lotti al 21/09).

Non toglie il controllo umano, lo sposta prima dell'invio invece che ad
ogni riga. Alle 8:00 (Lun-Ven) manda su Telegram l'elenco di cosa sta per
partire, aspetta FINESTRA minuti, e invia solo se non arriva "STOP" in
risposta. Una PEC protocollata non si ritira (R7b) — la finestra serve a
fermare TUTTO il giro di oggi con un messaggio, non a rivedere riga per
riga come prima.

La selezione e' FIFO su radar.invio: le righe 'da_inviare' piu' vecchie,
qualunque lotto. Non serve un punteggio qui — chi aspetta da piu' tempo
(generato a mano, dal bottone console, o da auto_genera.py stanotte) parte
per primo, ed e' lo stesso ordine con cui i lotti vengono generati (R27).

Ogni riga passa comunque da pec_smtp.spedisci(): stessi controlli di
sempre — segnaposto, destinatario coerente, doppio invio, tetto
giornaliero (R7b, R20). Questo script sceglie CHI mandare quando, non
COME: la logica di invio resta un solo posto.

Uso:
    python invio_automatico.py                  # tetto 15, finestra 25 min
    python invio_automatico.py --tetto 10 --finestra 10
    python invio_automatico.py --prova           # mostra la coda, non invia niente
    python invio_automatico.py --salta-finestra  # per test: non aspetta i 25 min

Dipendenza: psycopg.
"""

import argparse
import json
import os
import time
import urllib.request
from datetime import date, datetime

import psycopg

import pec_smtp
from notifica import conf, invia_telegram, maschera
from push_supabase import leggi_dsn

QUI = os.path.dirname(os.path.abspath(__file__))
# Stesso posto dei log di job.py e dello stato di auto_genera.py: la
# console puo' leggerlo per dire se il giro di stamattina e' partito.
STATO = os.path.join(QUI, "logs", "invio-automatico-stato.json")

# Sotto e' insistere per fermare qualcosa che magari non serve fermare,
# sopra vuol dire spedire le PEC troppo tardi in mattinata.
FINESTRA_MINUTI = 25
TETTO_DEFAULT = 15


def coda(cur, tetto):
    """Le righe 'da_inviare' piu' vecchie, qualunque lotto."""
    cur.execute("""
        SELECT lotto, progressivo, ente, pec, provincia
        FROM radar.invio
        WHERE stato = 'da_inviare'
        ORDER BY creata_il ASC, lotto, progressivo
        LIMIT %s""", (tetto,))
    return cur.fetchall()


def messaggio_preavviso(righe, minuti):
    testa = (f"*Invio automatico PEC* — {len(righe)} in coda, partono fra "
             f"{minuti} minuti.\nRispondi *STOP* qui per fermare tutto "
             f"l'invio di oggi.\n")
    corpo = "\n".join(
        f"{n}. {(ente or '?')[:44]} ({prov or '?'}) — {lotto}"
        for lotto, n, ente, pec, prov in righe)
    return testa + "\n" + corpo


def stop_ricevuto(dopo_ts):
    """C'e' un 'STOP' arrivato al bot dopo il preavviso?

    Niente offset persistente da tracciare fra un giro e l'altro: il filtro
    sulla data del messaggio basta, ed e' un bot a uso singolo (non un bot
    con piu' utenti da tenere distinti).
    """
    token, chat = conf("TELEGRAM_BOT_TOKEN"), conf("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/getUpdates?limit=100")
        with urllib.request.urlopen(req, timeout=30) as r:
            dati = json.loads(r.read())
    except Exception:
        # Telegram irraggiungibile non deve bloccare l'invio all'infinito:
        # si procede come se nessuno STOP fosse arrivato.
        return False
    for u in dati.get("result", []):
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != str(chat):
            continue
        if msg.get("date", 0) <= dopo_ts:
            continue
        if (msg.get("text") or "").strip().upper() == "STOP":
            return True
    return False


def scrivi_stato(inviate, fermato, motivo=None):
    os.makedirs(os.path.dirname(STATO), exist_ok=True)
    corpo = {
        "eseguito_il": datetime.now().isoformat(timespec="seconds"),
        "inviate": len(inviate),
        "fermato": fermato,
        "motivo": motivo,
    }
    with open(STATO, "w", encoding="utf-8") as f:
        json.dump(corpo, f, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tetto", type=int, default=TETTO_DEFAULT,
                    help="quante PEC al massimo in questo giro")
    ap.add_argument("--finestra", type=int, default=FINESTRA_MINUTI,
                    help="minuti di attesa prima di inviare davvero")
    ap.add_argument("--prova", action="store_true",
                    help="mostra la coda e il preavviso, non invia niente "
                         "e non tocca Telegram")
    ap.add_argument("--salta-finestra", action="store_true",
                    help="non aspetta i minuti di finestra (solo per test)")
    ap.add_argument("--forza-weekend", action="store_true",
                    help="ignora il controllo giorno feriale (solo per test)")
    a = ap.parse_args()

    if not a.forza_weekend and date.today().weekday() >= 5:
        print("weekend: nessun invio automatico (gira solo Lun-Ven).")
        return

    dsn = leggi_dsn()
    if not dsn:
        raise SystemExit("connessione non configurata: vedi ingestion/.env.local")

    try:
        with psycopg.connect(dsn, connect_timeout=30) as pg, pg.cursor() as cur:
            righe = coda(cur, a.tetto)
    except Exception as e:
        raise SystemExit(maschera(f"{type(e).__name__}: {e}", dsn))

    if not righe:
        print("nessuna PEC in coda ('da_inviare'): niente da fare.")
        scrivi_stato([], fermato=False)
        return

    preavviso = messaggio_preavviso(righe, a.finestra)
    print(preavviso)

    if a.prova:
        print("\n[--prova] non invio niente, non aspetto e non avviso Telegram.")
        return

    invia_telegram(preavviso)
    dopo_ts = int(time.time())

    if not a.salta_finestra:
        time.sleep(a.finestra * 60)

    if stop_ricevuto(dopo_ts):
        print("STOP ricevuto: invio di oggi annullato, nessuna PEC partita.")
        invia_telegram("Invio automatico di oggi annullato (STOP ricevuto). "
                       "Le PEC restano in coda per domani.")
        scrivi_stato([], fermato=True, motivo="STOP ricevuto su Telegram")
        return

    inviate, fallite = [], []
    try:
        with psycopg.connect(dsn, connect_timeout=30) as pg, pg.cursor() as cur:
            for lotto, n, ente, pec, prov in righe:
                e = pec_smtp.spedisci(cur, lotto, n)
                if e.get("inviata"):
                    pg.commit()
                    inviate.append(e)
                else:
                    pg.rollback()
                    fallite.append((lotto, n, ente, e["problemi"]))
                    # Il tetto giornaliero, una volta raggiunto, blocca
                    # anche tutte le righe dopo: non serve riprovarle una
                    # per una, sarebbe solo rumore nel log.
                    if any("tetto giornaliero" in p for p in e["problemi"]):
                        break
    except Exception as e:
        raise SystemExit(maschera(f"{type(e).__name__}: {e}", dsn))

    scrivi_stato(inviate, fermato=False)

    print(f"\n{len(inviate)} inviate, {len(fallite)} non partite.")
    for lotto, n, ente, problemi in fallite:
        print(f"  {lotto}#{n} {(ente or '?')[:40]}: {problemi[0]}")

    riassunto = (f"*Invio automatico PEC* completato: {len(inviate)} inviate"
                + (f", {len(fallite)} non partite" if fallite else "") + ".")
    invia_telegram(riassunto)


if __name__ == "__main__":
    main()
