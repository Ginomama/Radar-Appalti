#!/usr/bin/env python3
"""
Ricognizione ANAC open data — Fase 0 del radar appalti.

Scopo: capire SE il segnale esiste, prima di costruire qualsiasi cosa.
Scarica i CSV mensili dei bandi CIG, ne ispeziona lo schema reale
(le colonne cambiano tra annualita', non fidarsi della documentazione)
e conta quanti bandi escono sui CPV e nel territorio che ti interessano.

DEVE girare dalla tua macchina: il WAF ANAC risponde 403 ai cloud provider.

Uso:
    python anac_recon.py                  # ultimi 12 mesi, CPV IT, tutta Italia
    python anac_recon.py --mesi 24
    python anac_recon.py --cpv 45,50 --province VE,TV,PN,UD
"""

import argparse, csv, io, json, os, re, ssl, sys, zipfile
from collections import Counter, defaultdict
from datetime import date
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE = "https://dati.anticorruzione.it/opendata/download/dataset/cig-{y}/filesystem/cig_csv_{y}_{m:02d}.zip"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recon_out")

# La catena di certificati di dati.anticorruzione.it ha una CA con Basic Constraints
# non marcati critical. Da Python 3.13 create_default_context() attiva VERIFY_X509_STRICT
# di default e la rifiuta. Disattiviamo SOLO il controllo strict: hostname e catena
# restano verificati. E' l'equivalente stdlib dell'SSL adapter custom di ANAC-OD-DOWNLOADER.
SSLCTX = ssl.create_default_context()
SSLCTX.verify_flags &= ~ssl.VERIFY_X509_STRICT

# Province target -> prefissi delle sigle usate nei campi territoriali.
# Vuoto = tutta Italia.
DEFAULT_CPV = ["72", "48"]        # servizi IT / software
DEFAULT_PROV = []                  # es. ["VE","TV","PN","UD"]


def mesi_indietro(n):
    oggi = date.today()
    y, m = oggi.year, oggi.month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
        out.append((y, m))
    return out


def scarica(y, m, cache):
    dest = os.path.join(cache, f"cig_{y}_{m:02d}.zip")
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    url = BASE.format(y=y, m=m)
    req = Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urlopen(req, timeout=180, context=SSLCTX) as r, open(dest, "wb") as f:
            f.write(r.read())
    except HTTPError as e:
        print(f"  [{y}-{m:02d}] HTTP {e.code}"
              f"{'  <-- WAF: stai girando da cloud?' if e.code == 403 else ''}")
        return None
    except URLError as e:
        print(f"  [{y}-{m:02d}] errore rete: {e.reason}")
        return None
    return dest


def col(header, *candidati):
    """Trova una colonna per nome approssimato: i nomi cambiano tra annualita'."""
    norm = {re.sub(r"[^a-z0-9]", "", h.lower()): i for i, h in enumerate(header)}
    for c in candidati:
        k = re.sub(r"[^a-z0-9]", "", c.lower())
        if k in norm:
            return norm[k]
    for i, h in enumerate(header):
        hl = re.sub(r"[^a-z0-9]", "", h.lower())
        for c in candidati:
            if re.sub(r"[^a-z0-9]", "", c.lower()) in hl:
                return i
    return None


