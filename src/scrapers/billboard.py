"""
Wikipedia-based Billboard Year-End Hot 100 scraper.

Why Wikipedia instead of Billboard.com:
- Billboard locks year-end archives behind Billboard Pro (paid)
- Wikipedia has clean, consistent tables for every year 1959-present
- Public, no auth, no aggressive rate limiting
- Wikipedia explicitly permits scraping (their robots.txt and API both encourage it)

URL pattern: https://en.wikipedia.org/wiki/Billboard_Year-End_Hot_100_singles_of_{YEAR}

Table structure is remarkably stable: a wikitable with columns roughly
[Rank/No., Title, Artist(s)]. We use multiple parser strategies for resilience.
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup, Tag

from ..http_client import fetch
from ..storage import get_conn, insert_song

logger = logging.getLogger(__name__)

WIKI_URL = "https://en.wikipedia.org/wiki/Billboard_Year-End_Hot_100_singles_of_{year}"


@dataclass
class ChartEntry:
    year: int
    rank: int
    title: str
    artist: str


# Header keywords we use to identify which column is which.
# Wikipedia editors aren't 100% consistent across years.
RANK_HEADERS = {"no.", "no", "#", "rank", "position"}
TITLE_HEADERS = {"title", "single", "song"}
ARTIST_HEADERS = {"artist", "artist(s)", "artists", "performer", "performer(s)"}


def _clean_text(s: str) -> str:
    """Strip whitespace, footnote markers like [1], and surrounding quotes."""
    s = re.sub(r"\[\w+\]", "", s)        # remove [1], [a], [note 1], etc.
    s = s.strip()
    # Wikipedia wraps titles in straight or curly double quotes
    s = s.strip('"\u201c\u201d\u2018\u2019')
    return s.strip()


def _identify_columns(header_row: Tag) -> Optional[tuple[int, int, int]]:
    """Inspect the first row of a wikitable to find (rank_col, title_col, artist_col)."""
    headers = [_clean_text(th.get_text()).lower() for th in header_row.find_all(["th", "td"])]
    if not headers:
        return None

    rank_idx = title_idx = artist_idx = None
    for i, h in enumerate(headers):
        if h in RANK_HEADERS and rank_idx is None:
            rank_idx = i
        elif h in TITLE_HEADERS and title_idx is None:
            title_idx = i
        elif h in ARTIST_HEADERS and artist_idx is None:
            artist_idx = i

    if title_idx is None or artist_idx is None:
        return None
    # If no explicit rank column, assume position 0 is rank
    if rank_idx is None:
        rank_idx = 0
    return rank_idx, title_idx, artist_idx


def _extract_from_table(table: Tag, year: int) -> list[ChartEntry]:
    """Pull entries from a single wikitable. Returns [] if it doesn't look like a chart."""
    rows = table.find_all("tr")
    if len(rows) < 10:  # need at least a header + several data rows
        return []

    cols = _identify_columns(rows[0])
    if not cols:
        return []
    rank_idx, title_idx, artist_idx = cols

    entries: list[ChartEntry] = []
    auto_rank = 0  # used when the rank cell is missing/non-numeric

    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) <= max(rank_idx, title_idx, artist_idx):
            continue

        rank_text = _clean_text(cells[rank_idx].get_text())
        title = _clean_text(cells[title_idx].get_text())
        artist = _clean_text(cells[artist_idx].get_text())

        if not title or not artist:
            continue

        # Parse rank; fall back to auto-increment if cell is non-numeric
        # (some tables merge cells for ties, leaving the rank blank)
        m = re.match(r"\d+", rank_text)
        if m:
            rank = int(m.group())
            auto_rank = rank
        else:
            auto_rank += 1
            rank = auto_rank

        if 1 <= rank <= 100:
            entries.append(ChartEntry(year=year, rank=rank, title=title, artist=artist))

    return entries


def fetch_year(year: int) -> list[ChartEntry]:
    """Scrape one year's chart. Tries every wikitable on the page; returns the best."""
    url = WIKI_URL.format(year=year)
    html, status = fetch(url, subdir="wikipedia_billboard")

    if not html:
        logger.error(f"Failed to fetch Wikipedia page for {year} (status {status})")
        return []

    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table", class_=re.compile(r"wikitable"))

    if not tables:
        logger.warning(f"No wikitables found on Wikipedia page for {year}")
        return []

    # Try each table; keep the one yielding the most entries
    best: list[ChartEntry] = []
    for i, table in enumerate(tables):
        entries = _extract_from_table(table, year)
        if len(entries) > len(best):
            best = entries
            logger.debug(f"Year {year}: table {i} yielded {len(entries)} entries")

    # Dedupe by rank, keeping the first occurrence (handles tables split into halves)
    seen_ranks = set()
    deduped: list[ChartEntry] = []
    for e in sorted(best, key=lambda x: x.rank):
        if e.rank not in seen_ranks:
            seen_ranks.add(e.rank)
            deduped.append(e)

    logger.info(f"Year {year}: {len(deduped)} entries scraped")
    return deduped


def scrape_years(start_year: int, end_year: int) -> dict[int, int]:
    """Scrape and store charts for a range of years. Returns {year: count_inserted}."""
    results = {}
    with get_conn() as conn:
        for year in range(start_year, end_year + 1):
            entries = fetch_year(year)
            inserted = 0
            for e in entries:
                song_id = insert_song(conn, e.year, e.rank, e.title, e.artist)
                if song_id is not None:
                    inserted += 1
            results[year] = inserted
            logger.info(f"Year {year}: inserted {inserted} songs into DB")
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Smoke test: 2023
    entries = fetch_year(2023)
    print(f"\nFound {len(entries)} entries for 2023:\n")
    for e in entries[:15]:
        print(f"  {e.rank:3d}. {e.title:<40s} — {e.artist}")
    print(f"  ... and {len(entries) - 15} more")