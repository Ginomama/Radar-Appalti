#!/usr/bin/env python3
"""
Notifiche del Radar Scadenze (task R4).

Due canali, due ritmi diversi:

  --telegram   giornaliero, compatto. Manda SOLO i lead nuovi, cioe' quelli
               entrati nella finestra dei 90 giorni dall'ultima esecuzione.
               Rimandare ogni giorno gli stessi 873 lead li renderebbe rumore
               e in una settimana il bot verrebbe ignorato.
  --email      settimanale, digest completo raggruppato per categoria.

Filtro fisso su fornitore_persona_fisica = 0: sono ditte individuali e
professionisti, quindi persone fisiche. Escluderli e' la mitigazione decisa
in docs/liceita.md (R3).

Lo stato di cosa e' gia' stato notificato sta su Supabase in radar.notificato,
non in locale: cosi' sopravvive a un cambio di macchina e resta ispezionabile.

Credenziali: SOLO da ingestion/.env.local (git-ignored) o variabili d'ambiente.
Mai nel codice, mai in un file versionato.

Uso:
    python notifica.py --telegram --dry-run    # mostra il messaggio, non invia
    python notifica.py --telegram
    python notifica.py --email --dry-run
    python notifica.py --email

Dipendenza: psycopg (gia' richiesta dal push). Telegram e SMTP sono stdlib.
"""

import argparse
import json
import os
import smtplib
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr

QUI = os.path.dirname(os.path.abspath(__file__))
ENVFILE = os.path.join(QUI, ".env.local")

# Attribuzione dovuta dalle licenze delle fonti (CC BY-SA 4.0 e CC BY 4.0).
# Costa una riga e la richiedono entrambe: vedi docs/liceita.md.
ATTRIBUZIONE = ("Fonti: ANAC open data (CC BY-SA 4.0), IndicePA (CC BY 4.0). "
                "Elaborazione FlowLine.")


_duplicati_visti = set()


def conf(chiave, obbligatoria=True):
    """Legge da variabile d'ambiente, poi da .env.local. Mai dal codice.

    Se la chiave compare piu' volte nel file vince l'ULTIMA, come in ogni
    altro formato .env e come fa la shell con export. Chi incolla in fondo una
    credenziale nuova si aspetta che sostituisca quella sopra: con la regola
    opposta il programma continua a usare la vecchia e restituisce un errore
    di autenticazione che non si riesce a spiegare. (Successo davvero, il 4
    settembre 2026, con la password PEC.)

    La chiave ripetuta resta comunque un errore da correggere, quindi si
    segnala su stderr — una volta sola per chiave, per non fare rumore.
    """
    v = os.environ.get(chiave)
    if not v and os.path.exists(ENVFILE):
        trovate = []
        for riga in open(ENVFILE, encoding="utf-8-sig"):
            riga = riga.strip()
            if riga.startswith(chiave + "="):
                trovate.append(
                    riga.split("=", 1)[1].strip().strip('"').strip("'"))
        if len(trovate) > 1 and chiave not in _duplicati_visti:
            _duplicati_visti.add(chiave)
            print(f"[avviso] {chiave} e' definita {len(trovate)} volte in "
                  f"ingestion/.env.local: vale l'ultima. Togli le altre.",
                  file=sys.stderr)
        if trovate:
            v = trovate[-1]
    if not v and obbligatoria:
        sys.exit(f"{chiave} non configurata in ingestion/.env.local")
    return v


def maschera(testo, *segreti):
    for s in segreti:
        if s and len(s) > 4:
            testo = testo.replace(s, "<SEGRETO>")
    return testo


# --------------------------------------------------------------- dati
def connetti():
    import psycopg
    # Riusa il lettore del push: accetta sia "RADAR_SUPABASE_DSN=..." sia il
    # DSN nudo su una riga, sia la variabile d'ambiente.
    from push_supabase import leggi_dsn
    dsn = leggi_dsn()
    if not dsn:
        sys.exit("connessione non configurata: vedi ingestion/.env.local")
    return psycopg.connect(dsn, connect_timeout=30)


