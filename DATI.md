# Dati: licenze e cosa si può ridistribuire

Il codice di questo repository è MIT. **I dati che produce no.** La distinzione è importante
e vale la pena leggerla prima di pubblicare qualsiasi cosa che esca da questi script.

## Le fonti

| Fonte | Licenza | Cosa comporta |
|---|---|---|
| [ANAC — dati aperti appalti](https://dati.anticorruzione.it) | **CC BY-SA 4.0** | attribuzione **e ShareAlike** |
| [IndicePA](https://indicepa.gov.it) | CC BY 4.0 | solo attribuzione |

Il punto delicato è lo **ShareAlike** di ANAC. Ridistribuire il dataset, o un'elaborazione che
ne conserva la sostanza, obbliga a rilasciare il derivato sotto la stessa licenza. Non è un
divieto: è una condizione, e va soddisfatta consapevolmente, non per distrazione.

Per questo il repository **non contiene** né il database, né estratti, né liste di enti, né i
file generati. Solo il codice che li produce.

## Cosa non finisce qui dentro

- `ingestion/radar.db` — il database (~300 MB di record ANAC)
- `ingestion/recon_out/` — gli ZIP scaricati
- `docs/pec*/` — le lettere PEC generate
- `docs/lista-outreach.csv`, `docs/console-dati.json` — estratti derivati
- `ingestion/.env.local` — credenziali

Sono tutti in `.gitignore`. Se cloni e lanci gli script, li ricrei in locale.

## Dati personali

Gli indirizzi PEC delle pubbliche amministrazioni sono pubblici per obbligo di legge e stanno
su IndicePA. Questo non rende automaticamente lecito qualsiasi uso: una lista compilata,
filtrata e profilata è una cosa diversa dal registro da cui viene.

Le scelte fatte in questo progetto:

- si contattano **enti**, non persone. Nomi e ruoli dei RUP non vengono memorizzati.
- si escludono i fornitori che sono persone fisiche (`fornitore_persona_fisica = 0`): ditte
  individuali e professionisti sono persone, e trattarli come aziende è sbagliato.
- la console operativa ascolta **solo su 127.0.0.1**, perché mostra PEC e dati di contatto.

Il ragionamento completo, con le basi giuridiche, sta in [docs/liceita.md](docs/liceita.md).

## Se pubblichi qualcosa che esce da qui

1. cita ANAC e IndicePA come fonti;
2. se il tuo output conserva la sostanza del dataset ANAC, rilascialo in CC BY-SA 4.0;
3. un'elaborazione statistica aggregata è un'altra cosa da una copia del dataset — ma la
   linea non è netta, e in caso di dubbio conviene lo ShareAlike.
