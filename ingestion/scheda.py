#!/usr/bin/env python3
"""
Scheda ente (task R19).

Il documento che si legge nei dieci minuti prima di una call. Oggi quelle
informazioni ci sono tutte, ma sparse fra quattro tabelle, e ricostruirle a
mano davanti al telefono non si fa.

Cosa risponde, nell'ordine in cui serve saperlo:

  chi sono          denominazione, tipologia, PEC, sito
  quanto spendono   importo aggiudicato per anno, e se sta crescendo o calando
  chi glieli tiene  i fornitori che presidiano l'ente, per valore
  come comprano     gara o affidamento diretto, in che proporzione
  ogni quanto       durata tipica dei contratti: dice quando ripassare
  si entra?         lo storico degli esiti (R17) per questo ente
  cosa scade        le scadenze in arrivo, col punteggio di R18
  cosa gli ho detto lo storico dei nostri contatti PEC

La riga che conta piu' di tutte e' "si entra?": un ente che in tre anni non ha
mai cambiato fornitore non e' un lead, per quanto spenda.

Uso:
    python scheda.py "comune di jesi"      # cerca per nome
    python scheda.py 00292520427           # o per codice fiscale
    python scheda.py --cerca marche        # elenca gli enti che somigliano

Solo libreria standard. Legge radar.db. Lo storico dei contatti PEC vive su
Supabase, quindi da riga di comando non compare: lo aggiunge la console, che
la connessione ce l'ha gia' aperta.
"""

import argparse
import os
import sqlite3
import sys
from datetime import date

QUI = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(QUI, "radar.db")

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

APERTA = "(esito = 'nuovo_fornitore' OR COALESCE(contendibile, 0) = 1)"


def eur(v):
    if not v:
        return "—"
    if v >= 1e6:
        return f"{v / 1e6:,.1f} M€"
    if v >= 1e3:
        return f"{v / 1e3:,.0f} k€"
    return f"{v:,.0f} €"


def cerca(cx, testo):
    """Enti che somigliano, piu' grandi per primi.

    Si cerca sia in `cig` sia in IndicePA: la denominazione ANAC e quella
    IndicePA spesso non coincidono ('COMUNE DI JESI' contro 'Comune di Jesi')
    e cercare in una sola delle due fa sparire meta' degli enti."""
    t = f"%{testo.strip().lower()}%"
    return cx.execute("""
        SELECT c.cf_amministrazione_appaltante AS cf,
               max(c.denominazione_amministrazione_appaltante) AS ente,
               max(k.denominazione_ipa) AS ipa,
               max(c.provincia) AS prov,
               count(*) AS lotti
        FROM cig c
        LEFT JOIN ente_contatti k ON k.cf_ente = c.cf_amministrazione_appaltante
        WHERE c.cf_amministrazione_appaltante IS NOT NULL
          AND (lower(c.denominazione_amministrazione_appaltante) LIKE ?
               OR lower(k.denominazione_ipa) LIKE ?
               OR c.cf_amministrazione_appaltante = ?)
        GROUP BY cf ORDER BY lotti DESC LIMIT 25""",
        (t, t, testo.strip())).fetchall()


