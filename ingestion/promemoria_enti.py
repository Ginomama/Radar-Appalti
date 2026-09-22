#!/usr/bin/env python3
"""
Promemoria Telegram per enti senza canale di richiesta diretta (R32).

Il problema che risolve: alcuni enti (es. Provincia di Brescia) rispondono
che non accettano richieste dirette di iscrizione, e le opportunita' si
trovano solo controllando a mano una pagina pubblica ("Manifestazioni di
interesse", bandi, ecc.). Costruire uno scraper per un caso simile non vale
la pena: la pagina di Provincia di Brescia e' dietro un WAF che blocca le
richieste HTTP automatiche (403 anche con user-agent realistico), quindi
uno script fallirebbe silenziosamente o richiederebbe manutenzione continua
per un solo ente. Un promemoria mensile costa zero manutenzione.

Elenco ENTI qui sotto: un dict per ogni ente da controllare a mano, con il
link diretto alla pagina giusta (non alla home). Aggiungere una voce quando
un altro ente risponde allo stesso modo.

Uso:
    python promemoria_enti.py --dry-run
    python promemoria_enti.py

Dipendenza: solo stdlib.
"""

import argparse
import os
import sys
import urllib.parse
import urllib.request

QUI = os.path.dirname(os.path.abspath(__file__))
ENVFILE = os.path.join(QUI, ".env.local")

ENTI = [
    dict(nome="Provincia di Brescia",
         url="https://at.provincia.brescia.it/pagina566_bandi-di-gara-e-contratti.html",
         nota="niente elenco fornitori: bisogna rispondere alle "
              "Manifestazioni di interesse pubblicate qui"),
]


def conf(chiave):
    v = os.environ.get(chiave)
    if v:
        return v.strip()
    if not os.path.exists(ENVFILE):
        return None
    trovato = None
    with open(ENVFILE, encoding="utf-8") as f:
        for riga in f:
            riga = riga.strip()
            if riga.startswith("#") or "=" not in riga:
                continue
            k, _, val = riga.partition("=")
            if k.strip() == chiave:
                trovato = val.strip().strip('"').strip("'")
    return trovato or None


def messaggio():
    righe = ["Promemoria controllo manuale bandi\n"]
    for e in ENTI:
        righe.append(f"- {e['nome']} — {e['nota']}\n  {e['url']}")
    return "\n".join(righe)


def invia_telegram(testo):
    token, chat = conf("TELEGRAM_BOT_TOKEN"), conf("TELEGRAM_CHAT_ID")
    if not (token and chat):
        print("TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID mancanti in .env.local")
        return False
    dati = urllib.parse.urlencode({"chat_id": chat, "text": testo}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=dati)
    with urllib.request.urlopen(req, timeout=30):
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    testo = messaggio()
    if a.dry_run:
        print(testo)
        return 0
    if not invia_telegram(testo):
        return 1
    print(f"inviato ({len(ENTI)} enti)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
