#!/root/ema/bin/python3
import csv
import json
import sqlite3
import numpy as np
from pathlib import Path

from signal_methods import all_methods

BASE = Path("/root/ema/export")
OUT = BASE / "enquete_covid/results"
OUT.mkdir(parents=True, exist_ok=True)

ROOT = BASE / "raw_stats_cache/_evt"

meta = json.loads((ROOT / "meta.json").read_text())
pts = json.loads((ROOT / "pts.json").read_text())
subs = json.loads((ROOT / "subs.json").read_text())

N = int(meta["N"])
priors = meta["gps"]["priors"]

assert len(priors) == 5

pt_index = {
    label: i for i, label in enumerate(pts["labels"])
}
sc_index = {
    str(code): i for i, code in enumerate(subs["code"])
}

with np.load(ROOT / "pairs.npz", allow_pickle=False) as z:
    pair_sc = z["sc"]
    pair_pt = z["pt"]
    pair_n = z["n"]

# Index rapide : (substance, effet) -> nombre observé.
pairs = {
    (int(s), int(p)): int(n)
    for s, p, n in zip(pair_sc, pair_pt, pair_n)
}

con = sqlite3.connect(
    f"file:{BASE}/enquete_covid/data/covid_graves.sqlite?mode=ro",
    uri=True
)

groupes = dict(con.execute(
    "SELECT groupe, signalements FROM totaux"
))

champs = [
    "groupe", "reaction_pt", "signalements",
    "deces", "hospitalisation", "sequelles",
    "invalidite", "pronostic_vital",
    "anomalie_congenitale", "autre_importante"
]

cursor = con.execute("""
    SELECT groupe, reaction_pt, signalements,
           deces, hospitalisation, sequelles,
           invalidite, pronostic_vital,
           anomalie_congenitale, autre_importante
    FROM effets
    WHERE deces>0 OR hospitalisation>0
       OR sequelles>0 OR invalidite>0
       OR pronostic_vital>0
       OR anomalie_congenitale>0
       OR autre_importante>0
    ORDER BY groupe, reaction_pt
""")

rows = [dict(zip(champs, row)) for row in cursor]

print("Paires graves à analyser :", len(rows), flush=True)

# Vérifier les marges AVANT tout calcul.
# L'extraction Covid et l'index global doivent employer
# les mêmes identifiants, PT et règles de dédoublonnage.
anomalies = []

for r in rows:
    pt = pt_index.get(r["reaction_pt"])
    if pt is None:
        anomalies.append((r["groupe"], r["reaction_pt"],
                          "PT absent de la référence"))
        continue

    a = int(r["signalements"])
    n1j = int(groupes[r["groupe"]])
    ni1 = int(pts["n"][pt])
    b, c = n1j - a, ni1 - a
    d = N - a - b - c

    if min(a, b, c, d) < 0:
        anomalies.append((r["groupe"], r["reaction_pt"],
                          "Tableau 2x2 invalide"))
        continue

    # Contrôle indépendant sur les formulations.
    if r["groupe"].startswith("FORMULE:"):
        code = r["groupe"].split(":", 1)[1]
        si = sc_index.get(code)
        if si is None:
            anomalies.append((code, r["reaction_pt"],
                              "Formulation absente de l'index"))
        elif (
            n1j != int(subs["n"][si])
            or a != pairs.get((si, pt), 0)
        ):
            anomalies.append((code, r["reaction_pt"],
                              "Effectifs discordants"))

if anomalies:
    print("ARRÊT : incohérences détectées :", len(anomalies))
    for x in anomalies[:20]:
        print(" ", x)
    raise SystemExit(
        "Aucun CSV statistique créé. Vérifier les sources."
    )

print("Contrôle des effectifs : OK", flush=True)

# Les méthodes ROR/PRR existantes exigent des
# cellules positives. Les cas non estimables sont
# conservés et explicitement signalés.
fichier = OUT / "signaux_statistiques_covid.csv"
tmp = fichier.with_suffix(".csv.tmp")

colonnes = champs + [
    "N_global", "n_effet_global",
    "a", "b", "c", "d",
    "ror", "ror_lb", "ror_ub", "ror_signal",
    "prr", "prr_lb", "prr_ub", "prr_chi2",
    "prr_signal",
    "bcpnn", "ic", "ic025", "bcpnn_signal",
    "gps_ebgm", "gps_eb05", "gps_eb95",
    "gps_signal", "methodes_positives", "statut"
]

def valeur(x):
    return float(np.asarray(x).reshape(-1)[0])

def signal(x):
    return bool(np.asarray(x).reshape(-1)[0])

with tmp.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=colonnes)
    writer.writeheader()

    for i, r in enumerate(rows, 1):
        pt = pt_index[r["reaction_pt"]]
        a = int(r["signalements"])
        n1j = int(groupes[r["groupe"]])
        ni1 = int(pts["n"][pt])

        b = n1j - a
        c = ni1 - a
        d = N - a - b - c

        record = dict(r)
        record.update(
            N_global=N, n_effet_global=ni1,
            a=a, b=b, c=c, d=d
        )

        if min(a, b, c, d) == 0:
            record["statut"] = "non_estimable_cellule_nulle"
            record["methodes_positives"] = ""
        else:
            result = all_methods(
                np.array([a], dtype=float),
                np.array([n1j], dtype=float),
                np.array([ni1], dtype=float),
                N, priors
            )

            ro = result["ror"]
            pr = result["prr"]
            bc = result["bcpnn"]
            gp = result["gps"]

            record.update(
                ror=valeur(ro["value"]),
                ror_lb=valeur(ro["lb"]),
                ror_ub=valeur(ro["ub"]),
                ror_signal=signal(ro["signal"]),
                prr=valeur(pr["value"]),
                prr_lb=valeur(pr["lb"]),
                prr_ub=valeur(pr["ub"]),
                prr_chi2=valeur(pr["chi2"]),
                prr_signal=signal(pr["signal"]),
                bcpnn=valeur(bc["value"]),
                ic=valeur(bc["ic"]),
                ic025=valeur(bc["ic025"]),
                bcpnn_signal=signal(bc["signal"]),
                gps_ebgm=valeur(gp["value"]),
                gps_eb05=valeur(gp["lb"]),
                gps_eb95=valeur(gp["ub"]),
                gps_signal=signal(gp["signal"]),
                methodes_positives=sum(
                    signal(result[k]["signal"])
                    for k in ("ror", "prr", "bcpnn", "gps")
                ),
                statut="calcule"
            )

        writer.writerow(record)

        if i % 10000 == 0:
            print(f"Calculés : {i:,}/{len(rows):,}",
                  flush=True)

tmp.replace(fichier)
con.close()

print("TERMINÉ :", fichier, flush=True)
print("GPS : calibration globale _evt/meta.json", flush=True)
