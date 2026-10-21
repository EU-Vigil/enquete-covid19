import sqlite3
import time
from collections import defaultdict

DB = "file:/root/ema/export/ema_clean.db?mode=ro"

con = sqlite3.connect(DB, uri=True)
con.execute("PRAGMA query_only=ON")

start = time.monotonic()

codes = {
    "Pfizer-BioNTech": 42325700,
    "Moderna": 40983312,
    "AstraZeneca": 40995439,
    "Janssen": 42287887,
    "Novavax": 42287687
}

for vaccin, code in codes.items():
    ids = [
        row[0] for row in con.execute("""
            SELECT DISTINCT safety_report_id
            FROM ema_icsr_substance
            WHERE substance_code = ?
            LIMIT 1000
        """, (code,))
    ]

    counts = defaultdict(set)
    fatal = set()
    hospital = set()
    sequelae = set()

    for offset in range(0, len(ids), 500):
        batch = ids[offset:offset + 500]
        marks = ",".join("?" for _ in batch)

        rows = con.execute(f"""
            SELECT safety_report_id, reaction_pt, reaction_details
            FROM ema_icsr_reaction
            WHERE safety_report_id IN ({marks})
        """, batch)

        for rid, pt, details in rows:
            if pt:
                counts[pt].add(rid)

            d = (details or "").upper()

            if "RESULTS IN DEATH" in d or " - FATAL" in d:
                fatal.add(rid)
            if "CAUSED/PROLONGED HOSPITALISATION" in d:
                hospital.add(rid)
            if "RECOVERED/RESOLVED WITH SEQUELAE" in d:
                sequelae.add(rid)

    print(f"\n=== {vaccin} ===", flush=True)
    print("Signalements :", len(ids), flush=True)
    print("Effets distincts :", len(counts), flush=True)
    print("Décès signalés :", len(fatal), flush=True)
    print("Hospitalisations :", len(hospital), flush=True)
    print("Séquelles déclarées :", len(sequelae), flush=True)
    print("Top 5 effets :", flush=True)

    for pt, reports in sorted(
        counts.items(),
        key=lambda x: len(x[1]),
        reverse=True
    )[:5]:
        print(f"  {pt}: {len(reports)}", flush=True)

print(f"\nDurée : {time.monotonic()-start:.2f} s")
con.close()
