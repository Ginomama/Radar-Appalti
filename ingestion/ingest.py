#!/usr/bin/env python3
"""
Ingestion ANAC -> SQLite. Fase 3 del radar appalti.

Incrementale e idempotente: il CIG e' la chiave naturale, rieseguire lo stesso
file non duplica ne' perde righe. Ogni file processato lascia una riga in
ingestion_log, comprese le assenze: i buchi nella sequenza sono un dato di
prodotto, non un errore da nascondere.

DEVE girare da un runner non-cloud: il WAF ANAC risponde 403 ai cloud provider.

Uso:
    python ingest.py --init                    # crea schema (idempotente)
    python ingest.py --backfill                # ingerisce tutti gli ZIP in cache
    python ingest.py --delta 2026-08           # scarica e ingerisce un delta mensile
    python ingest.py --delta ultimi:3          # gli ultimi 3 delta disponibili
    python ingest.py --bulk                    # aggiudicazioni/aggiudicatari/avvio
    python ingest.py --stato                   # riepilogo e buchi

    python ingest.py --cpv 45,50 ...           # cambia verticale (default 72,48)

Solo libreria standard.
"""

import argparse
import csv
import io
import os
import re
import sqlite3
import sys
import zipfile
from datetime import datetime

import anac_http as anac

QUI = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(QUI, "radar.db")
CACHE = os.path.join(QUI, "recon_out", "zip")
SCHEMA = os.path.join(QUI, "schema_sqlite.sql")

csv.field_size_limit(10 ** 7)

# I bulk non sono mensili: un file unico per dataset.
BULK = {
    "aggiudicazioni":  ("aggiudicazioni",  "aggiudicazioni_csv.zip"),
    "aggiudicatari":   ("aggiudicatari",   "aggiudicatari_csv.zip"),
    "avvio_contratto": ("avvio-contratto", "avvio-contratto_csv.zip"),
}

# Colonne intere e numeriche, per la conversione. Il resto resta testo.
INTERI = {"n_lotti_componenti", "num_imprese_offerenti", "num_imprese_invitate",
          "num_imprese_richiedenti", "numero_offerte_ammesse",
          "numero_offerte_escluse", "n_manif_interesse"}
REALI = {"importo_lotto", "importo_complessivo_gara", "importo_sicurezza",
         "importo_aggiudicazione", "ribasso_aggiudicazione",
         "massimo_ribasso", "minimo_ribasso"}
DATE = {"data_pubblicazione", "data_scadenza_offerta", "data_comunicazione_esito",
        "data_aggiudicazione_definitiva", "data_stipula_contratto",
        "data_esecutivita_contratto", "data_termine_contrattuale",
        "data_inizio_effettiva", "data_verbale_prima_consegna",
        "data_verbale_consegna_definitiva"}


# --------------------------------------------------------------- conversioni
def _num(v):
    v = (v or "").strip().replace(",", ".")
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _int(v):
    f = _num(v)
    return int(f) if f is not None else None


def _data(v):
    """Normalizza a ISO. Tollera DD/MM/YYYY e code orarie. '0001-...' -> None."""
    v = (v or "").strip()
    if not v:
        return None
    v = v.split(" ")[0].split("T")[0]
    for f in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            d = datetime.strptime(v, f).date()
            return d.isoformat() if 1990 <= d.year <= 2100 else None
        except ValueError:
            pass
    return None


def converti(col, v):
    if col in DATE:
        return _data(v)
    if col in REALI:
        return _num(v)
    if col in INTERI:
        return _int(v)
    v = (v or "").strip()
    return v or None


# ------------------------------------------------------------------ database
def connetti():
    cx = sqlite3.connect(DB)
    cx.execute("PRAGMA foreign_keys = ON")
    return cx


def init(cx):
    with open(SCHEMA, encoding="utf-8") as f:
        cx.executescript(f.read())
    cx.commit()
    print(f"schema creato/verificato -> {DB}")


def colonne(cx, tabella):
    """Colonne scrivibili: escluse le generated, che SQLite rifiuta in INSERT."""
    out = []
    for r in cx.execute(f"PRAGMA table_xinfo({tabella})"):
        nome, hidden = r[1], r[6]
        if hidden in (2, 3):        # 2=VIRTUAL, 3=STORED
            continue
        out.append(nome)
    return out