def scheda(cx, cf):
    """Tutto quello che sappiamo su un ente. Ritorna un dict."""
    cx.row_factory = sqlite3.Row
    d = {"cf": cf}

    r = cx.execute("""
        SELECT max(c.denominazione_amministrazione_appaltante) ente,
               max(c.provincia) prov, count(*) lotti,
               sum(c.importo_lotto) bandito,
               min(c.data_pubblicazione) primo, max(c.data_pubblicazione) ultimo
        FROM cig c WHERE c.cf_amministrazione_appaltante = ?""", (cf,)).fetchone()
    if not r or not r["lotti"]:
        return None
    d.update(dict(r))

    k = cx.execute("SELECT denominazione_ipa, pec, mail_alt, sito_istituzionale,"
                   " tipologia_amm, comune, regione FROM ente_contatti "
                   "WHERE cf_ente = ?", (cf,)).fetchone()
    d["contatti"] = dict(k) if k else {}

    # Spesa per anno: la tendenza conta piu' del totale. Un ente che spendeva
    # 2 M€ e ora ne spende 300 k€ ha centralizzato altrove, e chiamarlo e'
    # tempo perso.
    d["per_anno"] = [dict(r) for r in cx.execute("""
        SELECT substr(c.data_pubblicazione,1,4) anno, count(*) n,
               sum(a.importo_aggiudicazione) importo
        FROM cig c LEFT JOIN aggiudicazioni a ON a.cig = c.cig
        WHERE c.cf_amministrazione_appaltante = ?
          AND c.data_pubblicazione >= '2021'
        GROUP BY anno ORDER BY anno""", (cf,))]

    d["fornitori"] = [dict(r) for r in cx.execute("""
        SELECT g.codice_fiscale cf, max(g.denominazione) nome,
               count(DISTINCT g.cig) contratti,
               sum(a.importo_aggiudicazione) valore,
               max(c.data_pubblicazione) ultimo
        FROM cig c
        JOIN aggiudicatari g  ON g.cig = c.cig
        LEFT JOIN aggiudicazioni a ON a.id_aggiudicazione = g.id_aggiudicazione
        WHERE c.cf_amministrazione_appaltante = ?
        GROUP BY g.codice_fiscale
        ORDER BY valore DESC NULLS LAST, contratti DESC LIMIT 12""", (cf,))]

    d["procedure"] = [dict(r) for r in cx.execute("""
        SELECT coalesce(tipo_scelta_contraente,'non dichiarata') tipo,
               count(*) n, sum(importo_lotto) importo
        FROM cig WHERE cf_amministrazione_appaltante = ?
        GROUP BY tipo ORDER BY n DESC LIMIT 8""", (cf,))]

    r = cx.execute("""
        SELECT count(*) n,
               avg(julianday(v.data_termine_contrattuale)
                   - julianday(v.data_stipula_contratto)) giorni
        FROM avvio_contratto v JOIN cig c ON c.cig = v.cig
        WHERE c.cf_amministrazione_appaltante = ?
          AND v.data_stipula_contratto > '2000'
          AND v.data_termine_contrattuale > v.data_stipula_contratto""",
        (cf,)).fetchone()
    d["durata"] = dict(r) if r else {}

    r = cx.execute(f"""
        SELECT count(*) n, sum({APERTA}) aperte,
               sum(esito = 'rinnovato') rinnovi,
               sum(esito = 'nuovo_fornitore') cambi,
               sum(esito = 'nessun_seguito') senza
        FROM esito WHERE cf_ente = ?""", (cf,)).fetchone()
    d["esiti"] = dict(r) if r and r["n"] else {}

    d["scadenze"] = [dict(r) for r in cx.execute("""
        SELECT cig, oggetto_lotto, categoria, data_termine_contrattuale scad,
               importo_aggiudicazione importo, fornitore_uscente,
               punteggio, prob_apertura
        FROM v_scadenze_contattabili
        WHERE cf_amministrazione_appaltante = ?
        ORDER BY punteggio DESC NULLS LAST, data_termine_contrattuale
        LIMIT 30""", (cf,))]
    return d