def crea_stato(pg):
    with pg.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS radar.notificato (
                cig        text NOT NULL,
                canale     text NOT NULL,
                inviato_il timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (cig, canale)
            )""")
    pg.commit()


FILTRO = "fornitore_persona_fisica = 0"


def lead_nuovi(pg, canale, limite=15):
    """Lead mai notificati su questo canale, i piu' urgenti prima."""
    with pg.cursor() as cur:
        cur.execute(f"""
            SELECT l.cig, l.giorni_alla_scadenza, l.ente, l.provincia,
                   l.categoria, l.oggetto_lotto, l.importo_aggiudicazione,
                   l.fornitore_uscente, l.pec
            FROM radar.v_lead_90gg l
            LEFT JOIN radar.notificato n
                   ON n.cig = l.cig AND n.canale = %s
            WHERE n.cig IS NULL AND l.{FILTRO}
            ORDER BY l.importo_aggiudicazione DESC NULLS LAST
            LIMIT %s""", (canale, limite))
        return cur.fetchall()


def segna(pg, cigs, canale):
    with pg.cursor() as cur:
        cur.executemany(
            "INSERT INTO radar.notificato (cig, canale) VALUES (%s, %s) "
            "ON CONFLICT (cig, canale) DO NOTHING",
            [(c, canale) for c in cigs])
    pg.commit()


def digest(pg):
    """Tutto cio' che scade entro 90 giorni, per il report settimanale."""
    with pg.cursor() as cur:
        cur.execute(f"""
            SELECT coalesce(categoria, 'Non classificato'), count(*),
                   sum(importo_aggiudicazione)
            FROM radar.v_lead_90gg WHERE {FILTRO}
            GROUP BY 1 ORDER BY 3 DESC NULLS LAST""")
        per_cat = cur.fetchall()
        cur.execute(f"""
            SELECT cig, giorni_alla_scadenza, ente, provincia, categoria,
                   oggetto_lotto, importo_aggiudicazione, fornitore_uscente, pec
            FROM radar.v_lead_90gg WHERE {FILTRO}
            ORDER BY importo_aggiudicazione DESC NULLS LAST LIMIT 25""")
        top = cur.fetchall()
        cur.execute(f"SELECT count(*) FROM radar.v_lead_90gg WHERE {FILTRO}")
        tot = cur.fetchone()[0]
    return per_cat, top, tot


def eur(v):
    return f"{v:,.0f}".replace(",", ".") if v else "n.d."


# ----------------------------------------------------------- telegram
def testo_telegram(righe):
    if not righe:
        return None
    out = [f"*Radar Appalti* — {len(righe)} nuove scadenze\n"]
    for cig, gg, ente, prov, cat, ogg, imp, forn, pec in righe:
        urgenza = "🔴" if gg <= 30 else ("🟠" if gg <= 60 else "🟡")
        out.append(
            f"{urgenza} *{gg}gg* · {(cat or 'n.c.')}\n"
            f"{(ente or '?')[:52]} ({prov or '?'})\n"
            f"€ {eur(imp)} · uscente: {(forn or 'n.d.')[:38]}\n"
            f"`{(ogg or '')[:88]}`\n"
            f"PEC: {pec}\n")
    out.append(f"_{ATTRIBUZIONE}_")
    return "\n".join(out)


