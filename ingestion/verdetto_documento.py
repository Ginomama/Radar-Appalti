"""Verifica approfondita sul documento vero del bando — R43.

SECONDO STADIO del parere AI. `verdetto_regionale.py` (primo stadio,
economico: solo titolo/descrizione/importo) scarta gia' la maggior parte dei
bandi come 'no' — verificato dal vivo: su ~400 bandi Emilia-Romagna/Toscana,
solo una manciata resta 'si' o 'forse'. E' su QUELLI, non su tutti, che ha
senso il passo in piu': scaricare il documento vero (un capitolato, una
lettera d'invito, un avviso) e farlo leggere al modello — molto piu'
affidabile del solo titolo, ma anche piu' caro e lento, quindi va usato solo
dove il primo filtro ha gia' ridotto il numero a poche decine.

Un solo PDF per bando, non tutti gli allegati: modulistica da compilare
(DGUE, dichiarazioni, tracciabilita' flussi, scheda offerta, privacy...) non
aggiunge contesto, solo tempo e costo. Si sceglie per parola chiave sulla
descrizione/nome file (capitolato, avviso, lettera d'invito, disciplinare,
bando, relazione, progetto), altrimenti il primo PDF rimasto dopo
l'esclusione della modulistica. Solo PDF: e' l'unico formato che l'API di
Claude legge nativamente (blocco "document" nei messages), senza bisogno di
una libreria di conversione in piu' nel progetto.

Uso:
    python verdetto_documento.py --fonte intercenter --dry-run
    python verdetto_documento.py --fonte intercenter
    python verdetto_documento.py --fonte start_toscana

Dipendenza: solo urllib — stessa scelta del resto dell'ingestion.
"""

import argparse
import base64
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verdetto_regionale import CHI_SIAMO  # noqa: E402 — un solo "chi siamo"

QUI = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(QUI, "radar.db")
API = "https://api.anthropic.com/v1/messages"
MODELLO = "claude-haiku-4-5-20251001"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

# Byte, non pagine: la stima e' grezza ma basta a scartare i pochi allegati
# anomali (verificato dal vivo: la "Documentazione protocollata" di un
# bando era un unico PDF da 3,5 MB con tutto lo storico dentro).
TETTO_BYTE = 15 * 1024 * 1024

FONTI = {
    "intercenter": dict(tabella="intercenter_avviso", piattaforma="intercenter"),
    "start_toscana": dict(tabella="start_avviso", piattaforma="start"),
}

ESCLUDI = re.compile(
    r"dgue|dichiarazion|tracciabilit|offerta economica|informativa|privacy|"
    r"ccnl|patto.{0,3}integrit|iscrizione|modello|scheda.{0,3}offerta|"
    r"equivalenza", re.I)
PRIORITA = ["capitolato", "avviso", "lettera d'invito", "lettera di invito",
            "disciplinare", "bando", "relazione", "progetto", "preventivo"]


def estensione(allegato):
    e = (allegato.get("ext") or "").lower()
    if e:
        return e
    nome = allegato.get("nome") or ""
    return nome.rsplit(".", 1)[-1].lower() if "." in nome else ""


def scegli_documento(allegati):
    candidati = [a for a in allegati if estensione(a) == "pdf"
                 and not ESCLUDI.search(f"{a.get('descrizione') or ''} {a.get('nome') or ''}")]
    if not candidati:
        return None
    for kw in PRIORITA:
        for a in candidati:
            if kw in f"{a.get('descrizione') or ''} {a.get('nome') or ''}".lower():
                return a
    return candidati[0]


def scarica_intercenter(allegato):
    url = urllib.parse.quote(allegato["url"], safe=":/?&=%*")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def scarica_start(codice, tipo_api, allegato):
    import start_toscana as st
    opener = st.sessione()
    url1 = (f"{st.BASE}/tendering-api/tenders/{tipo_api}/{codice}/"
            f"attachments/{allegato['id']}/template")
    req1 = urllib.request.Request(url1, headers={"User-Agent": st.UA,
                                                  "Accept": "application/json"})
    with opener.open(req1, timeout=30) as r:
        redirect = json.loads(r.read())["redirectUrl"]
    req2 = urllib.request.Request(st.BASE + redirect, headers={"User-Agent": st.UA})
    with opener.open(req2, timeout=60) as r:
        return r.read()


PROMPT = """{chi_siamo}

Sotto trovi il documento vero di un bando (avviso, capitolato o lettera
d'invito) — leggilo e valuta se vale la pena rispondere, con un parere
netto. Il documento e' l'informazione principale: usa titolo/importo solo
come contesto, non al posto della lettura.

Ente: {ente}
Titolo: {titolo}
Valore stimato: {valore}

Rispondi ESATTAMENTE in questo formato, due righe, niente altro prima o dopo:
VERDETTO: si|forse|no
MOTIVO: <una frase, massimo 25 parole, in italiano, basata sul contenuto del documento>
"""


def euro(v):
    return f"{v:,.0f} EUR".replace(",", ".") if v else "non indicato"


