#!/root/ema/bin/python3
import sqlite3
from collections import defaultdict

BASE = "/root/ema/export/enquete_covid/data/"

def ouvrir(nom):
    return sqlite3.connect(
        "file:" + BASE + nom + "?mode=ro",
        uri=True
    )

ref = ouvrir("covid_graves.sqlite")
dim = ouvrir("effets_dimensions.sqlite")

categories = {
    "TOUS": "signalements",
    "DECES": "deces",
    "HOSPITALISATION": "hospitalisation",
    "SEQUELLES": "sequelles",
    "INVALIDITE": "invalidite",
    "PRONOSTIC_VITAL": "pronostic_vital",
    "ANOMALIE_CONGENITALE": "anomalie_congenitale",
    "AUTRE_IMPORTANTE": "autre_importante"
}

colonnes = list(categories.values())

attendus = {}
for row in ref.execute("""
    SELECT groupe,reaction_pt,signalements,deces,
           hospitalisation,sequelles,invalidite,
           pronostic_vital,anomalie_congenitale,
           autre_importante
    FROM effets
    WHERE deces>0 OR hospitalisation>0
       OR sequelles>0 OR invalidite>0
       OR pronostic_vital>0
       OR anomalie_congenitale>0
       OR autre_importante>0
"""):
    attendus[(row[0],row[1])] = dict(zip(colonnes,row[2:]))

print("Couples de référence :",len(attendus),flush=True)

verifies = set()
discordances = []
compteur = 0

for groupe,pt,cat,total in dim.execute("""
    SELECT groupe,reaction_pt,categorie,SUM(nombre)
    FROM repartitions
    WHERE dimension='AGE'
    GROUP BY groupe,reaction_pt,categorie
"""):
    cle = (groupe,pt)
    compteur += 1

    if cle not in attendus:
        discordances.append((groupe,pt,cat,"couple inattendu",total))
        continue

    verifies.add(cle)
    colonne = categories.get(cat)

    if colonne is None:
        discordances.append((groupe,pt,cat,"catégorie inconnue",total))
    else:
        attendu = attendus[cle][colonne]
        if total != attendu:
            discordances.append(
                (groupe,pt,cat,attendu,total)
            )

    if compteur % 100000 == 0:
        print("Catégories contrôlées :",compteur,flush=True)

absents = set(attendus) - verifies

print("\n=== VALIDATION FINALE ===")
print("Couples attendus :",len(attendus))
print("Couples présents :",len(verifies))
print("Catégories examinées :",compteur)
print("Couples absents :",len(absents))
print("Discordances :",len(discordances))

for x in discordances[:25]:
    print("DIFFÉRENCE :",x)
for x in list(absents)[:10]:
    print("ABSENT :",x)

if not discordances and not absents:
    print("VALIDATION RÉUSSIE")
else:
    print("VALIDATION ÉCHOUÉE : correction nécessaire")

ref.close()
dim.close()
