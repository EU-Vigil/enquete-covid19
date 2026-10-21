#!/root/ema/bin/python3
import sqlite3
import heapq
import itertools
import time
import fcntl
import sys
from pathlib import Path
from collections import Counter
from datetime import datetime

BASE = Path("/root/ema/export")
DATA = BASE / "enquete_covid/data"
SOURCE = BASE / "ema_clean.db"
REFERENCE = DATA / "covid_graves.sqlite"
DEST = DATA / "effets_dimensions.sqlite"

BATCH_SIZE = 400

VACCINS = {
    "Pfizer-BioNTech": [
        42325700, 110321159, 131360120, 134610033,
        81984179, 60123556, 60141237
    ],
    "Moderna": [
        40983312, 107200136, 134560987,
        79900912, 60124330, 61579746
    ],
    "AstraZeneca": [40995439],
    "Janssen": [42287887],
    "Novavax": [42287687, 121095546, 92217242],
    "Valneva": [60104913],
    "VidPrevtyn Beta": [62969550],
    "Bimervax": [131358626],
    "Kostaive": [54171727],
}

CODES = {
    code: marque
    for marque, liste in VACCINS.items()
    for code in liste
}

lockfile = open(DATA / ".effets_dimensions.lock", "w")
try:
    fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit("Une extraction est déjà active.")

def readonly(path):
    connection = sqlite3.connect(
        f"file:{path}?mode=ro", uri=True, timeout=60
    )
    connection.execute("PRAGMA query_only=ON")
    return connection

ref = readonly(REFERENCE)

# Ensemble exact des effets retenus par groupe,
# issu de l'extraction scientifiquement contrôlée.
selected = {}

for group, pt in ref.execute("""
    SELECT groupe, reaction_pt
    FROM effets
    WHERE deces>0 OR hospitalisation>0
       OR sequelles>0 OR invalidite>0
       OR pronostic_vital>0
       OR anomalie_congenitale>0
       OR autre_importante>0
"""):
    selected.setdefault(group, set()).add(pt)

print(
    "Couples groupe-effet retenus :",
    sum(map(len, selected.values())),
    flush=True
)

src = readonly(SOURCE)

dst = sqlite3.connect(DEST, timeout=120)
dst.execute("PRAGMA journal_mode=WAL")
dst.execute("PRAGMA synchronous=NORMAL")

dst.executescript("""
CREATE TABLE IF NOT EXISTS progression (
    id INTEGER PRIMARY KEY CHECK(id=1),
    dernier_id INTEGER NOT NULL,
    traites INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS repartitions (
    groupe TEXT NOT NULL,
    reaction_pt TEXT NOT NULL,
    categorie TEXT NOT NULL,
    dimension TEXT NOT NULL,
    valeur TEXT NOT NULL,
    nombre INTEGER NOT NULL,
    PRIMARY KEY (
        groupe, reaction_pt, categorie,
        dimension, valeur
    )
);

INSERT OR IGNORE INTO progression VALUES (1, 0, 0);
""")
dst.commit()

dernier, traites = dst.execute("""
    SELECT dernier_id, traites
    FROM progression WHERE id=1
""").fetchone()

print(
    "Reprise depuis ID :", dernier,
    "| signalements traités :", traites,
    flush=True
)

# Une connexion de lecture par substance.
# Les curseurs ne perturbent ainsi pas les autres
# requêtes exécutées sur la base source.
flux_connections = []

def flux(code, marque):
    con = readonly(SOURCE)
    flux_connections.append(con)

    for (rid,) in con.execute("""
        SELECT safety_report_id
        FROM ema_icsr_substance
        INDEXED BY ema_icsr_substance_code_idx
        WHERE substance_code=?
          AND safety_report_id>?
        ORDER BY safety_report_id
    """, (code, dernier)):
        yield int(rid), marque, code

fusion = heapq.merge(*(
    flux(code, marque)
    for code, marque in CODES.items()
))

def categories(details):
    d = str(details or "").upper()
    result = set()

    if "RESULTS IN DEATH" in d or " - FATAL" in d:
        result.add("DECES")

    if "CAUSED/PROLONGED HOSPITALISATION" in d:
        result.add("HOSPITALISATION")

    if "RECOVERED/RESOLVED WITH SEQUELAE" in d:
        result.add("SEQUELLES")

    if "DISABLING" in d:
        result.add("INVALIDITE")

    if "LIFE THREATENING" in d:
        result.add("PRONOSTIC_VITAL")

    if "CONGENITAL ANOMALY" in d:
        result.add("ANOMALIE_CONGENITALE")

    if "OTHER MEDICALLY IMPORTANT CONDITION" in d:
        result.add("AUTRE_IMPORTANTE")

    return result

