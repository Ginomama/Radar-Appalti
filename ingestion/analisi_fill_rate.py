"""Tasso di riempimento delle colonne che reggono le promesse di prodotto.
Gira sui ZIP gia' in cache. Nessun download."""
import csv, io, os, re, sys, zipfile
from collections import Counter

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recon_out", "zip")
CPV_PREF = ("72", "48")
csv.field_size_limit(10**7)

WATCH = ["DURATA_PREVISTA", "ESITO", "COD_ESITO", "DATA_COMUNICAZIONE_ESITO",
         "FLAG_PNRR_PNC", "importo_lotto", "importo_complessivo_gara",
         "n_lotti_componenti", "data_scadenza_offerta", "cod_cpv",
         "cf_amministrazione_appaltante", "provincia", "stato", "CIG_COLLEGAMENTO",
         "cig_accordo_quadro", "numero_gara"]

tot = Counter(); pieno = Counter()
tot_it = Counter(); pieno_it = Counter()
righe = righe_it = 0
durata_val = Counter(); esito_val = Counter(); stato_val = Counter()
gare_multi = Counter()

for fn in sorted(os.listdir(CACHE)):
    if not re.match(r"cig_\d{4}_\d{2}\.zip$", fn):
        continue
    with zipfile.ZipFile(os.path.join(CACHE, fn)) as z:
        for nome in z.namelist():
            if not nome.lower().endswith(".csv"):
                continue
            with z.open(nome) as fh:
                rdr = csv.reader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace"), delimiter=";")
                header = next(rdr)
                idx = {h: i for i, h in enumerate(header)}
                i_cpv = idx.get("cod_cpv")
                for r in rdr:
                    righe += 1
                    cpv = (r[i_cpv] if i_cpv is not None and i_cpv < len(r) else "") or ""
                    it = cpv.strip().startswith(CPV_PREF)
                    if it:
                        righe_it += 1
                    for c in WATCH:
                        i = idx.get(c)
                        if i is None:
                            continue
                        v = (r[i] if i < len(r) else "") or ""
                        v = v.strip()
                        tot[c] += 1
                        if v: pieno[c] += 1
                        if it:
                            tot_it[c] += 1
                            if v: pieno_it[c] += 1
                    if it:
                        i = idx.get("DURATA_PREVISTA")
                        if i is not None and i < len(r) and r[i].strip():
                            durata_val[r[i].strip()] += 1
                        i = idx.get("ESITO")
                        if i is not None and i < len(r):
                            esito_val[(r[i] or "(vuoto)").strip()[:40]] += 1
                        i = idx.get("stato")
                        if i is not None and i < len(r):
                            stato_val[(r[i] or "(vuoto)").strip()[:40]] += 1
                        i = idx.get("numero_gara")
                        if i is not None and i < len(r) and r[i].strip():
                            gare_multi[r[i].strip()] += 1

print(f"righe totali: {righe:,}   righe CPV 72/48: {righe_it:,}\n")
print(f"{'colonna':32s} {'tutte':>10s} {'CPV IT':>10s}")
print("-" * 55)
for c in WATCH:
    a = pieno[c]/tot[c]*100 if tot[c] else float('nan')
    b = pieno_it[c]/tot_it[c]*100 if tot_it[c] else float('nan')
    print(f"{c:32s} {a:9.1f}% {b:9.1f}%")

print("\n--- DURATA_PREVISTA: valori piu' frequenti (CPV IT) ---")
for v, n in durata_val.most_common(12):
    print(f"  {n:7,}  {v!r}")
print(f"  valori distinti: {len(durata_val):,}")

print("\n--- ESITO (CPV IT) ---")
for v, n in esito_val.most_common(10):
    print(f"  {n:7,}  {v}")

print("\n--- stato (CPV IT) ---")
for v, n in stato_val.most_common(10):
    print(f"  {n:7,}  {v}")

multi = sum(1 for v in gare_multi.values() if v > 1)
righe_in_multi = sum(v for v in gare_multi.values() if v > 1)
print(f"\n--- multi-lotto (CPV IT) ---")
print(f"  numero_gara distinti: {len(gare_multi):,}")
print(f"  gare con >1 riga:     {multi:,} ({multi/len(gare_multi)*100:.1f}% delle gare)")
print(f"  righe dentro gare multi-lotto: {righe_in_multi:,} ({righe_in_multi/righe_it*100:.1f}% delle righe)")