# ------------------------------------------------------------- stampa
def stampa(d, invii=None):
    c = d.get("contatti") or {}
    nome = c.get("denominazione_ipa") or d.get("ente") or "?"
    print(f"\n{'=' * 74}")
    print(f"  {nome}")
    print(f"{'=' * 74}\n")
    riga = [x for x in (c.get("tipologia_amm"), c.get("comune") or d.get("prov"),
                        c.get("regione")) if x]
    if riga:
        print("  " + " · ".join(riga))
    if c.get("pec"):
        print(f"  PEC   {c['pec']}")
    if c.get("sito_istituzionale"):
        print(f"  web   {c['sito_istituzionale']}")
    print(f"  CF    {d['cf']}")

    print(f"\n  {d['lotti']:,} lotti IT dal {(d.get('primo') or '?')[:7]} "
          f"al {(d.get('ultimo') or '?')[:7]}  ·  bandito {eur(d.get('bandito'))}")

    # --- si entra?
    e = d.get("esiti") or {}
    print(f"\n  {'-' * 70}\n  SI ENTRA?")
    if not e.get("n"):
        print("    Nessun contratto di questo ente e' ancora scaduto nel "
              "periodo osservato:\n    non sappiamo dire come si comporta.")
    else:
        q = (e["aperte"] or 0) / e["n"] * 100
        print(f"    {e['aperte'] or 0} porte aperte su {e['n']} scadenze "
              f"osservate — {q:.0f}%")
        print(f"    {e['cambi'] or 0} cambi di fornitore · "
              f"{e['rinnovi'] or 0} rinnovi allo stesso · "
              f"{e['senza'] or 0} senza seguito visibile")
        if q >= 15:
            print("    -> ente che si muove: vale la pena insistere.")
        elif q >= 5:
            print("    -> nella media (il tasso nazionale e' 4,7%).")
        else:
            print("    -> chiuso: rinnova quasi sempre a chi ha gia'. "
                  "Priorita' bassa,\n       per quanto spenda.")

    # --- come comprano
    print(f"\n  {'-' * 70}\n  COME COMPRANO")
    tot = sum(p["n"] for p in d["procedure"]) or 1
    diretti = sum(p["n"] for p in d["procedure"]
                  if "AFFIDAMENTO DIRETTO" in (p["tipo"] or "").upper())
    for p in d["procedure"][:5]:
        print(f"    {p['n']:>5,}  {(p['tipo'] or '?')[:52]:54s} "
              f"{p['n'] / tot * 100:4.0f}%")
    print(f"\n    {diretti / tot * 100:.0f}% per affidamento diretto: "
          + ("qui non serve vincere una gara,\n    serve essere conosciuti "
             "prima." if diretti / tot > 0.7 else
             "una quota di gare vere c'e'."))

    # --- ogni quanto
    du = d.get("durata") or {}
    if du.get("giorni"):
        mesi = du["giorni"] / 30.4
        print(f"\n  {'-' * 70}\n  OGNI QUANTO")
        print(f"    Durata media dei contratti: {mesi:.0f} mesi "
              f"(su {du['n']:,} con date note).")
        print(f"    Se oggi non c'e' finestra, si ripassa fra circa "
              f"{max(3, round(mesi / 2))} mesi.")

    # --- quanto spendono
    if d["per_anno"]:
        print(f"\n  {'-' * 70}\n  QUANTO SPENDONO")
        mx = max((a["importo"] or 0) for a in d["per_anno"]) or 1
        for a in d["per_anno"]:
            imp = a["importo"] or 0
            print(f"    {a['anno']}  {a['n']:>4} lotti  {eur(imp):>10s}  "
                  + "#" * int(imp / mx * 34))

    # --- chi glieli tiene
    print(f"\n  {'-' * 70}\n  CHI GLIELI TIENE")
    for f in d["fornitori"][:8]:
        print(f"    {eur(f['valore']):>10s}  {f['contratti']:>3} contr.  "
              f"{(f['nome'] or '?')[:44]:46s} ultimo {(f['ultimo'] or '?')[:7]}")

    # --- cosa scade
    print(f"\n  {'-' * 70}\n  COSA SCADE")
    if not d["scadenze"]:
        print("    Niente in scadenza fra quelli che vediamo.")
    for s in d["scadenze"][:10]:
        pt = s["punteggio"]
        print(f"    [{('  -' if pt is None else f'{pt:3d}')}]  {s['scad']}  "
              f"{eur(s['importo']):>10s}  {(s['categoria'] or '?')[:24]:26s}")
        print(f"           {(s['oggetto_lotto'] or '')[:64]}")
        print(f"           uscente: {(s['fornitore_uscente'] or '?')[:52]}")

    # --- contatti
    if invii is not None:
        print(f"\n  {'-' * 70}\n  COSA GLI ABBIAMO GIA' DETTO")
        if not invii:
            print("    Mai contattato.")
        for i in invii:
            print(f"    {i.get('lotto')}#{i.get('n')}  {i.get('stato')}  "
                  f"{i.get('inviata') or ''}  {(i.get('note') or '')[:44]}")
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("chi", nargs="?", help="nome parziale o codice fiscale")
    ap.add_argument("--cerca", metavar="TESTO")
    a = ap.parse_args()
    if not (a.chi or a.cerca):
        ap.print_help()
        return
    if not os.path.exists(DB):
        sys.exit(f"{DB} non esiste.")
    cx = sqlite3.connect(DB)
    cx.row_factory = sqlite3.Row

    testo = a.cerca or a.chi
    trovati = cerca(cx, testo)
    if not trovati:
        sys.exit(f"nessun ente somiglia a '{testo}'.")

    if a.cerca or len(trovati) > 1:
        print(f"\n{len(trovati)} enti somigliano a '{testo}':\n")
        for t in trovati:
            nome = t["ipa"] or t["ente"] or "?"
            print(f"  {t['lotti']:>5,} lotti  {t['cf']:<16s} "
                  f"{nome[:46]:48s} {(t['prov'] or '')[:14]}")
        if a.cerca:
            print("\nPer la scheda: python scheda.py <codice fiscale>\n")
            return
        print(f"\n-> mostro il primo. Per un altro: "
              f"python scheda.py <codice fiscale>\n")

    d = scheda(cx, trovati[0]["cf"])
    if d:
        stampa(d)
    cx.close()


if __name__ == "__main__":
    main()
