"""Presentazione in PDF da allegare alla PEC (task R21).

QUANDO SI POTEVA FARE, E PERCHE' NON PRIMA. R21 era bloccato da una condizione
scritta nella roadmap: farlo **solo dopo** aver misurato il tasso di risposta
senza allegato, se no non si sarebbe mai saputo se serviva. Adesso la misura
c'e' — 18 PEC inviate, 18 consegnate, **0 risposte** — quindi la linea di
base e' zero e qualunque miglioramento e' attribuibile.

COSA C'E' DENTRO, E COSA NON C'E'. Il contenuto viene dalle stesse fonti del
testo della PEC (MITTENTE e COSA_FACCIAMO di genera_pec.py): un solo posto da
aggiornare, e allegato e messaggio non possono contraddirsi.

Quello che NON c'e' sono referenze, casi studio e numeri di clienti. Non e'
una dimenticanza: sono cose che non si inventano. Le sezioni ci sono, marcate
con [DA COMPILARE], e finche' restano cosi' lo script si rifiuta di allegare
il file a una PEC vera — un allegato con dei segnaposto dentro fa piu' danno
di nessun allegato.

Uso:
    python presentazione.py                # genera il PDF
    python presentazione.py --controlla    # dice se e' pronto per l'invio

Dipendenze: reportlab.
"""

import argparse
import os
import re
import sys

QUI = os.path.dirname(os.path.abspath(__file__))
FUORI = os.path.join(QUI, "..", "docs", "allegati")
NOME = "flowline-presentazione.pdf"

SEGNAPOSTO = re.compile(r"\[DA COMPILARE[^\]]*\]")

# Le referenze. Vuoto di proposito: vanno scritte da chi le ha, non generate.
# Ogni voce e' (ente o settore, cosa e' stato fatto, risultato misurabile).
REFERENZE = [
    # ("Comune di ...", "automazione del protocollo", "3 ore/settimana"),
]

# Le domande che un RUP fa davvero, nell'ordine in cui le fa. Sono la parte
# piu' utile del documento: rispondono a obiezioni, non elencano competenze.
DOMANDE = [
    ("Chi resta proprietario di quello che fate?",
     "L'ente. Codice, configurazioni e documentazione sono consegnati e "
     "restano suoi: puo' farli mantenere da noi, da un altro fornitore o "
     "internamente. Non vendiamo licenze ne' abbonamenti, e non c'e' nessun "
     "componente che smette di funzionare se il rapporto finisce."),
    ("Che succede se ci lasciate a meta'?",
     "La documentazione di consegna e' parte della fornitura, non un extra: "
     "descrive come e' fatto, dove gira e come si modifica. E' il motivo per "
     "cui non c'e' un vincolo tecnico a restare con noi."),
    ("Come si affida un lavoro cosi'?",
     "Sotto le soglie di legge con affidamento diretto previa indagine di "
     "mercato; sopra, partecipando alle procedure. La richiesta di iscrizione "
     "all'elenco degli operatori economici serve esattamente a essere fra "
     "quelli consultati quando l'esigenza si presenta."),
    ("Quanto costa scoprire se serve?",
     "Niente. La prima analisi — quali passaggi manuali si possono togliere e "
     "quante ore recupererebbe l'ufficio — la facciamo prima di qualunque "
     "preventivo e non e' vincolante per nessuno."),
]


def dati_mittente():
    """Presi da genera_pec: un posto solo da aggiornare."""
    sys.path.insert(0, QUI)
    import genera_pec
    return genera_pec.MITTENTE, genera_pec.COSA_FACCIAMO, genera_pec.GENERICO