def upsert_sql(tabella, cols, pk):
    """INSERT ... ON CONFLICT DO UPDATE. Sintassi identica su Postgres."""
    agg = [c for c in cols if c not in pk and c != "ingerito_il"]
    return (
        f"INSERT INTO {tabella} ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' * len(cols))}) "
        f"ON CONFLICT ({', '.join(pk)}) DO UPDATE SET "
        + ", ".join(f"{c}=excluded.{c}" for c in agg)
        + ", ingerito_il=CURRENT_TIMESTAMP"
    )


def log(cx, dataset, risorsa, esito, **kw):
    campi = dict(dataset=dataset, risorsa=risorsa, esito=esito,
                 finito_il=datetime.now().isoformat(timespec="seconds"), **kw)
    cols = list(campi)
    cx.execute(
        f"INSERT INTO ingestion_log ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' * len(cols))}) "
        f"ON CONFLICT (dataset, risorsa) DO UPDATE SET "
        + ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("dataset", "risorsa")),
        [campi[c] for c in cols])
    cx.commit()


# ----------------------------------------------------------------- ingestion
# -------------------------------------------------- sentinella sullo schema
# ANAC rinomina le colonne fra un'annualita' e l'altra: e' documentato e ci e'
# gia' costato una diagnosi sbagliata. Il guaio non e' il cambio in se', e'
# che ingerisci_zip lo assorbe in silenzio — una colonna assente non entra
# nella mappa e diventa None per tutte le righe. Il database si riempie di
# nulla e il conteggio delle righe scritte resta identico.
#
# ESSENZIALI = senza queste il caricamento non ha senso: si ferma.
# Le altre mancanti si segnalano rumorosamente e si va avanti.
ESSENZIALI = {
    "cig":             ("cig", "cod_cpv", "oggetto_lotto",
                        "cf_amministrazione_appaltante"),
    "aggiudicazioni":  ("id_aggiudicazione", "cig", "importo_aggiudicazione"),
    "aggiudicatari":   ("id_aggiudicazione", "codice_fiscale", "cig",
                        "denominazione"),
    "avvio_contratto": ("id_aggiudicazione", "cig",
                        "data_termine_contrattuale"),
}


def controlla_schema(zpath, tabella, cols_tab, presenti):
    """Confronta l'header del CSV con le colonne attese.

    Ritorna l'elenco delle mancanti non essenziali, da annotare nel log.
    Solleva se ne manca una essenziale: meglio fermarsi che ingerire colonne
    vuote e accorgersene tre settimane dopo da un grafico storto.
    """
    attese = [c for c in cols_tab if c not in ("src_file", "ingerito_il")]
    mancanti = [c for c in attese if c not in presenti]
    critiche = [c for c in ESSENZIALI.get(tabella, ()) if c not in presenti]

    if critiche:
        raise SystemExit(
            f"\n[SCHEMA CAMBIATO] {os.path.basename(zpath)} non contiene "
            f"{', '.join(critiche)}.\n"
            f"Sono colonne senza le quali la tabella '{tabella}' non serve a "
            f"niente.\nANAC rinomina le colonne fra annualita': confronta "
            f"l'header del CSV con lo schema in schema_sqlite.sql e aggiorna "
            f"la mappatura.\nColonne trovate nel file: "
            f"{', '.join(sorted(presenti)[:25])}"
            + (" ..." if len(presenti) > 25 else ""))

    if mancanti:
        print(f"  [schema] {os.path.basename(zpath)}: {len(mancanti)} colonne "
              f"attese e non presenti -> resteranno vuote:",
              file=sys.stderr)
        print(f"           {', '.join(mancanti)}", file=sys.stderr)

    # Colonne nuove: non e' un errore, ma e' l'unico momento in cui si scopre
    # che ANAC ha aggiunto qualcosa che potrebbe servirci.
    nuove = [c for c in presenti if c not in attese]
    if nuove:
        print(f"  [schema] {len(nuove)} colonne nel file che non usiamo: "
              f"{', '.join(sorted(nuove)[:12])}"
              + (" ..." if len(nuove) > 12 else ""), file=sys.stderr)
    return mancanti


