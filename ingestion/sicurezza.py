"""Revisione di sicurezza del database (task R13).

PERCHE' UNO SCRIPT E NON UNA LETTURA A OCCHIO. Le impostazioni che contano
qui non si vedono aprendo la console di Supabase: RLS attivo ma senza policy
sembra "protetto" ed e' protetto solo finche' nessuno espone lo schema; un
GRANT ad 'anon' rimasto da una prova non compare da nessuna parte finche'
qualcuno non chiama l'API. E si degradano da sole: basta una migrazione che
crea una tabella nuova senza RLS.

Quindi si misura, e si rimisura quando serve — prima di ogni esposizione.

COSA CONTROLLA, e perche' ognuna e' la domanda giusta:

  1. RLS per tabella      senza, chiunque abbia la chiave anon legge tutto
  2. policy per tabella   RLS senza policy = nessuno legge (va bene, ma va
                          saputo: sembra un blocco, non una protezione)
  3. schemi esposti       PostgREST pubblica solo gli schemi in elenco: se
                          'radar' non c'e', l'API non lo vede affatto ed e'
                          questa la difesa vera oggi
  4. privilegi ad anon    il buco classico: un GRANT lasciato da una prova
  5. viste                una vista senza security_invoker gira coi diritti
                          di chi l'ha creata: scavalca RLS delle tabelle sotto
                          e lo scudo che si e' appena acceso non serve piu'
  6. estensioni           pg_net e simili possono chiamare l'esterno
  7. dati personali       quali colonne, e se sono quelle dichiarate lecite

Uso:
    python sicurezza.py              # il rapporto
    python sicurezza.py --breve      # solo il verdetto, per un job

Esce 0 se non c'e' niente da sistemare, 1 se c'e'.
"""

import argparse
import sys

import psycopg

from push_supabase import leggi_dsn, maschera

# Le tabelle che, se diventassero leggibili da fuori, farebbero danno. Non e'
# la stessa cosa per tutte: le scadenze sono dati aperti ANAC rielaborati,
# radar.invio invece e' il nostro lavoro commerciale.
RISERVATE = {
    "invio": "contatti PEC, testo dei messaggi, stato commerciale",
    "ente": "anagrafica con PEC e recapiti",
    "punteggio": "il giudizio sui lead: e' il prodotto",
    "punteggio_pesi": "i pesi misurati: e' il prodotto",
    "esito": "lo storico degli esiti: e' il prodotto",
    "notificato": "cosa e' gia' stato segnalato",
}

# Colonne che, se comparissero, sarebbero dati personali di persone fisiche.
# R3 le ha escluse deliberatamente: se tornano, qualcuno le ha reintrodotte
# senza rifare l'analisi di liceita'.
PERSONALI = ("nome_resp", "cogn_resp", "titolo_resp", "email_resp",
             "telefono_resp", "cf_resp", "nome_rup", "cognome_rup")


def q(cur, sql, par=()):
    cur.execute(sql, par)
    return cur.fetchall()


