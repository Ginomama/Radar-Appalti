#!/usr/bin/env python3
"""
IndicePA — contatti degli enti (task R1 della roadmap).

Serve a rendere azionabili le scadenze: senza un recapito, una scadenza e'
una riga, non un lead. La chiave di join e' il codice fiscale dell'ente.

IndicePA espone una CKAN come ANAC (verificato 2026-08-26):
    https://indicepa.gov.it/ipa-dati/api/3/action/package_list
Il dataset "amministrazioni" e' un TXT separato da TAB, UTF-8 con BOM.

Uso:
    python indicepa.py --gate       # R1.1: misura il tasso di match, non scrive
    python indicepa.py --ingest     # carica ente_contatti in radar.db

Solo libreria standard.
"""

import argparse
import csv
import io
import json
import os
import re
import sqlite3
import sys
from urllib.request import Request

import anac_http as h

QUI = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(QUI, "radar.db")
CACHE = os.path.join(QUI, "recon_out", "ipa")
API = "https://indicepa.gov.it/ipa-dati/api/3/action"

csv.field_size_limit(10 ** 7)


def risorsa_amministrazioni():
    """URL del TXT. Chiediamo al catalogo invece di costruire l'URL a mano."""
    req = Request(f"{API}/package_show?id=amministrazioni",
                  headers={"User-Agent": h.UA, "Accept": "application/json"})
    with h.apri(req, timeout=60) as r:
        d = json.loads(r.read())["result"]
    for x in d.get("resources", []):
        if (x.get("format") or "").upper() == "TXT":
            return x["url"]
    raise SystemExit("nessuna risorsa TXT nel dataset amministrazioni")


def scarica():
    os.makedirs(CACHE, exist_ok=True)
    dest = os.path.join(CACHE, "amministrazioni.txt")
    if os.path.exists(dest) and os.path.getsize(dest) > 100_000:
        return dest
    url = risorsa_amministrazioni()
    print("scarico amministrazioni.txt ...", end=" ", flush=True)
    with h.apri(Request(url, headers={"User-Agent": h.UA}), timeout=300) as r, \
            open(dest, "wb") as f:
        tot = 0
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
            tot += len(b)
    print(f"{tot/1e6:.1f} MB")
    return dest


def norm_cf(v):
    """
    Normalizza un codice fiscale per il confronto.

    I CF numerici degli enti sono 11 cifre, ma passando per fogli di calcolo
    o export perdono gli zeri iniziali: '00123456789' diventa '123456789'.
    Si azzera la differenza ripadando a 11.
    """
    v = re.sub(r"[^0-9A-Za-z]", "", (v or "")).upper()
    if not v:
        return None
    if v.isdigit() and len(v) < 11:
        v = v.zfill(11)
    return v


def leggi_ipa(path):
    """[(cf_norm, riga_dict)] dal TXT IndicePA. 'null' e' il vuoto, non una stringa."""
    out = []
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        rdr = csv.DictReader(f, delimiter="\t")
        for r in rdr:
            pulita = {k: (None if (v or "").strip().lower() in ("", "null") else v.strip())
                      for k, v in r.items() if k}
            cf = norm_cf(pulita.get("cf"))
            if cf:
                out.append((cf, pulita))
    return out


def pec(riga):
    """Prima PEC dichiarata tra mail1..mail5, guardando tipo_mailN."""
    for i in range(1, 6):
        if (riga.get(f"tipo_mail{i}") or "").strip().lower() == "pec":
            m = riga.get(f"mail{i}")
            if m and "@" in m:
                return m.lower()
    return None


def qualunque_mail(riga):
    for i in range(1, 6):
        m = riga.get(f"mail{i}")
        if m and "@" in m:
            return m.lower()
    return None


