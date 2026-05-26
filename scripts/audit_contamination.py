"""Audit how much contamination remains in stored lyrics."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import re
from src.storage import get_conn

# Signals that the first ~300 chars are editorial prose, not lyrics:
PROSE_SIGNALS = [
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
]

with get_conn() as conn:
    rows = conn.execute("""
        SELECT s.year, s.title, s.artist, l.lyrics_text
        FROM lyrics l JOIN songs s ON s.id = l.song_id
        WHERE l.source = 'genius'
    """).fetchall()

print(f"Total Genius rows: {len(rows)}\n")

contaminated = []
for r in rows:
    head = r["lyrics_text"][:400]
    for pattern in PROSE_SIGNALS:
        if re.search(pattern, head, flags=re.IGNORECASE):
            contaminated.append((r["year"], r["title"], r["artist"], pattern))
            break

print(f"Contaminated: {len(contaminated)} ({len(contaminated)/len(rows)*100:.1f}%)\n")

# Show 15 examples
print("Examples:")
for year, title, artist, pat in contaminated[:15]:
    print(f"  {year}: '{title}' by {artist}  (matched: {pat})")