def rapporto(cur):
    r = {"problemi": [], "note": []}

    # ---------------------------------------------------- 1 e 2. RLS
    tab = q(cur, """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
               (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid)
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'radar' AND c.relkind = 'r'
        ORDER BY c.relname""")
    r["tabelle"] = [dict(nome=a, rls=b, forzato=c, policy=d) for a, b, c, d in tab]
    senza_rls = [t["nome"] for t in r["tabelle"] if not t["rls"]]

    # ------------------------------------------------ 3. schemi esposti
    # La difesa vera oggi: se 'radar' non e' fra gli schemi che PostgREST
    # pubblica, l'API REST non lo vede nemmeno con la chiave giusta.
    esposti = q(cur, """
        SELECT setting FROM pg_settings WHERE name = 'pgrst.db_schemas'""")
    if not esposti:
        esposti = q(cur, """
            SELECT coalesce(
              (SELECT string_agg(unnest, ',') FROM unnest(
                 string_to_array(current_setting('pgrst.db_schemas', true), ','))),
              '(non impostato: lo decide la configurazione del progetto)')""")
    r["schemi_esposti"] = esposti[0][0] if esposti else "(sconosciuto)"

    # --------------------------------------------- 4. privilegi ad anon
    # Il buco classico. Si guarda anche 'authenticated': su Supabase ha la
    # chiave che finisce nel browser di chiunque usi l'app.
    grant = q(cur, """
        SELECT grantee, table_name, string_agg(DISTINCT privilege_type, ',')
        FROM information_schema.role_table_grants
        WHERE table_schema = 'radar' AND grantee IN ('anon','authenticated','public')
        GROUP BY 1,2 ORDER BY 1,2""")
    r["grant_pubblici"] = [dict(ruolo=a, tabella=b, privilegi=c) for a, b, c in grant]

    # Anche i privilegi sullo schema: senza USAGE non si arriva alle tabelle.
    uso = q(cur, """
        SELECT grantee, privilege_type
        FROM information_schema.usage_privileges
        WHERE object_schema = 'radar' AND grantee IN ('anon','authenticated','public')
        LIMIT 20""")
    r["uso_schema"] = [dict(ruolo=a, privilegio=b) for a, b in uso]

    # ---------------------------------------------------- 5. viste
    # Il trabocchetto meno visibile di tutti. Una vista, per default, legge
    # coi diritti di chi l'ha creata — cioe' 'postgres', che ha bypassrls.
    # Risultato: si accende RLS su tutte le tabelle, ci si sente al sicuro, e
    # una singola vista esposta continua a servire tutto a chiunque.
    viste = q(cur, """
        SELECT c.relname, coalesce((SELECT option_value
                 FROM pg_options_to_table(c.reloptions)
                 WHERE option_name = 'security_invoker'), 'off')
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'radar' AND c.relkind = 'v' ORDER BY 1""")
    r["viste"] = [dict(nome=a, invoker=(b == 'on')) for a, b in viste]

    # ------------------------------------------------- 6. estensioni
    est = q(cur, """
        SELECT extname, n.nspname FROM pg_extension e
        JOIN pg_namespace n ON n.oid = e.extnamespace ORDER BY 1""")
    r["estensioni"] = [dict(nome=a, schema=b) for a, b in est]

    # -------------------------------------------- 7. dati personali
    col = q(cur, """
        SELECT table_name, column_name FROM information_schema.columns
        WHERE table_schema = 'radar' AND column_name = ANY(%s)
        ORDER BY 1,2""", (list(PERSONALI),))
    r["personali"] = [dict(tabella=a, colonna=b) for a, b in col]

    # ------------------------------------------------------ verdetto
    if senza_rls:
        r["problemi"].append(
            ("RLS spento su " + ", ".join(senza_rls),
             "Oggi non fa danno perche' lo schema non e' esposto, ma e' "
             "l'unica cosa che separa un errore di configurazione da una "
             "fuga di dati. Accendilo prima di esporre qualunque API."))
    passanti = [v["nome"] for v in r["viste"] if not v["invoker"]]
    if passanti:
        r["problemi"].append(
            ("viste che scavalcano RLS: " + ", ".join(passanti),
             "Senza security_invoker una vista legge coi diritti di chi l'ha "
             "creata, che qui ha bypassrls. Esporne una vanificherebbe RLS su "
             "tutte le tabelle sotto. Si sistema con "
             "ALTER VIEW radar.<nome> SET (security_invoker = on)."))
    pericolosi = [g for g in r["grant_pubblici"]
                  if g["ruolo"] in ("anon", "public")
                  and g["privilegi"] != "TRIGGER"]
    if pericolosi:
        r["problemi"].append(
            (f"{len(pericolosi)} privilegi concessi ad 'anon'/'public'",
             "La chiave anon di Supabase e' pubblica per definizione: "
             "finisce nel browser. Un GRANT qui vale come pubblicare i dati."))
    if r["personali"]:
        r["problemi"].append(
            ("colonne con dati personali: "
             + ", ".join(f"{c['tabella']}.{c['colonna']}" for c in r["personali"]),
             "R3 le aveva escluse. Se ci sono, va rifatta l'analisi in "
             "docs/liceita.md prima di usarle."))

    senza_policy = [t["nome"] for t in r["tabelle"] if t["rls"] and not t["policy"]]
    if senza_policy:
        r["note"].append(
            f"RLS attivo ma senza policy su {len(senza_policy)} tabelle: "
            f"nessuno legge, nemmeno per sbaglio. E' una scelta valida "
            f"finche' l'accesso passa solo dalla chiave di servizio.")
    return r