def ingerisci_zip(cx, zpath, tabella, pk, cpv_pref=None, filtro_cig=None, src=None):
    """
    Carica un CSV zippato dentro `tabella`, mappando le colonne per nome
    (case-insensitive: ANAC mescola maiuscole e minuscole nello stesso file).

    cpv_pref   -> tiene solo le righe del verticale (tabella cig)
    filtro_cig -> tiene solo le righe i cui CIG sono gia' in database (bulk)
    """
    cols_tab = colonne(cx, tabella)
    sql = upsert_sql(tabella, cols_tab, pk)
    lette = scritte = 0
    batch = []

    with zipfile.ZipFile(zpath) as z:
        for nome in [n for n in z.namelist() if n.lower().endswith(".csv")]:
            with z.open(nome) as fh:
                rdr = csv.reader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace"),
                                 delimiter=";")
                header = next(rdr)
                # mappa colonna_tabella -> indice nel CSV
                low = {h.strip().lower(): i for i, h in enumerate(header)}
                # Prima di leggere una riga: l'header dice gia' tutto, e una
                # colonna sparita qui diventerebbe None per l'intero file.
                controlla_schema(zpath, tabella, cols_tab, set(low))
                mappa = {c: low[c] for c in cols_tab if c in low}
                i_cpv = low.get("cod_cpv")

                for r in rdr:
                    lette += 1
                    if cpv_pref is not None:
                        if i_cpv is None or i_cpv >= len(r):
                            continue
                        if not (r[i_cpv] or "").strip().startswith(cpv_pref):
                            continue
                    if filtro_cig is not None:
                        i = mappa["cig"]
                        if i >= len(r) or (r[i] or "").strip() not in filtro_cig:
                            continue

                    riga = []
                    for c in cols_tab:
                        if c == "src_file":
                            riga.append(src)
                        elif c == "ingerito_il":
                            riga.append(datetime.now().isoformat(timespec="seconds"))
                        elif c in mappa and mappa[c] < len(r):
                            riga.append(converti(c, r[mappa[c]]))
                        else:
                            riga.append(None)

                    # la PK non puo' essere vuota: riga inutilizzabile, si salta
                    if any(riga[cols_tab.index(k)] in (None, "") for k in pk):
                        continue
                    batch.append(riga)
                    if len(batch) >= 5000:
                        cx.executemany(sql, batch)
                        scritte += len(batch)
                        batch.clear()
    if batch:
        cx.executemany(sql, batch)
        scritte += len(batch)
    cx.commit()
    return lette, scritte


def set_cig(cx):
    return {r[0] for r in cx.execute("SELECT cig FROM cig")}


def backfill(cx, cpv_pref):
    """Ingerisce tutti gli ZIP mensili gia' in cache."""
    files = sorted(f for f in os.listdir(CACHE)
                   if re.match(r"(cig_\d{4}_\d{2}|\d{8}-cig_csv)\.zip$", f))
    if not files:
        print("nessuno ZIP CIG in cache — usa --delta per scaricarne uno")
        return
    for fn in files:
        src = fn[:-4]
        print(f"[{src}] ...", end=" ", flush=True)
        try:
            l, s = ingerisci_zip(cx, os.path.join(CACHE, fn), "cig", ["cig"],
                                 cpv_pref=cpv_pref, src=src)
            print(f"lette {l:,} -> upsert {s:,}")
            log(cx, "cig", src, "ok", righe_lette=l, righe_upsert=s,
                byte_scaricati=os.path.getsize(os.path.join(CACHE, fn)))
        except zipfile.BadZipFile:
            print("ZIP corrotto")
            log(cx, "cig", src, "zip_corrotto")


def delta(cx, spec, cpv_pref):
    """Scarica e ingerisce i delta mensili del dataset `cig`."""
    ris = {n: u for n, u in anac.risorse_csv("cig") if re.match(r"\d{8}-cig_csv$", n)}
    disponibili = sorted(ris)
    if not disponibili:
        print("nessun delta mensile pubblicato")
        return

    if spec.startswith("ultimi:"):
        scelti = disponibili[-int(spec.split(":")[1]):]
    else:                                        # '2026-08' -> '20260801-cig_csv'
        tag = spec.replace("-", "") + "01-cig_csv"
        if tag not in ris:
            print(f"{spec} non pubblicato. Disponibili: {', '.join(disponibili)}")
            log(cx, "cig", tag, "assente_404", periodo=spec)
            return
        scelti = [tag]

    os.makedirs(CACHE, exist_ok=True)
    for nome in scelti:
        dest = os.path.join(CACHE, nome + ".zip")
        print(f"[{nome}] scarico...", end=" ", flush=True)
        byte, lm, status = anac.scarica(ris[nome], dest)
        if status != 200 or byte == 0:
            print(f"HTTP {status}")
            log(cx, "cig", nome, "assente_404" if status == 404 else "errore",
                http_status=status)
            continue
        print(f"{byte/1e6:.1f} MB, ingerisco...", end=" ", flush=True)
        periodo = f"{nome[:4]}-{nome[4:6]}"
        try:
            l, s = ingerisci_zip(cx, dest, "cig", ["cig"], cpv_pref=cpv_pref, src=nome)
            print(f"lette {l:,} -> upsert {s:,}")
            log(cx, "cig", nome, "ok", periodo=periodo, http_status=status,
                byte_scaricati=byte, righe_lette=l, righe_upsert=s, last_modified=lm)
        except zipfile.BadZipFile:
            print("ZIP corrotto")
            log(cx, "cig", nome, "zip_corrotto", periodo=periodo, byte_scaricati=byte)


