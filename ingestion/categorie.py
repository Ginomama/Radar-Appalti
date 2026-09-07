#!/usr/bin/env python3
"""
Categorizzazione funzionale dei contratti (task R2).

A cosa serve: "servizi informatici" non e' un mercato. Un fornitore di
cybersecurity e uno che fa manutenzione gestionale non vogliono gli stessi lead.

Strategia a tre livelli, dal piu' economico al piu' caro:

  1. CPV   -- il codice e' gerarchico e gia' presente su ogni riga: costo zero.
  2. regex -- sull'oggetto, solo per cio' che il CPV lascia generico.
  3. LLM   -- solo sul residuo, e solo sulle scadenze (~11k righe), mai sui
              236k CIG. Vedi la skill cost-aware-llm-pipeline.

Ogni livello viene misurato: senza numeri non si sa se il livello dopo serve.

Uso:
    python categorie.py --misura     # copertura per livello, non scrive
    python categorie.py --applica    # popola la tabella categoria

Solo libreria standard.
"""

import argparse
import os
import re
import sqlite3

QUI = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(QUI, "radar.db")

# ---------------------------------------------------------------------------
# Livello 1 — CPV. Prefissi dal piu' specifico al piu' generico: vince il piu'
# lungo. I codici sono quelli reali visti nei dati, non l'intero vocabolario.
# ---------------------------------------------------------------------------
CPV = {
    # sicurezza
    "48730": "Cybersecurity",
    "48731": "Cybersecurity",
    "72212731": "Cybersecurity",

    # sviluppo su misura
    "72230": "Sviluppo software",
    "72231": "Sviluppo software",
    "72232": "Sviluppo software",
    "72262": "Sviluppo software",
    "72263": "Sviluppo software",
    "72265": "Sviluppo software",
    "72240": "Sviluppo software",
    "72243": "Sviluppo software",
    "72244": "Sviluppo software",
    "72211": "Sviluppo software",
    "72212": "Sviluppo software",
    "72213": "Sviluppo software",
    "7223":  "Sviluppo software",

    # manutenzione, assistenza, helpdesk
    "72267": "Manutenzione e assistenza",
    "72261": "Manutenzione e assistenza",
    "72253": "Manutenzione e assistenza",
    "72251": "Manutenzione e assistenza",
    "72252": "Manutenzione e assistenza",
    "72254": "Manutenzione e assistenza",
    "72611": "Manutenzione e assistenza",
    "72610": "Manutenzione e assistenza",
    "7225":  "Manutenzione e assistenza",

    # licenze e pacchetti pronti
    "72268": "Licenze e pacchetti",
    "48218": "Licenze e pacchetti",
    "48517": "Licenze e pacchetti",
    "48771": "Licenze e pacchetti",
    "48772": "Licenze e pacchetti",
    "4844":  "Licenze e pacchetti",
    "4831":  "Licenze e pacchetti",
    "4832":  "Licenze e pacchetti",
    "4833":  "Licenze e pacchetti",
    "4841":  "Licenze e pacchetti",
    "4842":  "Licenze e pacchetti",
    "4845":  "Licenze e pacchetti",
    "4846":  "Licenze e pacchetti",
    "4847":  "Licenze e pacchetti",
    "4848":  "Licenze e pacchetti",
    "4849":  "Licenze e pacchetti",
    "4810":  "Licenze e pacchetti",
    "4862":  "Licenze e pacchetti",

    # infrastruttura, cloud, datacenter
    "72514": "Cloud e datacenter",
    "72540": "Cloud e datacenter",
    "48820": "Cloud e datacenter",
    "48821": "Cloud e datacenter",
    "48822": "Cloud e datacenter",
    "48810": "Cloud e datacenter",

    # connettivita' e reti
    "72700": "Connettivita e reti",
    "7271":  "Connettivita e reti",
    "7272":  "Connettivita e reti",
    "72410": "Connettivita e reti",
    "72411": "Connettivita e reti",
    "72412": "Connettivita e reti",
    "72413": "Connettivita e reti",
    "72414": "Connettivita e reti",
    "72415": "Connettivita e reti",
    "72416": "Connettivita e reti",
    "72417": "Connettivita e reti",
    "72420": "Connettivita e reti",
    "72421": "Connettivita e reti",
    "72422": "Connettivita e reti",
    "4820":  "Connettivita e reti",
    "4821":  "Connettivita e reti",

    # dati
    "72320": "Dati e analytics",
    "72322": "Dati e analytics",
    "72310": "Dati e analytics",
    "72311": "Dati e analytics",
    "72312": "Dati e analytics",
    "72313": "Dati e analytics",
    "72314": "Dati e analytics",
    "72316": "Dati e analytics",
    "72317": "Dati e analytics",
    "72319": "Dati e analytics",
    "48610": "Dati e analytics",
    "48611": "Dati e analytics",

    # documentale
    "72512": "Gestione documentale",
    "48311": "Gestione documentale",
    "48312": "Gestione documentale",
    "48313": "Gestione documentale",
    "48314": "Gestione documentale",

    # consulenza
    "72220": "Consulenza IT",
    "72221": "Consulenza IT",
    "72222": "Consulenza IT",
    "72224": "Consulenza IT",
    "72225": "Consulenza IT",
    "72226": "Consulenza IT",
    "72227": "Consulenza IT",
    "72228": "Consulenza IT",
    "72266": "Consulenza IT",
    "72100": "Consulenza IT",
    "7280":  "Consulenza IT",

    # verticali di settore
    "48180": "Software verticale",
    "48190": "Software verticale",
    "48151": "Software verticale",
    "48814": "Software verticale",
    "48920": "Software verticale",
    "48921": "Software verticale",
    "48960": "Software verticale",
    "4812":  "Software verticale",
    "4813":  "Software verticale",
    "4814":  "Software verticale",
    "4816":  "Software verticale",
    "4817":  "Software verticale",
    "4819":  "Software verticale",

    # lacune trovate misurando il residuo (R2.4, 2026-08-26)
    "4861":  "Dati e analytics",
    "48612": "Dati e analytics",
    "4877":  "Licenze e pacchetti",
    "48773": "Licenze e pacchetti",
    "4881":  "Cloud e datacenter",
}

