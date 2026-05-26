"""
Abstract base class that every lyrics scraper implements.

The contract is intentionally minimal: given a title and artist, return
either lyrics+url or None. The pipeline calls these one by one until
someone succeeds.

Why a base class? Three reasons:
1. Forces every scraper to have the same interface (the pipeline doesn't
   care which source it's calling).
2. Centralizes the lyrics-cleaning logic so every source produces
   comparable output.
3. Makes it trivial to add new sources later (just subclass + implement).
"""

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class LyricsResult:
    lyrics: str
    url: str
    source: str


class LyricsScraper(ABC):
    """Abstract base. Subclasses implement search() and fetch_lyrics()."""

    # Subclasses override this with a short identifier ('genius', 'azlyrics', etc.)
    SOURCE_NAME: str = "abstract"

    # Minimum lyrics length to consider a fetch successful.
    # Anything shorter is likely an error page, a stub, or "lyrics not available".
    MIN_LYRICS_LENGTH = 100

    @abstractmethod
    def find_url(self, title: str, artist: str) -> Optional[str]:
        """
        Locate the lyrics page URL for this song.
        Some sources have a search endpoint; others use predictable URL patterns.
        Returns None if the song can't be located on this source.
        """
        ...

    @abstractmethod
    def parse_lyrics(self, html: str) -> Optional[str]:
        """
        Extract lyrics text from a fetched page.
        Returns None if the page doesn't contain valid lyrics.
        """
        ...

    def fetch(self, title: str, artist: str) -> Optional[LyricsResult]:
        """
        Top-level method: find URL → fetch HTML → parse → clean → validate.
        Handles all the error paths so the pipeline gets a clean Optional.

        Special-cases "double A-side" titles like:
            All I Have to Do Is Dream" / "Claudette
        These appear in Wikipedia data when a single had two A-sides counted
        together on the chart. We try each title separately and return the
        first one that succeeds.
        """
        # Detect double A-side notation. Wikipedia uses a literal '" / "' between
        # the two titles, so the input string typically looks like:
        #   Title One" / "Title Two
        # We split on '" / "' and try each candidate; for the multi-artist case
        # we apply the same split to the artist field if it's also split.
        candidates: list[tuple[str, str]] = [(title, artist)]
        if '" / "' in title:
            titles = [t.strip(' "') for t in title.split('" / "')]
            # Artists may or may not be split with " / " too
            if " / " in artist:
                artists = [a.strip() for a in artist.split(" / ")]
                # Pair them if counts match; otherwise use first artist for both
                if len(artists) == len(titles):
                    candidates = list(zip(titles, artists))
                else:
                    candidates = [(t, artists[0]) for t in titles]
            else:
                candidates = [(t, artist) for t in titles]

        for try_title, try_artist in candidates:
            result = self._fetch_single(try_title, try_artist)
            if result:
                return result
        return None

    def _fetch_single(self, title: str, artist: str) -> Optional[LyricsResult]:
        """Inner fetch for a single (title, artist) pair, no special-case logic."""
        from ..http_client import fetch as http_fetch

        try:
            url = self.find_url(title, artist)
        except Exception as e:
            logger.warning(f"[{self.SOURCE_NAME}] find_url failed for "
                           f"'{title}' / '{artist}': {e}")
            return None

        if not url:
            logger.debug(f"[{self.SOURCE_NAME}] no URL for '{title}' / '{artist}'")
            return None

        html, status = http_fetch(url, subdir=self.SOURCE_NAME)
        if not html:
            logger.debug(f"[{self.SOURCE_NAME}] fetch failed ({status}): {url}")
            return None

        try:
            raw_lyrics = self.parse_lyrics(html)
        except Exception as e:
            logger.warning(f"[{self.SOURCE_NAME}] parse_lyrics raised: {e}")
            return None

        if not raw_lyrics:
            return None

        cleaned = self.clean_lyrics(raw_lyrics)
        if len(cleaned) < self.MIN_LYRICS_LENGTH:
            logger.debug(f"[{self.SOURCE_NAME}] lyrics too short "
                         f"({len(cleaned)} chars) for '{title}'")
            return None

        return LyricsResult(lyrics=cleaned, url=url, source=self.SOURCE_NAME)

    @staticmethod
    def clean_lyrics(text: str) -> str:
        """
        Normalize lyrics text so output from all sources looks the same.
        - Remove section markers like [Verse 1], [Chorus], [Bridge]
        - Remove producer/contributor lines that some sources prepend
        - Collapse whitespace
        - Strip leading/trailing junk
        """
        # Remove bracketed section markers: [Verse], [Chorus: Beyoncé], [Intro], etc.
        text = re.sub(r"\[[^\]]{0,80}\]", "", text)

        # Some sources prepend stuff like "5 Contributors" or "Translations"
        text = re.sub(r"^\d+\s+Contributors?.*?Lyrics", "", text,
                      flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"^.*?Translations?.*?\n", "", text, flags=re.IGNORECASE)

        # Trailing junk: "Embed", "You might also like", view counts
        text = re.sub(r"\d*Embed\s*$", "", text)
        text = re.sub(r"You might also like.*$", "", text, flags=re.DOTALL)

        # Normalize whitespace: keep paragraph breaks (\n\n) but collapse runs
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"^\s+|\s+$", "", text)

        return text


def slugify(s: str, separator: str = "-") -> str:
    """
    Convert 'Beyoncé feat. Jay-Z' → 'beyonce-jay-z' style slugs.
    Different sources use different rules; subclasses can override.
    """
    import unicodedata
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    # Remove featuring clauses (each source handles featured artists differently)
    s = re.sub(r"\(feat\.?.*?\)|\(featuring.*?\)|\(ft\.?.*?\)", "", s)
    s = re.sub(r"\bfeat\.?\b.*$|\bfeaturing\b.*$|\bft\.?\b.*$", "", s)
    # Keep alphanumeric, replace everything else with separator
    s = re.sub(r"[^a-z0-9]+", separator, s)
    s = s.strip(separator)
    return s