def storico(cx, anni, cpv_pref, purga=False):
    """
    Backfill dagli archivi annuali `cig-{YYYY}`, un file per mese.

    Con purga=True lo ZIP viene eliminato appena ingerito. La regola del progetto
    e' tenere la cache per non riscaricare e non farsi notare dal WAF: l'intento
    resta rispettato, ogni file si scarica una volta sola. Ma l'artefatto e'
    radar.db, e tenere ~1,5 GB di ZIP dentro una cartella sincronizzata non paga.
    """
    tot_lette = tot_scritte = mancanti = 0
    for anno in anni:
        try:
            risorse = sorted(anac.risorse_csv(f"cig-{anno}"))
        except Exception as e:
            print(f"[cig-{anno}] catalogo non raggiungibile: {e}")
            log(cx, "cig", f"cig-{anno}", "assente_404", periodo=str(anno))
            mancanti += 12
            continue
        print(f"[cig-{anno}] {len(risorse)} mesi")
        for nome, url in risorse:
            gia = cx.execute(
                "SELECT righe_upsert FROM ingestion_log "
                "WHERE dataset='cig' AND risorsa=? AND esito='ok'", (nome,)).fetchone()
            if gia:
                print(f"   {nome:22s} gia' ingerito ({gia[0]:,})")
                continue
            dest = os.path.join(CACHE, nome + ".zip")
            byte, lm, status = anac.scarica(url, dest)
            if status != 200 or byte == 0:
                print(f"   {nome:22s} HTTP {status}")
                log(cx, "cig", nome, "assente_404" if status == 404 else "errore",
                    http_status=status)
                mancanti += 1
                continue
            try:
                l, s = ingerisci_zip(cx, dest, "cig", ["cig"],
                                     cpv_pref=cpv_pref, src=nome)
            except zipfile.BadZipFile:
                print(f"   {nome:22s} ZIP corrotto")
                log(cx, "cig", nome, "zip_corrotto", byte_scaricati=byte)
                mancanti += 1
                continue
            tot_lette += l
            tot_scritte += s
            periodo = f"{nome[8:12]}-{nome[13:15]}"
            log(cx, "cig", nome, "ok", periodo=periodo, http_status=status,
                byte_scaricati=byte, righe_lette=l, righe_upsert=s, last_modified=lm)
            nota = ""
            if purga:
                os.remove(dest)
                nota = "  (zip rimosso)"
            print(f"   {nome:22s} {byte/1e6:5.1f} MB  lette {l:8,} -> upsert {s:7,}{nota}")
    print(f"\nstorico: righe lette {tot_lette:,}, upsert {tot_scritte:,}, "
          f"file mancanti {mancanti}")


