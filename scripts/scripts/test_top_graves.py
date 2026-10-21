import sqlite3
import time
from collections import defaultdict

DB = "file:/root/ema/export/ema_clean.db?mode=ro"

VACCINS = {
    "Pfizer-BioNTech": 42325700,
    "Moderna": 40983312,
    "AstraZeneca": 40995439,
    "Janssen": 42287887,
    "Novavax": 42287687,
}

con = sqlite3.connect(DB, uri=True)
con.execute("PRAGMA query_only=ON")

start = time.monotonic()

CATEGORIES = {
    "DECES": lambda d: (
        "RESULTS IN DEATH" in d
        or " - FATAL" in d
    ),
    "HOSPITALISATIONS": lambda d: (
        "CAUSED/PROLONGED HOSPITALISATION" in d
    ),
    "SEQUELLES": lambda d: (
        "RECOVERED/RESOLVED WITH SEQUELAE" in d
    ),
    "INVALIDITES": lambda d: (
        "DISABLING" in d
    ),
    "PRONOSTIC_VITAL": lambda d: (
        "LIFE THREATENING" in d
    ),
}

for vaccin, code in VACCINS.items():

    ids = [
        row[0] for row in con.execute("""
            SELECT DISTINCT safety_report_id
            FROM ema_icsr_substance
            WHERE substance_code = ?
            LIMIT 1000
        """, (code,))
    ]

    counts = {
        category: defaultdict(set)
        for category in CATEGORIES
    }

    totals = {
        category: set()
        for category in CATEGORIES
    }

    for offset in range(0, len(ids), 500):
        batch = ids[offset:offset + 500]
        marks = ",".join("?" for _ in batch)

        rows = con.execute(f"""
            SELECT safety_report_id, reaction_pt, reaction_details
            FROM ema_icsr_reaction
            WHERE safety_report_id IN ({marks})
        """, batch)

        for rid, pt, details in rows:
            if not pt:
                continue

            d = (details or "").upper()

            for category, detector in CATEGORIES.items():
                if detector(d):
                    totals[category].add(rid)
                    counts[category][pt].add(rid)

    print(f"\n{'=' * 65}")
    print(f"VACCIN : {vaccin}")
    print(f"Signalements de l'échantillon : {len(ids)}")

    for category in CATEGORIES:
        print(f"\n--- {category} ---")
        print(
            "Signalements concernés :",
            len(totals[category])
        )

        top = sorted(
            counts[category].items(),
            key=lambda item: (-len(item[1]), item[0])
        )[:10]

        if not top:
            print("Aucun événement identifié")
            continue

        for pt, reports in top:
            print(f"  {len(reports):>6}  {pt}")

print(f"\nDurée totale : {time.monotonic()-start:.2f} s")

con.close()
