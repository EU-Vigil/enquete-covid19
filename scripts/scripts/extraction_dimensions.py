#!/root/ema/bin/python3
import sqlite3
import heapq
import itertools
import time
import fcntl
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

BASE = Path("/root/ema/export")
DST = BASE / "enquete_covid/data/dimensions.sqlite"
SOURCE = BASE / "ema_clean.db"
BATCH = 500

VACCINS = {
    "Pfizer-BioNTech": [
        42325700,110321159,131360120,134610033,
        81984179,60123556,60141237
    ],
    "Moderna": [
        40983312,107200136,134560987,
        79900912,60124330,61579746
    ],
    "AstraZeneca": [40995439],
    "Janssen": [42287887],
    "Novavax": [42287687,121095546,92217242],
    "Valneva": [60104913],
    "VidPrevtyn Beta": [62969550],
    "Bimervax": [131358626],
    "Kostaive": [54171727],
}

codes = {
    c: marque
    for marque, items in VACCINS.items()
    for c in items
}

lock = open(
    BASE / "enquete_covid/data/.dimensions.lock", "w"
)
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit("Extraction déjà active")

src = sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True)
src.execute("PRAGMA query_only=ON")

dst = sqlite3.connect(DST, timeout=60)
dst.execute("PRAGMA journal_mode=WAL")

dst.executescript("""
CREATE TABLE IF NOT EXISTS progression (
 id INTEGER PRIMARY KEY CHECK(id=1),
 dernier INTEGER NOT NULL,
 traites INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS dimensions (
 groupe TEXT NOT NULL,
 categorie TEXT NOT NULL,
 dimension TEXT NOT NULL,
 valeur TEXT NOT NULL,
 nombre INTEGER NOT NULL,
 PRIMARY KEY(groupe,categorie,dimension,valeur)
);

INSERT OR IGNORE INTO progression VALUES(1,0,0);
""")
dst.commit()

dernier, traites = dst.execute(
    "SELECT dernier,traites FROM progression WHERE id=1"
).fetchone()

print("Reprise :", dernier, traites, flush=True)

def flux(code, marque):
    sql = """
      SELECT safety_report_id
      FROM ema_icsr_substance
      INDEXED BY ema_icsr_substance_code_idx
      WHERE substance_code=? AND safety_report_id>?
      ORDER BY safety_report_id
    """
    for (rid,) in src.execute(sql, (code, dernier)):
        yield rid, marque, code

fusion = heapq.merge(*[
    flux(code, marque)
    for code, marque in codes.items()
])

def mois(date):
    if not date:
        return "INCONNU"
    try:
        d = datetime.strptime(
            date[:10], "%d/%m/%Y"
        )
        if 2002 <= d.year <= 2026:
            return d.strftime("%Y-%m")
    except (ValueError, TypeError):
        pass
    return "INCONNU"

def categories(details):
    d = (details or "").upper()
    found = set()

    if "RESULTS IN DEATH" in d or " - FATAL" in d:
        found.add("DECES")
    if "CAUSED/PROLONGED HOSPITALISATION" in d:
        found.add("HOSPITALISATION")
    if "RECOVERED/RESOLVED WITH SEQUELAE" in d:
        found.add("SEQUELLES")
    if "DISABLING" in d:
        found.add("INVALIDITE")
    if "LIFE THREATENING" in d:
        found.add("PRONOSTIC_VITAL")
    if "CONGENITAL ANOMALY" in d:
        found.add("ANOMALIE_CONGENITALE")
    if "OTHER MEDICALLY IMPORTANT CONDITION" in d:
        found.add("AUTRE_IMPORTANTE")

    return found

def traiter(batch):
    global dernier, traites

    ids = [rid for rid, _ in batch]
    marks = ",".join("?" for _ in ids)

    informations = {}

    for row in src.execute(f"""
        SELECT safety_report_id,gateway_date,
               age_group,sex,regulatory_country,
               seriousness
        FROM ema_icsr
        WHERE safety_report_id IN ({marks})
    """, ids):
        informations[row[0]] = row[1:]

    gravite = {}

    for rid, details in src.execute(f"""
        SELECT safety_report_id,reaction_details
        FROM ema_icsr_reaction
        WHERE safety_report_id IN ({marks})
    """, ids):
        gravite.setdefault(rid, set()).update(
            categories(details)
        )

    aggreg = Counter()

    for rid, produits in batch:
        marques = {m for m, _ in produits}
        formulations = {c for _, c in produits}

        groupes = (
            {"GLOBAL"}
            | {"MARQUE:" + m for m in marques}
            | {"FORMULE:" + str(c) for c in formulations}
        )

        info = informations.get(rid)
        if info is None:
            continue

        date, age, sexe, pays, serious = info
        month = mois(date)

        valeurs = {
            "MOIS": month,
            "ANNEE": month[:4] if month != "INCONNU"
                     else "INCONNU",
            "AGE": str(age).strip() if age else "INCONNU",
            "SEXE": str(sexe).strip() if sexe else "INCONNU",
            "PAYS": str(pays).strip() if pays else "INCONNU",
        }

        cats = gravite.get(rid, set()).copy()

        if cats or (serious or "").strip().lower() == "serious":
            cats.add("GRAVE")

        cats.add("TOUS")

        for groupe in groupes:
            for cat in cats:
                for dim, valeur in valeurs.items():
                    aggreg[(groupe, cat, dim, valeur)] += 1

    for (g, c, d, v), n in aggreg.items():
        dst.execute("""
            INSERT INTO dimensions VALUES(?,?,?,?,?)
            ON CONFLICT(groupe,categorie,dimension,valeur)
            DO UPDATE SET nombre=nombre+excluded.nombre
        """, (g,c,d,v,n))

    dernier = batch[-1][0]
    traites += len(batch)

    dst.execute("""
        UPDATE progression SET dernier=?,traites=?
        WHERE id=1
    """, (dernier,traites))
    dst.commit()

batch = []
start = time.monotonic()
lots = 0

for rid, items in itertools.groupby(
    fusion, key=lambda x: x[0]
):
    batch.append((
        rid,
        [(marque, code) for _, marque, code in items]
    ))

    if len(batch) >= BATCH:
        traiter(batch)
        batch.clear()
        lots += 1

        if lots % 10 == 0:
            print(
                f"Traités : {traites:,} | "
                f"{time.monotonic()-start:.0f}s",
                flush=True
            )

if batch:
    traiter(batch)

print("=== EXTRACTION TERMINÉE ===", flush=True)
print("Signalements :", traites, flush=True)
print("Résultats :", DST, flush=True)

src.close()
dst.close()
