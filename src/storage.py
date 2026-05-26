"""
SQLite storage layer.

Design choices:
- One songs table keyed by (year, rank) since Billboard is our source of truth for "what to analyze"
- Separate lyrics table allows multiple attempts per song from different sources
- fetch_attempts table logs every scrape attempt for debugging/grading
- All timestamps are UTC ISO strings (SQLite has no native datetime)
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

DB_PATH = Path(__file__).parent.parent / "data" / "songs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS songs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    year         INTEGER NOT NULL,
    rank         INTEGER NOT NULL,
    title        TEXT    NOT NULL,
    artist       TEXT    NOT NULL,
    title_norm   TEXT    NOT NULL,   -- normalized for matching (lowercase, no punct)
    artist_norm  TEXT    NOT NULL,
    chart_source TEXT    NOT NULL DEFAULT 'billboard_year_end',
    scraped_at   TEXT    NOT NULL,
    UNIQUE(year, rank, chart_source)
);

CREATE INDEX IF NOT EXISTS idx_songs_year ON songs(year);
CREATE INDEX IF NOT EXISTS idx_songs_norm ON songs(title_norm, artist_norm);

CREATE TABLE IF NOT EXISTS lyrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id     INTEGER NOT NULL,
    source      TEXT    NOT NULL,    -- 'genius', 'azlyrics', 'lyrics_com'
    lyrics_text TEXT    NOT NULL,
    url         TEXT,
    word_count  INTEGER,
    scraped_at  TEXT    NOT NULL,
    FOREIGN KEY (song_id) REFERENCES songs(id),
    UNIQUE(song_id, source)
);

CREATE INDEX IF NOT EXISTS idx_lyrics_song ON lyrics(song_id);

CREATE TABLE IF NOT EXISTS fetch_attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id     INTEGER NOT NULL,
    source      TEXT    NOT NULL,
    success     INTEGER NOT NULL,   -- 0/1
    error_msg   TEXT,
    http_status INTEGER,
    url         TEXT,
    attempted_at TEXT   NOT NULL,
    FOREIGN KEY (song_id) REFERENCES songs(id)
);

CREATE INDEX IF NOT EXISTS idx_attempts_song ON fetch_attempts(song_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize(s: str) -> str:
    """Normalize titles/artists for fuzzy matching across sources.
    Genius might say 'Beyoncé', Billboard 'Beyonce', AZLyrics 'beyonce'.
    """
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"\(feat\.?.*?\)|\(featuring.*?\)|\(ft\.?.*?\)", "", s)
    s = re.sub(r"feat\.?.*$|featuring.*$|ft\.?.*$", "", s)
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


@contextmanager
def get_conn(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path = DB_PATH) -> None:
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)


def insert_song(conn: sqlite3.Connection, year: int, rank: int,
                title: str, artist: str,
                chart_source: str = "billboard_year_end") -> Optional[int]:
    """Insert a song, return its id. Returns None if it already exists."""
    try:
        cur = conn.execute(
            """INSERT INTO songs(year, rank, title, artist, title_norm,
                                 artist_norm, chart_source, scraped_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (year, rank, title, artist,
             normalize(title), normalize(artist), chart_source, now_iso())
        )
        return cur.lastrowid
    except sqlite3.IntegrityError:
        row = conn.execute(
            "SELECT id FROM songs WHERE year=? AND rank=? AND chart_source=?",
            (year, rank, chart_source)
        ).fetchone()
        return row["id"] if row else None


def insert_lyrics(conn: sqlite3.Connection, song_id: int, source: str,
                  lyrics_text: str, url: Optional[str] = None) -> bool:
    """Returns True if inserted, False if this song already has lyrics from this source."""
    try:
        conn.execute(
            """INSERT INTO lyrics(song_id, source, lyrics_text, url,
                                  word_count, scraped_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (song_id, source, lyrics_text, url,
             len(lyrics_text.split()), now_iso())
        )
        return True
    except sqlite3.IntegrityError:
        return False


def log_attempt(conn: sqlite3.Connection, song_id: int, source: str,
                success: bool, error_msg: Optional[str] = None,
                http_status: Optional[int] = None, url: Optional[str] = None) -> None:
    conn.execute(
        """INSERT INTO fetch_attempts(song_id, source, success, error_msg,
                                       http_status, url, attempted_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (song_id, source, 1 if success else 0, error_msg, http_status, url, now_iso())
    )


def songs_missing_lyrics(conn: sqlite3.Connection,
                          year: Optional[int] = None) -> list[sqlite3.Row]:
    """Songs that don't yet have lyrics from any source."""
    q = """SELECT s.* FROM songs s
           LEFT JOIN lyrics l ON l.song_id = s.id
           WHERE l.id IS NULL"""
    params: tuple = ()
    if year is not None:
        q += " AND s.year = ?"
        params = (year,)
    q += " ORDER BY s.year, s.rank"
    return conn.execute(q, params).fetchall()


def stats(conn: sqlite3.Connection) -> dict:
    return {
        "total_songs": conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0],
        "with_lyrics": conn.execute(
            "SELECT COUNT(DISTINCT song_id) FROM lyrics"
        ).fetchone()[0],
        "by_source": dict(conn.execute(
            "SELECT source, COUNT(*) FROM lyrics GROUP BY source"
        ).fetchall()),
        "by_year": dict(conn.execute(
            "SELECT year, COUNT(*) FROM songs GROUP BY year ORDER BY year"
        ).fetchall()),
    }


if __name__ == "__main__":
    init_db()
    print(f"Initialized DB at {DB_PATH}")