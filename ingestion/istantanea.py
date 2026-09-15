#!/usr/bin/env python3
"""
L'istantanea della console da condividere: il link per i colleghi.

La console vera e' console_live.py: dati in diretta, ma solo su questa
macchina e solo su 127.0.0.1, perche' mostra PEC e recapiti. Chi non e'
qui vede la versione pubblicata su claude.ai, che non puo' interrogare il
database (la policy dei contenuti pubblicati blocca gli host esterni): per
lei i dati vanno incorporati nella pagina, e invecchiano.

Questo script fa l'istantanea senza bisogno che la console sia accesa:
stessa query (console_live.raccogli), stessa pagina (docs/console.html),
payload incorporato al posto di quello vuoto. Lo lancia ogni mattina
un'attivita' pianificata, che poi ripubblica sullo stesso link.

Il link condiviso e' da CONSULTARE. Gli stati degli invii si cambiano dalla
console locale o da invii.py, che scrivono nel database: quello che si segna
sulla pagina pubblicata resta nella pagina, e la ripubblicazione del mattino
dopo lo sovrascrive.

Uso:
    python istantanea.py                       # -> docs/condivisa/console.html
    python istantanea.py --fuori C:\\percorso\\console.html
"""

import argparse
import json
import os
import re
import sys

import console_live
from push_supabase import leggi_dsn, maschera

QUI = os.path.dirname(os.path.abspath(__file__))
FUORI = os.path.join(QUI, "..", "docs", "condivisa", "console.html")
BLOCCO = re.compile(
    r'(<script id="dati" type="application/json">)(.*?)(</script>)', re.S)


def componi_pagina(html, dati):
    """La pagina con i dati dentro.

    '</' diventa '<\\/': un oggetto di gara che contenesse '</script>'
    chiuderebbe il blocco dei dati a meta' e la pagina uscirebbe bianca.
    Per JSON.parse le due forme sono identiche.
    """
    js = json.dumps(dati, ensure_ascii=False, separators=(",", ":"))
    js = js.replace("</", "<\\/")
    nuova, n = BLOCCO.subn(lambda m: m.group(1) + js + m.group(3), html, count=1)
    if n != 1:
        raise ValueError('docs/console.html non ha il blocco <script id="dati">: '
                         "la pagina e' cambiata, va aggiornato istantanea.py")
    return nuova


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--fuori", default=FUORI,
                    help="dove scrivere la pagina (default docs/condivisa/)")
    a = ap.parse_args()

    dsn = leggi_dsn()
    if not dsn:
        sys.exit("connessione non configurata: vedi ingestion/.env.local")
    try:
        d = console_live.dati(dsn)
    except Exception as e:
        # Il caso tipico e' Supabase in pausa dopo 7 giorni senza attivita':
        # il pooler risponde "tenant/user ... not found".
        sys.exit("database non raggiungibile: " + maschera(str(e), dsn)[:300])

    with open(console_live.PAGINA, encoding="utf-8") as f:
        html = componi_pagina(f.read(), d)
    fuori = os.path.abspath(a.fuori)
    os.makedirs(os.path.dirname(fuori), exist_ok=True)
    with open(fuori, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  {fuori}")
    print(f"  dati del {d['generato']}: {len(d['lead'])} lead, "
          f"{len(d['invii'])} invii, {len(html) // 1024} KB")


if __name__ == "__main__":
    main()