def valuta_documento(bando, pdf_bytes, chiave):
    prompt = PROMPT.format(chi_siamo=CHI_SIAMO, ente=bando["ente"] or "non indicato",
                            titolo=bando["titolo"] or "non indicato",
                            valore=euro(bando["importo"]))
    dati = json.dumps({
        "model": MODELLO,
        "max_tokens": 200,
        "messages": [{"role": "user", "content": [
            {"type": "document", "source": {
                "type": "base64", "media_type": "application/pdf",
                "data": base64.b64encode(pdf_bytes).decode()}},
            {"type": "text", "text": prompt},
        ]}],
    }).encode()
    req = urllib.request.Request(API, data=dati, headers={
        "x-api-key": chiave,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        risposta = json.loads(r.read())
    testo = risposta["content"][0]["text"]
    m_v = re.search(r"VERDETTO:\s*(si|sì|forse|no)", testo, re.I)
    m_m = re.search(r"MOTIVO:\s*(.+)", testo, re.I)
    if not m_v:
        raise ValueError(f"risposta senza VERDETTO riconoscibile: {testo[:200]!r}")
    verdetto = m_v.group(1).lower().replace("sì", "si")
    motivo = m_m.group(1).strip()[:250] if m_m else None
    return verdetto, motivo


def allinea_schema(cx, tabella):
    for stmt in (f"ALTER TABLE {tabella} ADD COLUMN allegati TEXT",
                 f"ALTER TABLE {tabella} ADD COLUMN verificato_doc_il TEXT",
                 f"ALTER TABLE {tabella} ADD COLUMN tipo_api TEXT"):
        try:
            cx.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e):
                raise
    cx.commit()


def main():
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--fonte", required=True, choices=sorted(FONTI))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tetto", type=int, default=40,
                     help="massimo bandi verificati in un giro (default 40)")
    a = ap.parse_args()
    cfg = FONTI[a.fonte]
    tabella = cfg["tabella"]

    if not os.path.exists(DB):
        sys.exit(f"radar.db non trovato: lancia prima {a.fonte}.py --ingest")

    cx = sqlite3.connect(DB)
    cx.row_factory = sqlite3.Row
    allinea_schema(cx, tabella)

    righe = cx.execute(
        f"SELECT * FROM {tabella} WHERE verdetto IN ('si', 'forse') "
        f"AND scadenza >= date('now') AND verificato_doc_il IS NULL "
        f"AND allegati IS NOT NULL ORDER BY scadenza LIMIT ?", (a.tetto,)).fetchall()

    if a.dry_run:
        if not righe:
            print("nessun bando 'si'/'forse' in attesa di verifica documento")
        else:
            print(f"{len(righe)} bandi verrebbero verificati sul documento:\n")
            for r in righe:
                allegati = json.loads(r["allegati"])
                scelto = scegli_documento(allegati)
                print(f"  {r['codice']:12s} {(r['ente'] or '')[:35]:37s} "
                      f"doc: {scelto['nome'] if scelto else 'NESSUN PDF UTILE'}")
        cx.close()
        return 0

    if not righe:
        print("nessun bando 'si'/'forse' in attesa di verifica documento")
        cx.close()
        return 0

    from notifica import conf
    chiave = conf("ANTHROPIC_API_KEY")
    if not chiave:
        sys.exit("ANTHROPIC_API_KEY mancante in .env.local")

    ok, senza_doc, falliti = 0, 0, 0
    for r in righe:
        allegati = json.loads(r["allegati"])
        scelto = scegli_documento(allegati)
        if not scelto:
            cx.execute(f"UPDATE {tabella} SET verificato_doc_il=CURRENT_TIMESTAMP "
                       f"WHERE codice=?", (r["codice"],))
            cx.commit()
            senza_doc += 1
            continue
        try:
            if cfg["piattaforma"] == "intercenter":
                pdf = scarica_intercenter(scelto)
            else:
                pdf = scarica_start(r["codice"], r["tipo_api"], scelto)
            if len(pdf) > TETTO_BYTE:
                raise ValueError(f"documento troppo grande ({len(pdf)/1e6:.1f} MB)")
            verdetto, motivo = valuta_documento(r, pdf, chiave)
        except (urllib.error.URLError, ValueError, KeyError, TimeoutError,
                json.JSONDecodeError) as e:
            print(f"  fallito {r['codice']}: {e}")
            falliti += 1
            continue
        cambiato = " (cambiato)" if verdetto != r["verdetto"] else ""
        cx.execute(f"UPDATE {tabella} SET verdetto=?, verdetto_motivo=?, "
                   f"verificato_doc_il=CURRENT_TIMESTAMP WHERE codice=?",
                   (verdetto, motivo, r["codice"]))
        cx.commit()
        print(f"  {r['codice']:12s} {r['verdetto']:>5s} -> {verdetto:<5s}{cambiato}  "
              f"{(r['titolo'] or '')[:50]}")
        ok += 1

    print(f"\nverificati {ok} documenti, {senza_doc} senza PDF utile, "
          f"{falliti} falliti")
    cx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
