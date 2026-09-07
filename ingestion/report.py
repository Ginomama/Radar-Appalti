"""Report dimostrativo su un territorio (task R8).

A COSA SERVE. Raccontare a voce che "esiste un motore che trova i contratti IT
in scadenza" non convince nessuno. Venti righe vere sulla provincia di chi
ascolta, con nomi di enti che conosce e fornitori che ha gia' incontrato, si
verificano in due minuti su ANAC — ed e' quella verificabilita' a fare la
differenza fra una demo e una presentazione.

DUE SCELTE CHE SEMBRANO PRUDENZA E SONO COMMERCIALI.

**La PEC non c'e'.** Il report mostra il giudizio — quale contratto scade,
quanto vale, chi ce l'ha adesso, quanto e' probabile che quella porta si
apra — e non il recapito per bussare. Chi legge vede esattamente cosa
comprerebbe e non ha ancora niente da usare. Con --contatti si include, ed e'
la versione per uso interno.

**L'attribuzione e' su ogni pagina.** ANAC e' CC BY-SA 4.0 e IndicePA CC BY
4.0: l'attribuzione e' dovuta da entrambe, e costa una riga. La clausola
ShareAlike di ANAC su un prodotto commerciale resta aperta (docs/liceita.md
§2): questo report e' un estratto elaborato di venti righe, non la
ridistribuzione del dataset, ma finche' quella domanda non ha risposta vale
la pena saperlo prima di stamparne cinquanta copie.

Uso:
    python report.py --provincia PADOVA
    python report.py --provincia ROMA --limite 30 --contatti
    python report.py --provincia VENEZIA --csv

Dipendenze: psycopg, reportlab.
"""

import argparse
import csv
import io
import os
import sys
from datetime import date

import psycopg

from push_supabase import leggi_dsn, maschera

QUI = os.path.dirname(os.path.abspath(__file__))
FUORI = os.path.join(QUI, "..", "docs", "report")

RILEVANTI = ("Sviluppo software", "Dati e analytics", "Gestione documentale",
             "Manutenzione e assistenza", "Consulenza IT")

FONTE = ("Fonti: ANAC open data (CC BY-SA 4.0) · IndicePA (CC BY 4.0). "
         "Elaborazione FlowLine.")

