"""One-off cleanup: remove Genius lyrics rows contaminated with editorial text."""
from src.storage import get_conn

MARKERS = [
    "Read More",
    "sophomore album",
    "debut album",
    "title track",
    "first single",
    "hit single",
    "chart-topping",
    "third single",
    "second single",
    "lead single",
]

with get_conn() as conn:
    # Build the WHERE clause from the markers
    conditions = " OR ".join("lyrics_text LIKE ?" for _ in MARKERS)
    params = [f"%{m}%" for m in MARKERS]

    # Count first
    count_sql = f"SELECT COUNT(*) FROM lyrics WHERE source='genius' AND ({conditions})"
    n = conn.execute(count_sql, params).fetchone()[0]
    print(f"Found {n} contaminated rows")

    # Delete
    delete_sql = f"DELETE FROM lyrics WHERE source='genius' AND ({conditions})"
    cur = conn.execute(delete_sql, params)
    print(f"Deleted {cur.rowcount} rows")