def bulk(cx, solo_delta=False):
    """
    Aggiudicazioni, aggiudicatari, avvio contratto.

    Ogni dataset ha un file base con lo storico completo — fermo a gennaio 2026 —
    piu' delta mensili datati YYYYMM01-*. Con il solo file base le aggiudicazioni
    recenti restano scoperte: misurato, la coorte giu-ago 2026 aveva copertura 0%
    contro il 94,9% della coorte 2025. Si carica quindi base + delta in ordine
    cronologico, cosi' i record piu' recenti sovrascrivono i vecchi via UPSERT.

    Si filtra sui CIG gia' in database: sono ~5 M righe l'uno, non ha senso
    caricarle tutte per un verticale che ne usa il 5%.
    """
    cigs = set_cig(cx)
    if not cigs:
        print("tabella cig vuota: esegui prima --backfill o --delta")
        return
    print(f"filtro sui {len(cigs):,} CIG del verticale gia' in database\n")
    pk = {"aggiudicazioni": ["id_aggiudicazione"],
          "aggiudicatari": ["id_aggiudicazione", "codice_fiscale"],
          "avvio_contratto": ["id_aggiudicazione"]}

    for tab, (dataset, _) in BULK.items():
        try:
            risorse = anac.risorse_csv(dataset)
        except Exception as e:
            print(f"[{tab}] catalogo non raggiungibile: {e}")
            log(cx, dataset, "package_show", "errore", note=str(e)[:200])
            continue

        datati = sorted((n, u) for n, u in risorse if re.match(r"\d{8}-", n))
        base = [(n, u) for n, u in risorse if not re.match(r"\d{8}-", n)]
        seq = ([] if solo_delta else base) + datati
        if not seq:
            print(f"[{tab}] nessuna risorsa CSV nel catalogo")
            continue

        print(f"[{tab}] {len(seq)} risorse")
        for nome, url in seq:
            dest = os.path.join(CACHE, nome + ".zip")
            esisteva = os.path.exists(dest)
            byte, lm, status = anac.scarica(url, dest)
            if status != 200 or byte == 0:
                print(f"   {nome:34s} HTTP {status}")
                log(cx, dataset, nome, "assente_404" if status == 404 else "errore",
                    http_status=status)
                continue
            marca = "cache" if esisteva else f"{byte/1e6:.1f} MB"
            try:
                l, s = ingerisci_zip(cx, dest, tab, pk[tab],
                                     filtro_cig=cigs, src=nome)
            except zipfile.BadZipFile:
                print(f"   {nome:34s} ZIP corrotto")
                log(cx, dataset, nome, "zip_corrotto", byte_scaricati=byte)
                continue
            print(f"   {nome:34s} {marca:>9s}  lette {l:9,} -> upsert {s:7,}")
            periodo = f"{nome[:4]}-{nome[4:6]}" if re.match(r"\d{8}-", nome) else None
            log(cx, dataset, nome, "ok", periodo=periodo, http_status=status,
                byte_scaricati=byte, righe_lette=l, righe_upsert=s, last_modified=lm)
        print()


# ------------------------------------------------------------ verifica cache
def verifica_cache(riscarica=False):
    """Gli ZIP in cache sono interi?

    Serve perche' fino al 7 settembre 2026 scarica() non confrontava i byte
    ricevuti con Content-Length: su file da 100+ MB la connessione cadeva a
    meta' e il troncone finiva in cache, dove passava il test 'esiste ed e'
    grande'. Ogni ritentativo restituiva lo stesso file rotto. Un delta da
    247 MB era rimasto fermo a 115 MB, cioe' 87.360 CIG mai ingeriti.

    Il confronto e' con la dimensione dichiarata dal server, non con
    l'apertura dello ZIP: un file troncato in mezzo al CSV puo' aprirsi
    benissimo e mancare comunque di meta' righe."""
    if not os.path.isdir(CACHE):
        print("nessuna cache da verificare")
        return 0
    locali = sorted(f for f in os.listdir(CACHE) if f.endswith(".zip"))
    if not locali:
        print("nessuna cache da verificare")
        return 0

    # nome risorsa -> url, da tutti i dataset che usiamo
    url = {}
    for ds in ("cig", "aggiudicazioni", "aggiudicatari", "avvio-contratto"):
        try:
            for nome, u in anac.risorse_csv(ds):
                url[nome + ".zip"] = u
        except Exception as e:
            print(f"  [{ds}] catalogo non raggiungibile: {e}")

    print(f"{len(locali)} file in cache\n")
    monchi = []
    for f in locali:
        loc = os.path.getsize(os.path.join(CACHE, f))
        u = url.get(f)
        if not u:
            print(f"  {f:38s} {loc/1e6:8.1f} MB   non piu' nel catalogo, "
                  f"non verificabile")
            continue
        _st, remoto, _lm = anac.testa(u)
        if not remoto:
            print(f"  {f:38s} {loc/1e6:8.1f} MB   il server non dichiara "
                  f"la dimensione")
            continue
        if loc >= remoto:
            print(f"  {f:38s} {loc/1e6:8.1f} MB   intero")
        else:
            monchi.append(f)
            print(f"  {f:38s} {loc/1e6:8.1f} MB   TRONCATO: mancano "
                  f"{(remoto-loc)/1e6:.1f} MB su {remoto/1e6:.1f}")

    if not monchi:
        print("\ntutti interi.")
        return 0
    print(f"\n{len(monchi)} file troncati.")
    if not riscarica:
        print("Per rifarli: python ingest.py --verifica-cache --riscarica")
        return len(monchi)
    for f in monchi:
        os.remove(os.path.join(CACHE, f))
        print(f"  cancellato {f}")
    print("\nRilancia l'ingestione: gli ZIP mancanti verranno riscaricati.")
    # Con --riscarica il problema e' stato risolto, non solo segnalato: uscire
    # con errore farebbe fallire il job schedulato proprio quando ha funzionato.
    return 0


