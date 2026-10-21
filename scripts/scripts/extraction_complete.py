#!/root/ema/bin/python3
import sqlite3
import heapq
import itertools
import time
import sys
import fcntl
from collections import Counter
from pathlib import Path

ROOT = Path("/root/ema/export/enquete_covid")
SOURCE = "/root/ema/export/ema_clean.db"
OUTPUT = ROOT / "data" / "covid_graves.sqlite"
BATCH_SIZE = 250

VACCINS = {
    "Pfizer-BioNTech": [
        42325700,110321159,131360120,134610033,
        81984179,60123556,60141237
    ],
    "Moderna": [
        40983312,107200136,134560987,79900912,
        60124330,61579746
    ],
    "AstraZeneca": [40995439],
    "Janssen": [42287887],
    "Novavax": [42287687,121095546,92217242],
    "Valneva": [60104913],
    "VidPrevtyn Beta": [62969550],
    "Bimervax": [131358626],
    "Kostaive": [54171727],
}

CODES = {
    code: marque
    for marque, codes in VACCINS.items()
    for code in codes
}

CATEGORIES = (
    "deces", "hospitalisation", "sequelles",
    "invalidite", "pronostic_vital",
    "anomalie_congenitale", "autre_importante"
)

def indicateurs(details):
    d = (details or "").upper()

    return {
        "deces": (
            "RESULTS IN DEATH" in d
            or " - FATAL" in d
        ),
        "hospitalisation":
            "CAUSED/PROLONGED HOSPITALISATION" in d,
        "sequelles":
            "RECOVERED/RESOLVED WITH SEQUELAE" in d,
        "invalidite": "DISABLING" in d,
        "pronostic_vital": "LIFE THREATENING" in d,
        "anomalie_congenitale": "CONGENITAL ANOMALY" in d,
        "autre_importante":
            "OTHER MEDICALLY IMPORTANT CONDITION" in d,
    }

ROOT.joinpath("data").mkdir(parents=True, exist_ok=True)

# Empêcher deux extractions simultanées.
lock = open(ROOT / "data" / ".extraction.lock", "w")
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit("Extraction déjà en cours.")

src = sqlite3.connect(
    f"file:{SOURCE}?mode=ro", uri=True
)
src.execute("PRAGMA query_only=ON")

out = sqlite3.connect(OUTPUT, timeout=60)
out.execute("PRAGMA journal_mode=WAL")
out.execute("PRAGMA synchronous=FULL")

out.executescript("""
CREATE TABLE IF NOT EXISTS progression (
    id INTEGER PRIMARY KEY CHECK(id=1),
    dernier_signalement INTEGER NOT NULL,
    traites INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS totaux (
    groupe TEXT PRIMARY KEY,
    signalements INTEGER NOT NULL DEFAULT 0,
    graves INTEGER NOT NULL DEFAULT 0,
    deces INTEGER NOT NULL DEFAULT 0,
    hospitalisation INTEGER NOT NULL DEFAULT 0,
    sequelles INTEGER NOT NULL DEFAULT 0,
    invalidite INTEGER NOT NULL DEFAULT 0,
    pronostic_vital INTEGER NOT NULL DEFAULT 0,
    anomalie_congenitale INTEGER NOT NULL DEFAULT 0,
    autre_importante INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS effets (
    groupe TEXT NOT NULL,
    reaction_pt TEXT NOT NULL,
    signalements INTEGER NOT NULL DEFAULT 0,
    deces INTEGER NOT NULL DEFAULT 0,
    hospitalisation INTEGER NOT NULL DEFAULT 0,
    sequelles INTEGER NOT NULL DEFAULT 0,
    invalidite INTEGER NOT NULL DEFAULT 0,
    pronostic_vital INTEGER NOT NULL DEFAULT 0,
    anomalie_congenitale INTEGER NOT NULL DEFAULT 0,
    autre_importante INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(groupe,reaction_pt)
);
""")

out.execute("""
INSERT OR IGNORE INTO progression
VALUES (1, 0, 0)
""")
out.commit()

dernier, total = out.execute("""
SELECT dernier_signalement, traites
FROM progression WHERE id=1
""").fetchone()

print(
    "Reprise après :", dernier,
    "| déjà traités :", total,
    flush=True
)

# Les 22 index sont parcourus dans l'ordre des signalements.
# On fusionne les résultats sans stocker les 2,3 millions d'ID en RAM.
def flux(code, marque):
    sql = """
        SELECT safety_report_id
        FROM ema_icsr_substance
        INDEXED BY ema_icsr_substance_code_idx
        WHERE substance_code=?
          AND safety_report_id>?
        ORDER BY safety_report_id
    """
    for (rid,) in src.execute(sql, (code, dernier)):
        yield (rid, marque, code)

streams = [
    flux(code, marque)
    for code, marque in CODES.items()
]