def gate():
    """R1.1 — il tasso di match decide se lo sprint regge. Non scrive nulla."""
    path = scarica()
    ipa = leggi_ipa(path)
    per_cf = {}
    for cf, riga in ipa:
        per_cf.setdefault(cf, riga)
    print(f"IndicePA: {len(ipa):,} righe, {len(per_cf):,} codici fiscali distinti\n")

    cx = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

    # 1) enti distinti nel nostro verticale
    enti = cx.execute(
        "SELECT cf_ente, ente, lotti, importo_aggiudicato FROM v_ente "
        "WHERE cf_ente IS NOT NULL AND trim(cf_ente) <> ''").fetchall()
    tot = len(enti)
    grezzo = sum(1 for cf, *_ in enti if (cf or "").strip().upper() in per_cf)
    match = [(cf, nome, lotti, imp) for cf, nome, lotti, imp in enti
             if norm_cf(cf) in per_cf]
    n = len(match)

    print(f"{'enti nel verticale':38s} {tot:8,}")
    print(f"{'match senza normalizzazione':38s} {grezzo:8,}  {grezzo/tot*100:5.1f}%")
    print(f"{'match con CF normalizzato':38s} {n:8,}  {n/tot*100:5.1f}%   <-- il gate")
    print(f"{'guadagno della normalizzazione':38s} {n-grezzo:8,}")

    # 2) il match pesato conta piu' di quello per testa: gli enti grandi valgono di piu'
    lotti_tot = sum(l or 0 for _, _, l, _ in enti)
    lotti_m = sum(l or 0 for _, _, l, _ in match)
    print(f"\n{'match pesato sui lotti':38s} {lotti_m:8,} / {lotti_tot:,}"
          f"  {lotti_m/lotti_tot*100:5.1f}%")

    # 3) copertura dei recapiti fra gli enti agganciati
    con_pec = sum(1 for cf, *_ in match if pec(per_cf[norm_cf(cf)]))
    con_mail = sum(1 for cf, *_ in match if qualunque_mail(per_cf[norm_cf(cf)]))
    print(f"\n{'di cui con PEC':38s} {con_pec:8,}  {con_pec/n*100:5.1f}% degli agganciati")
    print(f"{'di cui con almeno una email':38s} {con_mail:8,}  {con_mail/n*100:5.1f}%")

    # 4) e sulle scadenze, che sono cio' che vendiamo davvero
    scad = cx.execute(
        "SELECT cf_amministrazione_appaltante FROM v_scadenze_prossime "
        "WHERE giorni_alla_scadenza <= 365").fetchall()
    s_tot = len(scad)
    s_ok = sum(1 for (cf,) in scad
               if norm_cf(cf) in per_cf and pec(per_cf[norm_cf(cf)]))
    print(f"\n{'SCADENZE a 12 mesi':38s} {s_tot:8,}")
    print(f"{'  con PEC dell ente ricavabile':38s} {s_ok:8,}  {s_ok/s_tot*100:5.1f}%")

    # 5) chi resta fuori, per capire se e' un problema o rumore
    fuori = [(nome, lotti, imp) for cf, nome, lotti, imp in enti
             if norm_cf(cf) not in per_cf]
    fuori.sort(key=lambda x: -(x[2] or 0))
    print(f"\nprimi 8 enti NON agganciati, per numero di lotti:")
    for nome, lotti, imp in fuori[:8]:
        print(f"   {lotti:5,} lotti  {(nome or '?')[:56]}")

    print("\n--- verdetto ---")
    q = n / tot * 100
    if q >= 70:
        print(f"  {q:.1f}% >= 70%: il gate passa, R1 procede.")
    elif q >= 50:
        print(f"  {q:.1f}%: sotto la soglia posta in roadmap. Utilizzabile ma va")
        print("  capito chi resta fuori prima di costruirci sopra.")
    else:
        print(f"  {q:.1f}%: troppo basso. L'arricchimento va ripensato.")
    cx.close()


COLONNE = ["cf_ente", "cod_amm", "denominazione_ipa", "pec", "mail_alt",
           "sito_istituzionale", "comune", "provincia", "regione", "cap",
           "indirizzo", "tipologia_amm", "tipologia_istat"]


def ingest():
    """
    R1.2 — carica ente_contatti. Idempotente come tutto il resto: UPSERT sul CF.

    Si caricano SOLO gli enti che compaiono nel nostro verticale: IndicePA ne ha
    23.736, a noi ne servono ~18.700. Il resto sarebbe peso morto.
    """
    path = scarica()
    ipa = {}
    for cf, riga in leggi_ipa(path):
        ipa.setdefault(cf, riga)
    print(f"IndicePA: {len(ipa):,} enti")

    cx = sqlite3.connect(DB)
    with open(os.path.join(QUI, "schema_sqlite.sql"), encoding="utf-8") as f:
        cx.executescript(f.read())          # crea ente_contatti se manca

    nostri = {norm_cf(r[0]) for r in cx.execute(
        "SELECT DISTINCT cf_amministrazione_appaltante FROM cig "
        "WHERE cf_amministrazione_appaltante IS NOT NULL")}
    nostri.discard(None)
    print(f"enti nel verticale: {len(nostri):,}")

    sql = (f"INSERT INTO ente_contatti ({', '.join(COLONNE)}) "
           f"VALUES ({', '.join('?' * len(COLONNE))}) "
           f"ON CONFLICT (cf_ente) DO UPDATE SET "
           + ", ".join(f"{c}=excluded.{c}" for c in COLONNE[1:])
           + ", aggiornato_il=CURRENT_TIMESTAMP")

    batch, n = [], 0
    for cf in nostri:
        r = ipa.get(cf)
        if not r:
            continue
        batch.append((
            cf, r.get("cod_amm"), r.get("des_amm"), pec(r), qualunque_mail(r),
            r.get("sito_istituzionale"), r.get("Comune"), r.get("Provincia"),
            r.get("Regione"), r.get("Cap"), r.get("Indirizzo"),
            r.get("tipologia_amm"), r.get("tipologia_istat"),
        ))
        n += 1
        if len(batch) >= 2000:
            cx.executemany(sql, batch)
            batch.clear()
    if batch:
        cx.executemany(sql, batch)
    cx.commit()

    tot = cx.execute("SELECT count(*) FROM ente_contatti").fetchone()[0]
    con_pec = cx.execute(
        "SELECT count(*) FROM ente_contatti WHERE pec IS NOT NULL").fetchone()[0]
    print(f"\nente_contatti: {tot:,} righe ({n:,} agganciate in questo giro)")
    print(f"  con PEC: {con_pec:,} ({con_pec/tot*100:.1f}%)")

    lead = cx.execute(
        "SELECT count(*) FROM v_scadenze_contattabili "
        "WHERE giorni_alla_scadenza <= 365 AND pec IS NOT NULL").fetchone()[0]
    tot_s = cx.execute(
        "SELECT count(*) FROM v_scadenze_contattabili "
        "WHERE giorni_alla_scadenza <= 365").fetchone()[0]
    print(f"\nscadenze a 12 mesi contattabili: {lead:,} / {tot_s:,} "
          f"({lead/tot_s*100:.1f}%)")
    cx.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", action="store_true", help="R1.1 misura il match")
    ap.add_argument("--ingest", action="store_true", help="carica ente_contatti")
    a = ap.parse_args()
    if a.gate:
        gate()
    elif a.ingest:
        ingest()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