# Codici che NON classificano: sono contenitori generici. Vanno al livello 2.
CPV_GENERICI = {
    "72000000", "72200000", "72500000", "72510000", "72260000", "72300000",
    "72400000", "72600000", "48000000", "48900000", "48600000", "48800000",
    "48500000", "48700000", "48400000", "48300000", "48200000", "48100000",
    "72", "48",
}

# ---------------------------------------------------------------------------
# Livello 2 — regex sull'oggetto. Ordine = priorita': la prima che matcha vince.
# Le espressioni nascono dal testo reale, non da ipotesi.
# ---------------------------------------------------------------------------
# ATTENZIONE alla forma delle espressioni. La prima stesura usava
#     \b(manutenzion|svilupp|licenz|...)\b
# e il \b finale le rendeva morte: pretende un confine di parola dopo la
# radice, ma in "manutenzione" segue una 'e'. Nessuna riga con radici
# tronche veniva classificata. Qui il confine c'e' solo a sinistra, e le
# radici sono seguite da \w* dove serve.
REGEX = [
    # PRIMA di tutto il resto. Un abbonamento a UpToDate o a Leggi d'Italia
    # e' spesa editoriale, non analytics: la regola "banche dati" lo pescava
    # dentro Dati e analytics, e un fornitore che filtra per quella categoria
    # si ritrovava abbonamenti da biblioteca. Trovato generando le PEC vere.
    ("Abbonamenti editoriali",
     r"\b(?:abbonament\w*|sottoscrizione|acquisto)"
     r"(?:(?!licenz).){0,70}"
     r"\b(?:banc[ah] dati|banche dati|rivist\w*|periodic\w*"
     r"|biblio\w*|e-?book|editorial\w*|medialibrary)"),
    ("Cybersecurity", r"\b(?:cyber\w*|sicurezza informatica|firewall|antivirus"
                      r"|endpoint|penetration test|vulnerabilit\w*|\bsoc\b|siem"
                      r"|disaster recovery|business continuity|backup)"),
    ("Connettivita e reti", r"\b(?:connettivit\w*|banda larga|fibr\w* ottic\w*|mpls"
                            r"|vpn|rete geografica|\blan\b|\bwan\b|wi-?fi"
                            r"|telefoni\w*|voip|centralino|cablaggio"
                            r"|network management)"),
    ("Cloud e datacenter", r"\b(?:cloud|iaas|paas|saas|data ?cent\w*|server"
                           r"|virtualizzazion\w*|vmware|storage|hosting|housing"
                           r"|infrastruttur\w+ informatic\w+)"),
    ("Gestione documentale", r"\b(?:protocollo informatico|documental\w*"
                             r"|conservazione (?:sostitutiva|digitale)"
                             r"|fatturazione elettronica|firma digitale"
                             r"|gestione document\w*)"),
    ("Sviluppo software", r"\b(?:svilupp\w*|realizzazione (?:di )?(?:un[ao]? )?"
                          r"(?:portale|sito|applicativ\w*|software|sistema)"
                          r"|applicazione mobile|portale web|sito web|restyling"
                          r"|implementazione)"),
    ("Manutenzione e assistenza", r"\b(?:manutenzion\w*|assistenz\w*|supporto"
                                  r"|help ?desk|presidio|aggiornamento"
                                  r"|efficientamento)"),
    ("Licenze e pacchetti", r"\b(?:licenz\w*|abbonament\w*|rinnovo|canon[ei]"
                            r"|microsoft|adobe|oracle|\bsap\b|office 365"
                            r"|suite software)"),
    ("Dati e analytics", r"\b(?:banc[ah] dati|banche dati|business intelligence"
                         r"|analytics|cruscott\w*|datawarehouse|open data"
                         r"|base dati)"),
    ("Consulenza IT", r"\b(?:consulenz\w*|project management|formazione"
                      r"|assistenza tecnica specialistica)"),
    # ultimo scalino: se parla esplicitamente di software o sistema informativo
    # senza altri indizi, e' comunque meglio di "non classificato"
    ("Software verticale", r"\b(?:software|applicativ\w*|sistem\w* informati\w*"
                           r"|gestional\w*|piattaform\w*|ticketing|gestione (?:global\w*|del sistema))"),
]
REGEX = [(cat, re.compile(rx, re.I)) for cat, rx in REGEX]


