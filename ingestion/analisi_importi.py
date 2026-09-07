import csv, io, os, re, zipfile
from collections import defaultdict
CACHE=os.path.join(os.path.dirname(os.path.abspath(__file__)),"recon_out","zip")
csv.field_size_limit(10**7)
somma_righe=0.0; somma_lotti=0.0; per_gara={}
n=0
for fn in sorted(os.listdir(CACHE)):
    if not re.match(r"cig_\d{4}_\d{2}\.zip$",fn): continue
    with zipfile.ZipFile(os.path.join(CACHE,fn)) as z:
        for nome in z.namelist():
            if not nome.lower().endswith(".csv"): continue
            with z.open(nome) as fh:
                rdr=csv.reader(io.TextIOWrapper(fh,encoding="utf-8",errors="replace"),delimiter=";")
                h=next(rdr); idx={c:i for i,c in enumerate(h)}
                ic,ig,icg,il=idx["cod_cpv"],idx["numero_gara"],idx["importo_complessivo_gara"],idx["importo_lotto"]
                for r in rdr:
                    if len(r)<=max(ic,ig,icg,il): continue
                    if not (r[ic] or "").strip().startswith(("72","48")): continue
                    n+=1
                    def f(s):
                        try: return float((s or "0").replace(",","."))
                        except ValueError: return 0.0
                    somma_righe+=f(r[icg]); somma_lotti+=f(r[il])
                    g=(r[ig] or "").strip()
                    if g: per_gara[g]=f(r[icg])
dedup=sum(per_gara.values())
print(f"righe CPV IT: {n:,}   gare distinte: {len(per_gara):,}\n")
print(f"A) somma importo_complessivo_gara per riga  (metodo attuale): EUR {somma_righe:>18,.0f}")
print(f"B) somma importo_complessivo_gara dedup su numero_gara      : EUR {dedup:>18,.0f}")
print(f"C) somma importo_lotto                                      : EUR {somma_lotti:>18,.0f}")
print(f"\ngonfiaggio A vs B: {(somma_righe/dedup-1)*100:.1f}%")
print(f"A vs C:            {(somma_righe/somma_lotti-1)*100:.1f}%")