# -------------------------------------------------------------------- stato
def stato(cx):
    print("=== contenuto ===")
    for t in ("cig", "aggiudicazioni", "aggiudicatari", "avvio_contratto"):
        n = cx.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        print(f"  {t:18s} {n:9,}")

    n_cig = cx.execute("SELECT count(*) FROM cig").fetchone()[0]
    if n_cig:
        print("\n=== copertura join ===")
        for t, etichetta in (("aggiudicazioni", "con esito aggiudicato"),
                             ("avvio_contratto", "con contratto avviato")):
            n = cx.execute(
                f"SELECT count(DISTINCT c.cig) FROM cig c JOIN {t} x ON x.cig=c.cig"
            ).fetchone()[0]
            print(f"  {etichetta:24s} {n:8,}  ({n/n_cig*100:5.1f}%)")
        n = cx.execute(
            "SELECT count(DISTINCT c.cig) FROM cig c "
            "JOIN aggiudicatari t ON t.cig=c.cig").fetchone()[0]
        print(f"  {'con vincitore noto':24s} {n:8,}  ({n/n_cig*100:5.1f}%)")

        r = cx.execute(
            "SELECT min(data_pubblicazione), max(data_pubblicazione) FROM cig "
            "WHERE data_pubblicazione IS NOT NULL").fetchone()
        print(f"\n  periodo coperto: {r[0]} -> {r[1]}")

    print("\n=== ingestion_log ===")
    righe = cx.execute(
        "SELECT dataset, risorsa, esito, righe_upsert, last_modified "
        "FROM ingestion_log ORDER BY dataset, risorsa").fetchall()
    for d, r_, e, u, lm in righe:
        marca = "ok " if e == "ok" else "!! "
        print(f"  {marca}{d:16s} {r_:24s} {e:12s} {u or 0:9,}  {lm or ''}")
    buchi = [r for r in righe if r[2] != "ok"]
    print(f"\n  {len(righe)} risorse tracciate, {len(buchi)} non disponibili")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--delta", metavar="YYYY-MM|ultimi:N")
    ap.add_argument("--bulk", action="store_true")
    ap.add_argument("--storico", metavar="YYYY-YYYY",
                    help="backfill dagli archivi annuali, es. 2021-2025")
    ap.add_argument("--purga", action="store_true",
                    help="elimina lo ZIP appena ingerito (usa con --storico)")
    ap.add_argument("--stato", action="store_true")
    ap.add_argument("--verifica-cache", action="store_true",
                    help="controlla che gli ZIP scaricati siano interi")
    ap.add_argument("--riscarica", action="store_true",
                    help="con --verifica-cache: cancella i troncati")
    ap.add_argument("--cpv", default="72,48", help="prefissi CPV, vuoto = tutti")
    a = ap.parse_args()

    if a.verifica_cache:
        sys.exit(1 if verifica_cache(a.riscarica) else 0)

    if not any([a.init, a.backfill, a.delta, a.bulk, a.storico, a.stato]):
        ap.print_help()
        return

    cpv_pref = tuple(c.strip() for c in a.cpv.split(",") if c.strip()) or None
    os.makedirs(CACHE, exist_ok=True)
    cx = connetti()

    init(cx)   # idempotente: CREATE TABLE/VIEW IF NOT EXISTS
    if a.backfill:
        print(f"\n--- backfill (CPV {cpv_pref or 'tutti'}) ---")
        backfill(cx, cpv_pref)
    if a.delta:
        print(f"\n--- delta {a.delta} (CPV {cpv_pref or 'tutti'}) ---")
        delta(cx, a.delta, cpv_pref)
    if a.storico:
        da, _, al = a.storico.partition("-")
        anni = range(int(da), int(al or da) + 1)
        print(f"\n--- storico {da}-{al or da} (CPV {cpv_pref or 'tutti'})"
              f"{', zip purgati' if a.purga else ''} ---")
        storico(cx, anni, cpv_pref, purga=a.purga)
    if a.bulk:
        print("\n--- bulk ---")
        bulk(cx)
    if a.stato:
        print()
        stato(cx)
    cx.close()


if __name__ == "__main__":
    main()