MESI = ["", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
        "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]


def data_it(d):
    return f"{d.day} {MESI[d.month][:3]} {d.year}" if d else "—"


def eur(v):
    if not v:
        return "—"
    v = float(v)
    return f"{v/1e6:,.1f} M€" if v >= 1e6 else f"{v/1000:,.0f} k€"


def fascia(p):
    if p is None:
        return "—", (0.6, 0.6, 0.6)
    if p >= 60:
        return "eccezionale", (0.06, 0.46, 0.43)
    if p >= 35:
        return "ottimo", (0.08, 0.72, 0.65)
    if p >= 18:
        return "buono", (0.66, 0.45, 0.10)
    return "marginale", (0.55, 0.55, 0.55)


def raccogli(cur, provincia, limite):
    cur.execute("""
        SELECT s.cig, s.ente, s.categoria, s.data_termine_contrattuale,
               s.importo_aggiudicazione, s.fornitore_uscente,
               left(s.oggetto_lotto, 150), s.punteggio, s.pec, s.cf_ente,
               s.giorni_alla_scadenza
        FROM radar.v_scadenze s
        WHERE upper(s.provincia) = upper(%s)
          AND s.fornitore_persona_fisica = 0
          AND s.pec IS NOT NULL
          AND s.categoria = ANY(%s)
          AND s.giorni_alla_scadenza BETWEEN 0 AND 365
        ORDER BY s.punteggio DESC NULLS LAST,
                 s.importo_aggiudicazione DESC NULLS LAST
        LIMIT %s""", (provincia, list(RILEVANTI), limite))
    righe = [dict(zip(
        ("cig", "ente", "categoria", "scadenza", "importo", "uscente",
         "oggetto", "punti", "pec", "cf", "giorni"), r)) for r in cur.fetchall()]

    # Il contesto: senza, venti righe sono venti righe. Con, sono venti righe
    # scelte fra molte — che e' il prodotto.
    cur.execute("""
        SELECT count(*), sum(importo_aggiudicazione),
               count(DISTINCT cf_ente),
               count(*) FILTER (WHERE giorni_alla_scadenza <= 90)
        FROM radar.v_scadenze
        WHERE upper(provincia) = upper(%s)
          AND giorni_alla_scadenza BETWEEN 0 AND 365""", (provincia,))
    tot, val, enti, a90 = cur.fetchone()

    # E lo storico: quante volte, su questa provincia, una scadenza e' davvero
    # diventata un cambio di fornitore. E' il numero che rende il resto
    # credibile invece che ottimistico.
    cur.execute("""
        SELECT count(*), avg(coalesce(contendibile, 0))
        FROM radar.esito WHERE upper(provincia) = upper(%s)""", (provincia,))
    n_esiti, quota = cur.fetchone()
    return righe, dict(tot=tot or 0, valore=float(val or 0), enti=enti or 0,
                       a90=a90 or 0, esiti=n_esiti or 0,
                       apertura=float(quota or 0))


def pdf(provincia, righe, ctx, contatti, dove):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (KeepTogether, PageBreak, Paragraph,
                                    SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    VERDE = colors.HexColor("#1E6E58")
    GRIGIO = colors.HexColor("#5A6461")
    ss = getSampleStyleSheet()
    H1 = ParagraphStyle("H1", parent=ss["Title"], fontName="Helvetica-Bold",
                        fontSize=20, leading=24, textColor=VERDE,
                        alignment=TA_LEFT, spaceAfter=2)
    SOT = ParagraphStyle("SOT", parent=ss["Normal"], fontSize=10.5, leading=15,
                         textColor=GRIGIO, spaceAfter=14)
    H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                        fontSize=11, textColor=VERDE, spaceBefore=14,
                        spaceAfter=6)
    P = ParagraphStyle("P", parent=ss["Normal"], fontSize=9.5, leading=14)
    CELLA = ParagraphStyle("C", parent=ss["Normal"], fontSize=7.6, leading=9.6)
    PIC = ParagraphStyle("PIC", parent=ss["Normal"], fontSize=7.2, leading=9,
                         textColor=GRIGIO)

    def cornice(canv, doc):
        canv.saveState()
        canv.setFont("Helvetica", 7)
        canv.setFillColor(GRIGIO)
        canv.drawString(18 * mm, 12 * mm, FONTE)
        canv.drawRightString(A4[0] - 18 * mm, 12 * mm, f"pagina {doc.page}")
        canv.setStrokeColor(colors.HexColor("#DCE2DD"))
        canv.line(18 * mm, 16 * mm, A4[0] - 18 * mm, 16 * mm)
        canv.restoreState()

    os.makedirs(os.path.dirname(dove), exist_ok=True)
    doc = SimpleDocTemplate(
        dove, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=22 * mm,
        title=f"Radar Appalti — {provincia.title()}",
        author="FlowLine")

    st = []
    st.append(Paragraph(f"Contratti informatici in scadenza<br/>"
                        f"{provincia.title()}", H1))
    st.append(Paragraph(f"Estratto del {data_it(date.today())} · "
                        f"{len(righe)} contratti su {ctx['tot']:,} rilevati", SOT))

    q = ctx["apertura"] * 100
    st.append(Paragraph("Cosa dice il territorio", H2))
    st.append(Paragraph(
        f"Nei prossimi dodici mesi scadono <b>{ctx['tot']:,} contratti "
        f"informatici</b> in {ctx['enti']:,} enti della provincia, per "
        f"<b>{eur(ctx['valore'])}</b> complessivi. {ctx['a90']:,} scadono entro "
        f"novanta giorni.<br/><br/>"
        f"Sullo storico, {ctx['esiti']:,} contratti di questa provincia sono "
        f"gia' arrivati a scadenza e sono stati seguiti fino all'esito: nel "
        f"<b>{q:.1f}%</b> dei casi il fornitore e' cambiato. "
        + ("È sopra la media nazionale del 4,7%: un territorio dove si entra."
           if q > 5.5 else
           "È in linea con la media nazionale del 4,7%."
           if q > 3.5 else
           "È sotto la media nazionale del 4,7%: territorio presidiato, "
           "servono argomenti piu' forti del prezzo."), P))

    st.append(Paragraph("Le venti migliori occasioni", H2))
    st.append(Paragraph(
        "Ordinate per probabilita' che quella scadenza diventi davvero "
        "un'occasione — non per importo. Un contratto grande di un ente che "
        "rinnova sempre allo stesso fornitore non e' un'occasione, e in questa "
        "lista sta in fondo.", P))
    st.append(Spacer(1, 8))

    intest = ["Scade", "Ente", "Oggetto", "Importo", "Fornitore attuale", "Giudizio"]
    if contatti:
        intest.insert(2, "PEC")
    dati = [[Paragraph(f"<b>{c}</b>", CELLA) for c in intest]]
    stile = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFF2EE")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, colors.HexColor("#C3CCC5")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for i, r in enumerate(righe, start=1):
        et, col = fascia(r["punti"])
        cella = [
            Paragraph(data_it(r["scadenza"]) + f"<br/><font size=6.4>fra "
                      f"{r['giorni']} gg</font>", CELLA),
            Paragraph((r["ente"] or "")[:58], CELLA),
            Paragraph((r["oggetto"] or "")[:110], CELLA),
            Paragraph(eur(r["importo"]), CELLA),
            Paragraph((r["uscente"] or "—")[:38], CELLA),
            Paragraph(f"<b>{r['punti'] if r['punti'] is not None else '—'}</b>"
                      f"<br/><font size=6.4>{et}</font>", CELLA),
        ]
        if contatti:
            cella.insert(2, Paragraph((r["pec"] or "—")[:34], CELLA))
        dati.append(cella)
        stile.append(("LINEBELOW", (0, i), (-1, i), 0.3,
                      colors.HexColor("#E7EBE8")))
        stile.append(("TEXTCOLOR", (-1, i), (-1, i), colors.Color(*col)))

    larghezze = ([16, 22, 30, 12, 32, 15, 14] if contatti
                 else [17, 26, 35, 13, 22, 14])
    tot = sum(larghezze)
    disp = A4[0] - 36 * mm
    tab = Table(dati, colWidths=[w / tot * disp for w in larghezze],
                repeatRows=1)
    tab.setStyle(TableStyle(stile))
    st.append(tab)

    st.append(Spacer(1, 14))
    st.append(Paragraph("Come si legge il giudizio", H2))
    st.append(Paragraph(
        "Il numero non e' un'opinione: viene da <b>30.301 contratti gia' "
        "scaduti</b> di cui e' stato seguito l'esito. Il tasso base nazionale "
        "di cambio fornitore e' il <b>4,72%</b>, e ogni fattore — storia "
        "dell'ente, categoria, fascia d'importo, dimensione del fornitore "
        "uscente — e' un moltiplicatore misurato su quel tasso.<br/><br/>"
        "<b>60+</b> eccezionale · <b>35-59</b> ottimo · <b>18-34</b> buono · "
        "<b>sotto 18</b> marginale. Che la maggior parte dei contratti finisca "
        "sotto 18 e' il risultato corretto, non un difetto: serve a non "
        "sprecare il tempo di chi vende.", P))

    if not contatti:
        st.append(Spacer(1, 10))
        st.append(Paragraph(
            "<i>I recapiti PEC degli enti non sono inclusi in questo "
            "estratto.</i>", PIC))

    st.append(Spacer(1, 10))
    st.append(Paragraph(
        "Dati elaborati dagli open data ANAC (licenza CC BY-SA 4.0) e "
        "dall'Indice dei domicili digitali della pubblica amministrazione "
        "IndicePA (licenza CC BY 4.0). Le scadenze sono desunte dalle "
        "comunicazioni di avvio contratto: dove la durata non e' pubblicata, "
        "il contratto non compare.", PIC))

    doc.build(st, onFirstPage=cornice, onLaterPages=cornice)
    return dove


def scrivi_csv(provincia, righe, contatti, dove):
    col = ["punti", "scadenza", "giorni", "ente", "categoria", "oggetto",
           "importo", "uscente", "cig"]
    if contatti:
        col.insert(4, "pec")
    os.makedirs(os.path.dirname(dove), exist_ok=True)
    with io.open(dove, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(col)
        for r in righe:
            w.writerow([r.get(c) for c in col])
    return dove


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--provincia", required=True)
    ap.add_argument("--limite", type=int, default=20)
    ap.add_argument("--contatti", action="store_true",
                    help="includi le PEC (versione per uso interno)")
    ap.add_argument("--csv", action="store_true",
                    help="anche un CSV accanto al PDF")
    a = ap.parse_args()

    dsn = leggi_dsn()
    if not dsn:
        sys.exit("manca la stringa di connessione (ingestion/.env.local)")
    try:
        with psycopg.connect(dsn, connect_timeout=30) as pg, pg.cursor() as cur:
            righe, ctx = raccogli(cur, a.provincia, a.limite)
    except Exception as e:
        sys.exit("errore: " + maschera(str(e), dsn)[:300])

    if not righe:
        sys.exit(f"nessuna scadenza servibile in provincia di {a.provincia}. "
                 f"Prova con --provincia scritta come in ANAC (per esteso, "
                 f"es. 'PESARO E URBINO').")

    base = a.provincia.lower().replace(" ", "-")
    suff = "-interno" if a.contatti else ""
    p = pdf(a.provincia, righe, ctx, a.contatti,
            os.path.join(FUORI, f"radar-{base}{suff}.pdf"))
    print(f"  PDF  {os.path.relpath(p, QUI)}  ({os.path.getsize(p)/1024:.0f} KB)")
    if a.csv:
        c = scrivi_csv(a.provincia, righe, a.contatti,
                       os.path.join(FUORI, f"radar-{base}{suff}.csv"))
        print(f"  CSV  {os.path.relpath(c, QUI)}")
    print(f"\n  {len(righe)} righe su {ctx['tot']:,} scadenze rilevate, "
          f"{eur(ctx['valore'])} complessivi in {ctx['enti']:,} enti")
    if not a.contatti:
        print("  Le PEC NON sono nel file: --contatti per la versione interna.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
