"""
Path C: rescue contaminated Genius rows by re-fetching from AZLyrics/Lyrics.com.

1. Delete contaminated Genius rows from the lyrics table
2. Run the pipeline with Genius excluded — the affected songs (now missing
   lyrics) will be retried against the remaining sources
3. Songs that no source can find: stay missing (acceptable residual)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
import re

from src.storage import get_conn
from src.pipeline import run, report_coverage
from src.scrapers.azlyrics import AZLyricsScraper
from src.scrapers.lyrics_com import LyricsComScraper

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# Same patterns from the audit
PATTERNS = [
    r"\bRead More\b",
    r"\bContributors?\b.*?\bLyrics\b",
    r"\bsophomore album\b",
    r"\bdebut album\b",
    r"\btitle track\b",
    r"\bdescribed (?:the song|it) as\b",
    r"\bsays in (?:an interview|the song)\b",
    r"\bwas (?:released|written|recorded) (?:in|on|by)\b",
    r"\bis (?:a|the) (?:song|single|track) (?:from|by|off)\b",
    r"\bproduced by\b",
    r"\bin an interview\b",
    r"\bthe music video\b",
    r"\baccording to\b",
    r"\btold (?:rolling stone|billboard|nme|pitchfork)\b",
    r"\bbillboard hot 100\b",
    r"\bin (?:19|20)\d{2},\s",
    r"\b(?:first|1st) single\b",
    r"\bhit single\b",
    r"\bchart[- ]topping\b",
    r"\b(?:lead|second|third|fourth) single\b",
    r"\bdescribes? the (?:painful|emotional|joyful|romantic) (?:experience|story|journey)\b",
    r"\bdescribes? (?:her|his|their) (?:experience|story|journey|feelings)\b",
]

# ---- Step 1: identify and delete contaminated rows ----
combined = re.compile("|".join(PATTERNS), re.IGNORECASE)

with get_conn() as conn:
    rows = conn.execute("""
        SELECT l.id, s.title, s.artist, l.lyrics_text
        FROM lyrics l JOIN songs s ON s.id = l.song_id
        WHERE l.source = 'genius'
    """).fetchall()

    contaminated_ids = []
    for r in rows:
        head = r["lyrics_text"][:400]
        if combined.search(head):
            contaminated_ids.append(r["id"])

    print(f"Identified {len(contaminated_ids)} contaminated Genius rows")

    if contaminated_ids:
        # Use parameterized query for safety; SQLite has a limit of 999
        # parameters per query, so batch if needed
        for i in range(0, len(contaminated_ids), 500):
            batch = contaminated_ids[i:i+500]
            placeholders = ",".join("?" * len(batch))
            conn.execute(f"DELETE FROM lyrics WHERE id IN ({placeholders})", batch)
        print(f"Deleted {len(contaminated_ids)} rows")

# ---- Step 2: run the pipeline without Genius ----
print("\nRe-running pipeline with Genius excluded (fallback to AZLyrics, Lyrics.com)\n")

result = run(scrapers=[AZLyricsScraper(), LyricsComScraper()])

print(f"\nAttempted: {result.attempted}")
print(f"Recovered: {result.succeeded}")
print(f"By source: {result.by_source}")
print(f"Still missing: {len(result.failed_songs)}")

# ---- Step 3: report ----
print("\n" + "=" * 50)
print("FINAL COVERAGE:")
print("=" * 50)
report_coverage()