fusion = heapq.merge(*streams)

def enregistrer(batch):
    global dernier, total

    ids = [rid for rid, _ in batch]
    placeholders = ",".join("?" for _ in ids)

    # Chaque réaction est récupérée par l'index de signalement.
    reactions = {}

    sql = f"""
        SELECT safety_report_id,reaction_pt,reaction_details
        FROM ema_icsr_reaction
        WHERE safety_report_id IN ({placeholders})
    """

    for rid, pt, details in src.execute(sql, ids):
        reactions.setdefault(rid, []).append((pt, details))

    graves_info = {}

    sql = f"""
        SELECT safety_report_id,seriousness
        FROM ema_icsr
        WHERE safety_report_id IN ({placeholders})
    """

    for rid, seriousness in src.execute(sql, ids):
        graves_info[rid] = (
            (seriousness or "").strip().lower() == "serious"
        )

    totals = Counter()
    effets = Counter()

    for rid, produits in batch:
        marques = {m for m, c in produits}
        formules = {c for m, c in produits}

        groupes = (
            {"GLOBAL"}
            | {"MARQUE:" + m for m in marques}
            | {"FORMULE:" + str(c) for c in formules}
        )

        # Agrégation au niveau du signalement.
        cats_globales = set()
        pts = {}

        for pt, details in reactions.get(rid, []):
            flags = indicateurs(details)
            actives = {
                c for c, present in flags.items() if present
            }

            cats_globales.update(actives)

            if pt and pt.strip():
                pts.setdefault(pt.strip(), set()).update(actives)

        grave = graves_info.get(rid, False) or bool(cats_globales)

        for groupe in groupes:
            totals[(groupe, "signalements")] += 1

            if grave:
                totals[(groupe, "graves")] += 1

            for categorie in cats_globales:
                totals[(groupe, categorie)] += 1

            for pt, cats in pts.items():
                effets[(groupe, pt, "signalements")] += 1

                for categorie in cats:
                    effets[(groupe, pt, categorie)] += 1

    # Une seule transaction par lot, y compris le checkpoint.
    groupes = {k[0] for k in totals}

    for groupe in groupes:
        valeurs = [
            totals[(groupe, "signalements")],
            totals[(groupe, "graves")],
        ] + [totals[(groupe, c)] for c in CATEGORIES]

        out.execute("""
            INSERT INTO totaux VALUES
            (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(groupe) DO UPDATE SET
            signalements=signalements+excluded.signalements,
            graves=graves+excluded.graves,
            deces=deces+excluded.deces,
            hospitalisation=hospitalisation+excluded.hospitalisation,
            sequelles=sequelles+excluded.sequelles,
            invalidite=invalidite+excluded.invalidite,
            pronostic_vital=pronostic_vital+excluded.pronostic_vital,
            anomalie_congenitale=anomalie_congenitale+excluded.anomalie_congenitale,
            autre_importante=autre_importante+excluded.autre_importante
        """, (groupe, *valeurs))

    couples = {(g, pt) for g, pt, c in effets}

    for groupe, pt in couples:
        valeurs = [
            effets[(groupe, pt, "signalements")]
        ] + [
            effets[(groupe, pt, c)] for c in CATEGORIES
        ]

        out.execute("""
            INSERT INTO effets VALUES
            (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(groupe,reaction_pt) DO UPDATE SET
            signalements=signalements+excluded.signalements,
            deces=deces+excluded.deces,
            hospitalisation=hospitalisation+excluded.hospitalisation,
            sequelles=sequelles+excluded.sequelles,
            invalidite=invalidite+excluded.invalidite,
            pronostic_vital=pronostic_vital+excluded.pronostic_vital,
            anomalie_congenitale=anomalie_congenitale+excluded.anomalie_congenitale,
            autre_importante=autre_importante+excluded.autre_importante
        """, (groupe, pt, *valeurs))

    dernier = batch[-1][0]
    total += len(batch)

    out.execute("""
        UPDATE progression
        SET dernier_signalement=?, traites=?
        WHERE id=1
    """, (dernier, total))

    out.commit()

batch = []
start = time.monotonic()
lots = 0

for rid, items in itertools.groupby(fusion, key=lambda x: x[0]):
    produits = [(marque, code) for _, marque, code in items]
    batch.append((rid, produits))

    if len(batch) >= BATCH_SIZE:
        enregistrer(batch)
        batch.clear()
        lots += 1

        if lots % 10 == 0:
            print(
                f"Traités : {total:,} | "
                f"dernier ID : {dernier} | "
                f"durée : {time.monotonic()-start:.0f}s",
                flush=True
            )

if batch:
    enregistrer(batch)

print("\n=== EXTRACTION TERMINÉE ===", flush=True)
print("Signalements distincts :", total, flush=True)
print("Base résultats :", OUTPUT, flush=True)

src.close()
out.close()