def date_mois(value):
    try:
        date = datetime.strptime(
            str(value)[:10], "%d/%m/%Y"
        )
        if 2002 <= date.year <= 2026:
            return date.strftime("%Y-%m")
    except (ValueError, TypeError):
        pass
    return "INCONNU"

UPSERT = """
INSERT INTO repartitions (
    groupe, reaction_pt, categorie,
    dimension, valeur, nombre
)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT (
    groupe, reaction_pt, categorie,
    dimension, valeur
)
DO UPDATE SET nombre=nombre+excluded.nombre
"""

def traiter(batch):
    global dernier, traites

    ids = [rid for rid, _ in batch]
    marks = ",".join("?" for _ in ids)

    infos = {
        rid: (date, age, sexe, pays)
        for rid, date, age, sexe, pays
        in src.execute(f"""
            SELECT safety_report_id, gateway_date,
                   age_group, sex, regulatory_country
            FROM ema_icsr
            WHERE safety_report_id IN ({marks})
        """, ids)
    }

    if len(infos) != len(ids):
        raise RuntimeError(
            "Métadonnées manquantes : "
            f"{len(infos)} sur {len(ids)}"
        )

    # Une même réaction peut apparaître plusieurs fois
    # pour un signalement : union des critères de
    # gravité plutôt que double comptage.
    reactions = {}

    for rid, pt, details in src.execute(f"""
        SELECT safety_report_id,
               reaction_pt, reaction_details
        FROM ema_icsr_reaction
        WHERE safety_report_id IN ({marks})
    """, ids):
        if not pt:
            continue

        key = (rid, pt)

        if key not in reactions:
            reactions[key] = set()

        reactions[key].update(categories(details))

    par_rapport = {}

    for (rid, pt), cats in reactions.items():
        par_rapport.setdefault(rid, []).append(
            (pt, cats)
        )

    compteurs = Counter()

    for rid, substances in batch:
        groupes = {"GLOBAL"}

        for marque, code in substances:
            groupes.add("MARQUE:" + marque)
            groupes.add("FORMULE:" + str(code))

        date, age, sexe, pays = infos[rid]

        valeurs = {
            "MOIS": date_mois(date),
            "AGE": str(age).strip() if age else "INCONNU",
            "SEXE": str(sexe).strip() if sexe else "INCONNU",
            "PAYS": str(pays).strip() if pays else "INCONNU"
        }

        for pt, cats in par_rapport.get(rid, []):
            for groupe in groupes:
                if pt not in selected.get(groupe, ()):
                    continue

                for cat in ("TOUS", *sorted(cats)):
                    for dimension, valeur in valeurs.items():
                        compteurs[
                            (groupe, pt, cat, dimension, valeur)
                        ] += 1

    dst.executemany(
        UPSERT,
        (
            (g, pt, cat, dim, val, n)
            for (g, pt, cat, dim, val), n
            in compteurs.items()
        )
    )

    dernier = batch[-1][0]
    traites += len(batch)

    dst.execute("""
        UPDATE progression
        SET dernier_id=?, traites=?
        WHERE id=1
    """, (dernier, traites))

    dst.commit()

debut = time.monotonic()
batch = []
n_lots = 0

for rid, elements in itertools.groupby(
    fusion, key=lambda x: x[0]
):
    substances = [
        (marque, code)
        for _, marque, code in elements
    ]

    batch.append((rid, substances))

    if len(batch) >= BATCH_SIZE:
        traiter(batch)
        batch.clear()
        n_lots += 1

        if n_lots % 25 == 0:
            print(
                f"Signalements : {traites:,} "
                f"| durée : {time.monotonic()-debut:.0f}s",
                flush=True
            )

if batch:
    traiter(batch)

print("=== EXTRACTION TERMINÉE ===", flush=True)
print("Signalements :", traites, flush=True)
print("Fichier :", DEST, flush=True)

for con in flux_connections:
    con.close()

src.close()
ref.close()
dst.close()