def analizza(zpath, cpv_pref, province, stats, schema_visto):
    with zipfile.ZipFile(zpath) as z:
        for nome in z.namelist():
            if not nome.lower().endswith(".csv"):
                continue
            with z.open(nome) as fh:
                testo = io.TextIOWrapper(fh, encoding="utf-8", errors="replace")
                rdr = csv.reader(testo, delimiter=";")
                try:
                    header = next(rdr)
                except StopIteration:
                    continue

                if not schema_visto:
                    schema_visto.extend(header)

                i_cpv = col(header, "cpv", "codice_cpv", "cpv_descrizione")
                i_imp = col(header, "importo_complessivo_gara", "importo_lotto", "importo")
                i_sa = col(header, "denominazione_amministrazione_appaltante",
                           "denominazione_ente", "stazione_appaltante")
                i_cf = col(header, "cf_amministrazione_appaltante", "codice_fiscale_struttura")
                i_prov = col(header, "provincia", "sigla_provincia", "provincia_struttura")
                i_ogg = col(header, "oggetto_gara", "oggetto_lotto", "oggetto")

                for riga in rdr:
                    stats["righe_totali"] += 1
                    if i_cpv is None or i_cpv >= len(riga):
                        continue
                    cpv = (riga[i_cpv] or "").strip()
                    if not cpv or not any(cpv.startswith(p) for p in cpv_pref):
                        continue
                    if province and i_prov is not None and i_prov < len(riga):
                        if (riga[i_prov] or "").strip().upper() not in province:
                            continue
                    stats["match"] += 1
                    stats["per_cpv"][cpv[:5]] += 1
                    if i_sa is not None and i_sa < len(riga):
                        stats["per_ente"][(riga[i_sa] or "?").strip()[:70]] += 1
                    if i_prov is not None and i_prov < len(riga):
                        stats["per_prov"][(riga[i_prov] or "?").strip().upper()] += 1
                    if i_imp is not None and i_imp < len(riga):
                        try:
                            stats["importo"] += float((riga[i_imp] or "0").replace(",", "."))
                        except ValueError:
                            pass
                    if len(stats["esempi"]) < 25 and i_ogg is not None and i_ogg < len(riga):
                        stats["esempi"].append({
                            "cpv": cpv,
                            "ente": riga[i_sa][:70] if i_sa is not None and i_sa < len(riga) else "",
                            "oggetto": riga[i_ogg][:140],
                        })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mesi", type=int, default=12)
    ap.add_argument("--cpv", default=",".join(DEFAULT_CPV),
                    help="prefissi CPV separati da virgola, es. 72,48")
    ap.add_argument("--province", default=",".join(DEFAULT_PROV),
                    help="sigle provincia separate da virgola, vuoto = tutta Italia")
    a = ap.parse_args()

    cpv_pref = [c.strip() for c in a.cpv.split(",") if c.strip()]
    province = {p.strip().upper() for p in a.province.split(",") if p.strip()}

    os.makedirs(OUT, exist_ok=True)
    cache = os.path.join(OUT, "zip")
    os.makedirs(cache, exist_ok=True)

    print(f"CPV: {cpv_pref}   Province: {sorted(province) or 'tutte'}   Mesi: {a.mesi}\n")

    stats = {"righe_totali": 0, "match": 0, "importo": 0.0,
             "per_cpv": Counter(), "per_ente": Counter(),
             "per_prov": Counter(), "esempi": []}
    schema = []
    ok = mancanti = 0

    for y, m in mesi_indietro(a.mesi):
        print(f"[{y}-{m:02d}] scarico...", end=" ", flush=True)
        z = scarica(y, m, cache)
        if not z:
            mancanti += 1
            continue
        print(f"{os.path.getsize(z)/1e6:.1f} MB, analizzo...", end=" ", flush=True)
        try:
            analizza(z, cpv_pref, province, stats, schema)
            ok += 1
            print("ok")
        except zipfile.BadZipFile:
            print("ZIP corrotto (probabile pagina di errore HTML)")
            mancanti += 1

    print("\n" + "=" * 60)
    print(f"Mesi scaricati: {ok}   Mesi mancanti/404: {mancanti}")
    print(f"Righe CIG totali esaminate: {stats['righe_totali']:,}")
    print(f"Bandi che matchano i tuoi CPV: {stats['match']:,}")
    if ok:
        print(f"  --> media mensile: {stats['match']/ok:.1f}   stima annua: {stats['match']/ok*12:.0f}")
    print(f"Valore complessivo intercettato: EUR {stats['importo']:,.0f}")

    print("\nTop 15 enti:")
    for e, n in stats["per_ente"].most_common(15):
        print(f"  {n:5d}  {e}")
    print("\nDistribuzione province:")
    for p, n in stats["per_prov"].most_common(20):
        print(f"  {n:5d}  {p}")
    print("\nTop CPV:")
    for c, n in stats["per_cpv"].most_common(15):
        print(f"  {n:5d}  {c}")

    with open(os.path.join(OUT, "recon.json"), "w", encoding="utf-8") as f:
        json.dump({
            "parametri": {"mesi": a.mesi, "cpv": cpv_pref, "province": sorted(province)},
            "mesi_ok": ok, "mesi_mancanti": mancanti,
            "righe_totali": stats["righe_totali"], "match": stats["match"],
            "importo_totale": stats["importo"],
            "per_cpv": dict(stats["per_cpv"]),
            "per_ente": dict(stats["per_ente"].most_common(100)),
            "per_prov": dict(stats["per_prov"]),
            "esempi": stats["esempi"],
            "schema_csv": schema,
        }, f, ensure_ascii=False, indent=2)

    print(f"\nDettaglio + schema reale del CSV -> {os.path.join(OUT, 'recon.json')}")
    if not ok:
        print("\nNessun mese scaricato. Se vedi 403: sei dietro un IP cloud/VPN, "
              "rilancia da connessione domestica o aziendale.")


if __name__ == "__main__":
    main()
