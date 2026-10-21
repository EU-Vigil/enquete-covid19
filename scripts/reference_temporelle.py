#!/root/ema/bin/python3
import sqlite3, json, time, fcntl, sys
from pathlib import Path
from collections import Counter

ROOT = Path("/root/ema/export")
DATA = ROOT / "enquete_covid/data"
OUT = DATA / "reference_temporelle.sqlite"
SOURCE = ROOT / "ema_clean.db"
STEP = 200000

lock = open(DATA / ".reference_temporelle.lock", "w")
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit("Calcul déjà actif")

def ro(p):
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    c.execute("PRAGMA query_only=ON")
    return c

def month(s):
    if not s or len(s) < 10:
        return None
    d = s[:10]
    if (len(d) == 10 and d[2] == "/" and d[5] == "/"
            and d[:2].isdigit() and d[3:5].isdigit()
            and d[6:10].isdigit()
            and 1 <= int(d[3:5]) <= 12
            and 2002 <= int(d[6:10]) <= 2026):
        return d[6:10] + "-" + d[3:5]
    return None

ref = ro(DATA / "covid_graves.sqlite")
pts = {
    row[0] for row in ref.execute(
        "SELECT DISTINCT reaction_pt FROM effets"
    )
}
ref.close()
print("Termes MedDRA suivis :", len(pts), flush=True)

src = ro(SOURCE)
dst = sqlite3.connect(OUT, timeout=120)
dst.execute("PRAGMA journal_mode=WAL")
dst.executescript("""
CREATE TABLE IF NOT EXISTS progress(
 phase TEXT PRIMARY KEY, last_id INTEGER NOT NULL,
 n INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS periods(
 month TEXT PRIMARY KEY, n INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pt_periods(
 pt TEXT NOT NULL, month TEXT NOT NULL,
 n INTEGER NOT NULL, PRIMARY KEY(pt,month)
);
INSERT OR IGNORE INTO progress VALUES('reports',0,0);
INSERT OR IGNORE INTO progress VALUES('reactions',0,0);
""")
dst.commit()

def state(phase):
    return dst.execute(
        "SELECT last_id,n FROM progress WHERE phase=?",
        (phase,)
    ).fetchone()

def persist(phase, last, count, values):
    dst.execute("BEGIN")
    if phase == "reports":
        dst.executemany("""
            INSERT INTO periods VALUES(?,?)
            ON CONFLICT(month)
            DO UPDATE SET n=n+excluded.n
        """, ((m,n) for m,n in values.items()))
    else:
        dst.executemany("""
            INSERT INTO pt_periods VALUES(?,?,?)
            ON CONFLICT(pt,month)
            DO UPDATE SET n=n+excluded.n
        """, ((p,m,n) for (p,m),n in values.items()))
    dst.execute(
        "UPDATE progress SET last_id=?,n=? WHERE phase=?",
        (last,count,phase)
    )
    dst.commit()

start = time.monotonic()

# Phase 1 : tous les signalements, par mois.
last, n = state("reports")
if last != -1:
    cur = src.execute("""
        SELECT safety_report_id,gateway_date
        FROM ema_icsr
        WHERE safety_report_id>?
        ORDER BY safety_report_id
    """, (last,))
    while True:
        block = cur.fetchmany(STEP)
        if not block:
            break
        counts = Counter()
        for rid, date in block:
            m = month(date)
            counts[m or "INCONNU"] += 1
        last = block[-1][0]
        n += len(block)
        persist("reports", last, n, counts)
        print("Référence tous signalements :", f"{n:,}",
              flush=True)
    dst.execute(
        "UPDATE progress SET last_id=-1 WHERE phase='reports'"
    )
    dst.commit()

# Phase 2 : tous les signalements par effet et mois.
# Dédoublonnage de chaque terme MedDRA par signalement.
last, n = state("reactions")
if last != -1:
    cur = src.execute("""
        SELECT r.safety_report_id,r.reaction_pt,i.gateway_date
        FROM ema_icsr_reaction r
        JOIN ema_icsr i
          ON i.safety_report_id=r.safety_report_id
        WHERE r.safety_report_id>?
        ORDER BY r.safety_report_id
    """, (last,))

    counts = Counter()
    pending = 0
    current_id = None
    current_month = None
    current_pts = set()

    def finish():
        global pending
        if current_id is None:
            return
        for pt in current_pts:
            counts[(pt,current_month)] += 1
        pending += 1

    for rid, pt, date in cur:
        if rid != current_id:
            if current_id is not None:
                finish()

            # Sauvegarde uniquement après un signalement entier.
            if pending >= STEP:
                n += pending
                persist("reactions", last, n, counts)
                print(
                    "Référence effets :", f"{n:,}",
                    "| durée :", int(time.monotonic()-start), "s",
                    flush=True
                )
                pending = 0
                counts.clear()

            current_id = rid
            current_month = month(date) or "INCONNU"
            current_pts = set()
            last = rid

        if pt in pts:
            current_pts.add(pt)

    if current_id is not None:
        finish()
        n += pending
        persist("reactions", last, n, counts)

    dst.execute(
        "UPDATE progress SET last_id=-1 WHERE phase='reactions'"
    )
    dst.commit()

print("=== RÉFÉRENCE TEMPORELLE TERMINÉE ===", flush=True)
print("Base :", OUT, flush=True)
print("Total de référence :", dst.execute(
    "SELECT SUM(n) FROM periods"
).fetchone()[0], flush=True)
print("Paires effet-mois :", dst.execute(
    "SELECT COUNT(*) FROM pt_periods"
).fetchone()[0], flush=True)

src.close()
dst.close()