def per_cpv(cpv):
    """Prefisso piu' lungo che matcha. None se il codice e' generico."""
    if not cpv:
        return None
    if cpv in CPV_GENERICI:
        return None
    for lung in range(8, 1, -1):
        cat = CPV.get(cpv[:lung])
        if cat:
            return cat
    return None


def per_regex(oggetto):
    if not oggetto:
        return None
    for cat, rx in REGEX:
        if rx.search(oggetto):
            return cat
    return None


# Categorie che devono essere riconosciute PRIMA del CPV, perche' il codice
# dice una cosa e l'oggetto ne dice un'altra: un abbonamento a UpToDate ha
# CPV 72320 "servizi di banche dati", ma non e' un servizio di analytics.
OVERRIDE = {"Abbonamenti editoriali"}


def classifica(cpv, oggetto):
    """(categoria, metodo). metodo = override | cpv | regex | None."""
    for cat, rx in REGEX:
        if cat in OVERRIDE and oggetto and rx.search(oggetto):
            return cat, "override"
    c = per_cpv(cpv)
    if c:
        return c, "cpv"
    c = per_regex(oggetto)
    if c:
        return c, "regex"
    return None, None


def _righe(cx):
    return cx.execute(
        "SELECT cig, cpv_norm, oggetto_lotto FROM v_scadenze_contattabili")


def misura():
    """R2.4 — quanto copre ogni livello. Non scrive niente."""
    cx = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    tot = solo_cpv = solo_regex = niente = 0
    per_cat = {}
    non_classificati = []
    for cig, cpv, ogg in _righe(cx):
        tot += 1
        cat, metodo = classifica(cpv, ogg)
        if metodo == "cpv":
            solo_cpv += 1
        elif metodo == "regex":
            solo_regex += 1
        else:
            niente += 1
            if len(non_classificati) < 12:
                non_classificati.append((cpv, (ogg or "")[:64]))
        if cat:
            per_cat[cat] = per_cat.get(cat, 0) + 1

    print(f"scadenze da classificare: {tot:,}\n")
    print(f"  livello 1  CPV     {solo_cpv:6,}  {solo_cpv/tot*100:5.1f}%")
    print(f"  livello 2  regex   {solo_regex:6,}  {solo_regex/tot*100:5.1f}%")
    print(f"  residuo    LLM     {niente:6,}  {niente/tot*100:5.1f}%")
    print(f"  {'-'*38}")
    print(f"  copertura senza LLM {tot-niente:6,}  {(tot-niente)/tot*100:5.1f}%")

    print("\n=== distribuzione per categoria ===")
    for cat, n in sorted(per_cat.items(), key=lambda x: -x[1]):
        print(f"  {cat:28s} {n:6,}  {n/tot*100:5.1f}%")

    if non_classificati:
        print("\n=== esempi non classificati (materiale per le regex) ===")
        for cpv, ogg in non_classificati:
            print(f"  {cpv:10s} {ogg}")
    cx.close()


def applica():
    cx = sqlite3.connect(DB)
    cx.executescript("""
        CREATE TABLE IF NOT EXISTS categoria (
            cig           TEXT PRIMARY KEY,
            categoria     TEXT,
            metodo        TEXT,
            aggiornato_il TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS ix_cat ON categoria (categoria);
    """)
    batch = []
    for cig, cpv, ogg in _righe(cx).fetchall():
        cat, metodo = classifica(cpv, ogg)
        batch.append((cig, cat, metodo))
    cx.executemany(
        "INSERT INTO categoria (cig, categoria, metodo) VALUES (?,?,?) "
        "ON CONFLICT (cig) DO UPDATE SET categoria=excluded.categoria, "
        "metodo=excluded.metodo, aggiornato_il=CURRENT_TIMESTAMP", batch)
    cx.commit()
    n = cx.execute("SELECT count(*) FROM categoria").fetchone()[0]
    c = cx.execute(
        "SELECT count(*) FROM categoria WHERE categoria IS NOT NULL").fetchone()[0]
    print(f"categoria: {n:,} righe, {c:,} classificate ({c/n*100:.1f}%)")
    cx.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--misura", action="store_true")
    ap.add_argument("--applica", action="store_true")
    a = ap.parse_args()
    if a.misura:
        misura()
    elif a.applica:
        applica()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