def costruisci(dove):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)

    mitt, cosa, generico = dati_mittente()
    VERDE = colors.HexColor("#1E6E58")
    GRIGIO = colors.HexColor("#5A6461")
    ss = getSampleStyleSheet()
    H1 = ParagraphStyle("H1", parent=ss["Title"], fontName="Helvetica-Bold",
                        fontSize=22, leading=26, textColor=VERDE,
                        alignment=TA_LEFT, spaceAfter=2)
    SOT = ParagraphStyle("SOT", parent=ss["Normal"], fontSize=11, leading=16,
                         textColor=GRIGIO, spaceAfter=16)
    H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                        fontSize=11.5, textColor=VERDE, spaceBefore=15,
                        spaceAfter=5)
    D = ParagraphStyle("D", parent=ss["Normal"], fontName="Helvetica-Bold",
                       fontSize=10, leading=14, spaceBefore=9, spaceAfter=2)
    P = ParagraphStyle("P", parent=ss["Normal"], fontSize=10, leading=15)
    PIC = ParagraphStyle("PIC", parent=ss["Normal"], fontSize=7.6, leading=10,
                         textColor=GRIGIO)

    def cornice(canv, doc):
        canv.saveState()
        canv.setFont("Helvetica", 7.4)
        canv.setFillColor(GRIGIO)
        canv.drawString(20 * mm, 12 * mm,
                        f"{mitt['nome']}"
                        + (f" — {mitt['insegna']}" if mitt.get("insegna") else "")
                        + f" · P.IVA {mitt['piva']} · {mitt['email']}")
        canv.drawRightString(A4[0] - 20 * mm, 12 * mm, f"{doc.page}")
        canv.setStrokeColor(colors.HexColor("#DCE2DD"))
        canv.line(20 * mm, 16 * mm, A4[0] - 20 * mm, 16 * mm)
        canv.restoreState()

    os.makedirs(os.path.dirname(dove), exist_ok=True)
    doc = SimpleDocTemplate(
        dove, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=20 * mm, bottomMargin=22 * mm,
        title=f"{mitt.get('insegna') or mitt['nome']} — presentazione",
        author=mitt["nome"])

    st = []
    st.append(Paragraph(mitt.get("insegna") or mitt["nome"], H1))
    st.append(Paragraph(
        "Automazione dei processi e integrazione di sistemi informativi "
        "per la pubblica amministrazione", SOT))

    st.append(Paragraph("In una riga", H2))
    st.append(Paragraph(
        "Togliamo i passaggi manuali ripetitivi fra i sistemi che l'ente "
        "gia' usa — gestionali, protocollo, banche dati, posta — senza "
        "sostituirli e senza aggiungere licenze da rinnovare.", P))

    st.append(Paragraph("Cosa facciamo, in concreto", H2))
    for categoria, testo in cosa.items():
        st.append(Paragraph(categoria, D))
        st.append(Paragraph(testo.replace("\n", " "), P))

    st.append(Paragraph("Le domande che ci fanno sempre", H2))
    for dom, ris in DOMANDE:
        st.append(KeepTogether([Paragraph(dom, D), Paragraph(ris, P)]))

    st.append(Paragraph("Referenze", H2))
    if REFERENZE:
        dati = [["Ente", "Intervento", "Risultato"]] + [list(r) for r in REFERENZE]
        tab = Table(dati, colWidths=[45 * mm, 65 * mm, 40 * mm])
        tab.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFF2EE")),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#DCE2DD")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        st.append(tab)
    else:
        st.append(Paragraph(
            "[DA COMPILARE: due o tre referenze, con ente, intervento e "
            "risultato misurabile. Vanno scritte da chi le ha — inventarle "
            "e' l'unico modo sicuro di perdere l'unica occasione che si ha "
            "con un ente pubblico.]", P))

    st.append(Paragraph("Contatti", H2))
    st.append(Paragraph(
        f"<b>{mitt['nome']}</b><br/>"
        + (f"{mitt['insegna']}<br/>" if mitt.get("insegna") else "")
        + f"P.IVA {mitt['piva']}<br/>"
        f"tel. {mitt['telefono']}<br/>"
        f"{mitt['email']}", P))

    st.append(Spacer(1, 12))
    st.append(Paragraph(
        "Documento allegato a una richiesta di iscrizione all'elenco degli "
        "operatori economici. I recapiti dell'ente sono stati reperiti "
        "dall'Indice dei domicili digitali della pubblica amministrazione "
        "(IndicePA, licenza CC BY 4.0); i riferimenti ai contratti in "
        "scadenza, dove citati, provengono dagli open data ANAC "
        "(licenza CC BY-SA 4.0).", PIC))

    doc.build(st, onFirstPage=cornice, onLaterPages=cornice)
    return dove


def controlla(percorso):
    """Pronto per essere allegato a una PEC vera?"""
    problemi = []
    if not os.path.exists(percorso):
        return [f"il file non esiste: genera con `python presentazione.py`"]
    # Nota: cercare "DA COMPILARE" nei byte del PDF non funziona — il testo e'
    # compresso e non si trova mai. Il controllo vero e' sulla sorgente: il
    # segnaposto viene inserito se e solo se REFERENZE e' vuoto.
    if not REFERENZE:
        problemi.append(
            "REFERENZE e' vuoto, quindi il PDF contiene un [DA COMPILARE]. "
            "Un allegato con dei buchi dentro fa piu' danno di nessun "
            "allegato. Due o tre righe vere bastano; non vanno inventate.")
    if os.path.getsize(percorso) > 2_000_000:
        problemi.append("supera i 2 MB: molte caselle PEC hanno un tetto "
                        "sull'allegato.")
    return problemi


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--controlla", action="store_true")
    a = ap.parse_args()
    dove = os.path.abspath(os.path.join(FUORI, NOME))

    if not a.controlla:
        costruisci(dove)
        print(f"  {os.path.relpath(dove, QUI)}  "
              f"({os.path.getsize(dove)/1024:.0f} KB)")

    problemi = controlla(dove)
    if problemi:
        print("\n  NON allegabile a una PEC vera:")
        for p in problemi:
            print(f"    - {p}")
        print("\n  pec_smtp.py rifiuta di allegarlo finche' resta cosi'.\n")
        return 1
    print("\n  Pronto: pec_smtp.py lo allega se PEC_ALLEGATO=1 in .env.local\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