def stampa(r, breve=False):
    if breve:
        for t, _ in r["problemi"]:
            print("  ! " + t)
        print("  nessun problema" if not r["problemi"] else "")
        return

    print("\n=== revisione di sicurezza — schema radar ===\n")

    print("  tabella                    RLS   policy   contiene")
    for t in r["tabelle"]:
        que = RISERVATE.get(t["nome"], "dati ANAC rielaborati")
        rls = "si " if t["rls"] else "NO "
        print(f"  {t['nome']:24s} {rls:5s} {t['policy']:^7d}  {que[:40]}")

    print(f"\n  schemi esposti via API: {r['schemi_esposti']}")
    if "radar" not in str(r["schemi_esposti"]):
        print("    -> 'radar' non e' esposto: l'API REST non lo vede affatto.")
        print("       E' questa, oggi, la difesa vera — non RLS.")

    print("\n  privilegi a ruoli pubblici:")
    if not r["grant_pubblici"]:
        print("    nessuno. Alle tabelle si arriva solo con la chiave di servizio.")
    for g in r["grant_pubblici"]:
        print(f"    {g['ruolo']:14s} {g['tabella']:22s} {g['privilegi']}")

    print("\n  viste:")
    for v in r["viste"]:
        stato = "legge coi diritti di chi la chiama" if v["invoker"] \
            else "SCAVALCA RLS (security_invoker spento)"
        print(f"    {v['nome']:22s} {stato}")

    print("\n  estensioni installate:")
    for e in r["estensioni"]:
        segno = " <- puo' chiamare l'esterno" if e["nome"] in ("pg_net", "http") else ""
        print(f"    {e['nome']:22s} ({e['schema']}){segno}")

    print("\n  dati personali di persone fisiche:")
    print("    nessuna colonna fra quelle escluse da R3."
          if not r["personali"] else
          "\n".join(f"    {c['tabella']}.{c['colonna']}" for c in r["personali"]))

    if r["note"]:
        print()
        for n in r["note"]:
            print(f"  nota: {n}")

    print("\n  " + "-" * 66)
    if not r["problemi"]:
        print("  Niente da sistemare per come il sistema e' usato oggi.")
    else:
        print(f"  {len(r['problemi'])} cose da sistemare "
              f"PRIMA di esporre qualunque API o dashboard:\n")
        for titolo, perche in r["problemi"]:
            print(f"    - {titolo}")
            for riga in perche.split(". "):
                if riga.strip():
                    print(f"      {riga.strip().rstrip('.')}.")
            print()
    print("  " + "-" * 66 + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--breve", action="store_true")
    a = ap.parse_args()
    dsn = leggi_dsn()
    try:
        with psycopg.connect(dsn, connect_timeout=25) as pg, pg.cursor() as cur:
            r = rapporto(cur)
    except Exception as e:
        # Un traceback di psycopg puo' contenere la DSN intera.
        sys.exit("errore: " + maschera(str(e), dsn)[:300])
    stampa(r, a.breve)
    return 1 if r["problemi"] else 0


if __name__ == "__main__":
    sys.exit(main())
