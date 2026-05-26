"""
Lyrics scraping pipeline.

For each song missing lyrics:
  - Try Genius. If it returns a result, store and move on.
  - Else try AZLyrics. If it returns a result, store and move on.
  - Else try Lyrics.com. If it returns a result, store and move on.
  - Else give up (and log all three failed attempts).

Every attempt — success or failure — gets logged to fetch_attempts so we
can analyze coverage later (e.g. "Genius found 87%, AZLyrics filled
another 8%, Lyrics.com another 3%, 2% failed entirely").

The pipeline is restart-safe: songs that already have lyrics in any
source are skipped. Songs that failed before are retried (since the
failure might have been transient).
"""

import logging
from dataclasses import dataclass
from typing import Optional

from tqdm import tqdm

from .scrapers.azlyrics import AZLyricsScraper
from .scrapers.base import LyricsScraper
from .scrapers.genius import GeniusScraper
from .scrapers.lyrics_com import LyricsComScraper
from .storage import (
    get_conn, insert_lyrics, log_attempt, songs_missing_lyrics, stats,
)

logger = logging.getLogger(__name__)


@dataclass
class PipelineStats:
    attempted: int = 0
    succeeded: int = 0
    by_source: dict[str, int] = None
    failed_songs: list[tuple[str, str]] = None

    def __post_init__(self):
        if self.by_source is None:
            self.by_source = {}
        if self.failed_songs is None:
            self.failed_songs = []


# Order matters: best general coverage first.
DEFAULT_SCRAPERS: list[LyricsScraper] = [
    GeniusScraper(),
    AZLyricsScraper(),
    LyricsComScraper(),
]


def fetch_lyrics_for_song(
    scrapers: list[LyricsScraper],
    song_id: int,
    title: str,
    artist: str,
    conn,
) -> Optional[str]:
    """Try each scraper in order. Returns source name on success, None on total failure."""
    for scraper in scrapers:
        result = scraper.fetch(title, artist)

        if result:
            inserted = insert_lyrics(
                conn, song_id, result.source, result.lyrics, result.url,
            )
            log_attempt(conn, song_id, result.source,
                        success=True, url=result.url)
            if inserted:
                logger.info(f"[OK] {result.source}: '{title}' — {artist} "
                            f"({len(result.lyrics)} chars)")
            return result.source
        else:
            log_attempt(conn, song_id, scraper.SOURCE_NAME,
                        success=False, error_msg="no result")

    logger.info(f"[FAIL] all sources: '{title}' — {artist}")
    return None


def run(
    year: Optional[int] = None,
    scrapers: Optional[list[LyricsScraper]] = None,
    limit: Optional[int] = None,
) -> PipelineStats:
    """
    Run the pipeline over all songs missing lyrics.

    Args:
        year: limit to a single year (None = all years)
        scrapers: override the default scraper list/order
        limit: maximum number of songs to process (useful for testing)
    """
    scrapers = scrapers or DEFAULT_SCRAPERS
    stats_acc = PipelineStats()

    with get_conn() as conn:
        todo = songs_missing_lyrics(conn, year=year)
        if limit:
            todo = todo[:limit]

        if not todo:
            logger.info("Nothing to do — all songs already have lyrics.")
            return stats_acc

        logger.info(f"Pipeline will process {len(todo)} songs "
                    f"using {len(scrapers)} sources")

        for row in tqdm(todo, desc="Fetching lyrics", unit="song"):
            stats_acc.attempted += 1
            source = fetch_lyrics_for_song(
                scrapers, row["id"], row["title"], row["artist"], conn,
            )
            if source:
                stats_acc.succeeded += 1
                stats_acc.by_source[source] = stats_acc.by_source.get(source, 0) + 1
            else:
                stats_acc.failed_songs.append((row["title"], row["artist"]))

            # Commit periodically so a crash doesn't lose progress
            if stats_acc.attempted % 10 == 0:
                conn.commit()

    return stats_acc


def report_coverage() -> None:
    """Print a summary of current coverage."""
    with get_conn() as conn:
        s = stats(conn)
        total = s["total_songs"]
        with_lyrics = s["with_lyrics"]
        pct = (with_lyrics / total * 100) if total else 0
        print(f"\nCoverage: {with_lyrics}/{total} songs have lyrics ({pct:.1f}%)")
        print(f"By source: {s['by_source']}")

        # Per-year coverage
        print("\nPer-year breakdown:")
        rows = conn.execute("""
            SELECT s.year,
                   COUNT(DISTINCT s.id) AS total,
                   COUNT(DISTINCT l.song_id) AS with_lyrics
            FROM songs s LEFT JOIN lyrics l ON l.song_id = s.id
            GROUP BY s.year ORDER BY s.year
        """).fetchall()
        for r in rows:
            pct = (r["with_lyrics"] / r["total"] * 100) if r["total"] else 0
            print(f"  {r['year']}: {r['with_lyrics']:3d}/{r['total']:3d} ({pct:.0f}%)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s: %(message)s")
    # Smoke test: 5 songs from 2023
    result = run(year=2023, limit=5)
    print(f"\nAttempted: {result.attempted}")
    print(f"Succeeded: {result.succeeded}")
    print(f"By source: {result.by_source}")
    if result.failed_songs:
        print(f"Failed: {result.failed_songs}")
    report_coverage()