def invia_telegram(testo):
    token = conf("TELEGRAM_BOT_TOKEN")
    chat = conf("TELEGRAM_CHAT_ID")
    dati = urllib.parse.urlencode({
        "chat_id": chat, "text": testo, "parse_mode": "Markdown",
        "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=dati)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception as e:
        sys.exit(maschera(f"Telegram: {e}", token, chat))


# -------------------------------------------------------------- email
def html_digest(per_cat, top, tot):
    oggi = datetime.now().strftime("%d/%m/%Y")
    righe_cat = "".join(
        f"<tr><td>{c}</td><td align=right>{n}</td>"
        f"<td align=right>€ {eur(v)}</td></tr>" for c, n, v in per_cat)
    righe_top = "".join(
        f"<tr><td align=right><b>{gg}</b></td><td>{(ente or '?')[:46]}</td>"
        f"<td>{prov or ''}</td><td>{cat or 'n.c.'}</td>"
        f"<td align=right>€ {eur(imp)}</td><td>{(forn or '')[:30]}</td>"
        f"<td><a href='mailto:{pec}'>{pec}</a></td></tr>"
        for cig, gg, ente, prov, cat, ogg, imp, forn, pec in top)
    return f"""<html><body style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;
      color:#1a1a1a;max-width:900px">
<h2 style="margin-bottom:4px">Radar Appalti — digest del {oggi}</h2>
<p style="color:#555;margin-top:0">{tot} contratti IT in scadenza entro 90 giorni
presso enti pubblici. Esclusi i fornitori persona fisica.</p>

<h3>Per categoria</h3>
<table cellpadding=6 style="border-collapse:collapse;font-size:14px">
<tr style="background:#f2f2f2"><th align=left>Categoria</th><th>Lead</th>
<th align=right>Valore</th></tr>{righe_cat}</table>

<h3>Le 25 per valore</h3>
<table cellpadding=5 style="border-collapse:collapse;font-size:13px">
<tr style="background:#f2f2f2"><th>gg</th><th align=left>Ente</th><th>Prov</th>
<th align=left>Categoria</th><th align=right>Importo</th>
<th align=left>Fornitore uscente</th><th align=left>PEC</th></tr>{righe_top}</table>

<p style="color:#777;font-size:12px;margin-top:26px">{ATTRIBUZIONE}</p>
</body></html>"""


def invia_email(html, oggetto):
    host = conf("SMTP_HOST")
    porta = int(conf("SMTP_PORT", False) or 587)
    utente = conf("SMTP_USER")
    pwd = conf("SMTP_PASSWORD")
    a = conf("EMAIL_A")
    msg = EmailMessage()
    msg["Subject"] = oggetto
    msg["From"] = formataddr(("Radar Appalti", utente))
    msg["To"] = a
    msg.set_content("Questo messaggio richiede un client HTML.")
    msg.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP(host, porta, timeout=45) as s:
            s.starttls()
            s.login(utente, pwd)
            s.send_message(msg)
        return True
    except Exception as e:
        sys.exit(maschera(f"SMTP: {e}", pwd, utente))


# --------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--email", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="mostra cosa invierebbe, senza inviare nulla")
    ap.add_argument("--limite", type=int, default=15)
    a = ap.parse_args()
    if not (a.telegram or a.email):
        ap.print_help()
        return

    with connetti() as pg:
        crea_stato(pg)

        if a.telegram:
            righe = lead_nuovi(pg, "telegram", a.limite)
            testo = testo_telegram(righe)
            if not testo:
                print("telegram: nessun lead nuovo, niente da inviare")
            elif a.dry_run:
                print("--- messaggio Telegram (non inviato) ---")
                print(testo)
            else:
                if invia_telegram(testo):
                    segna(pg, [r[0] for r in righe], "telegram")
                    print(f"telegram: inviati {len(righe)} lead")
                else:
                    sys.exit("telegram: invio rifiutato dall'API")

        if a.email:
            per_cat, top, tot = digest(pg)
            html = html_digest(per_cat, top, tot)
            oggetto = f"Radar Appalti — {tot} scadenze IT entro 90 giorni"
            if a.dry_run:
                print(f"--- email (non inviata) --- oggetto: {oggetto}")
                print(f"    {tot} lead, {len(per_cat)} categorie, "
                      f"{len(top)} righe in tabella, {len(html):,} byte HTML")
            else:
                invia_email(html, oggetto)
                print(f"email: digest inviato ({tot} lead)")


if __name__ == "__main__":
    main()
