"""Check 20 random Genius pages to characterize the preamble pattern."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import re
from src.storage import get_conn
from src.scrapers.genius import GeniusScraper

scraper = GeniusScraper()

with get_conn() as conn:
    # Get 20 random songs across decades
    rows = conn.execute("""
        SELECT title, artist, year FROM songs
        WHERE year IN (1955, 1965, 1975, 1985, 1995, 2005, 2015, 2024)
        ORDER BY RANDOM() LIMIT 20
    """).fetchall()

for row in rows:
    result = scraper.fetch(row["title"], row["artist"])
    if not result:
        continue

    lyrics = result.lyrics
    # Find first section marker
    m = re.search(r"\[(?:Verse|Chorus|Intro|Bridge|Pre-Chorus|Post-Chorus|Outro|Hook|Refrain)", lyrics)
    if m:
        before = lyrics[:m.start()].strip()
        first_marker_pos = m.start()
    else:
        before = lyrics[:200].strip()
        first_marker_pos = -1

    # Heuristic check: does "before" look like prose?
    # Prose tends to: end with period/question/exclamation,
    # have long lines, contain few line breaks relative to length
    first_lines = before.split("\n")[:3]
    last_char = before[-1] if before else ""

    print(f"--- {row['year']} {row['title']!r} by {row['artist']!r} ---")
    print(f"  First marker at char {first_marker_pos}")
    print(f"  Text before marker ({len(before)} chars):")
    for ln in first_lines:
        print(f"    {ln[:80]!r}")
    print(f"  Last char before marker: {last_char!r}")
    print()