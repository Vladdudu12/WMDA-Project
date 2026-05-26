"""
Lyrics.com scraper — third fallback after Genius and AZLyrics.

Lyrics.com has its own search page with HTML results. URL pattern:
  https://www.lyrics.com/lyrics/{query}
  https://www.lyrics.com/serp.php?st={query}&qtype=1

Each search result links to a lyric page where the body is contained in
<pre id="lyric-body-text">. Simple and stable.
"""

import logging
import re
from typing import Optional
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from ..http_client import fetch
from ..storage import normalize
from .base import LyricsScraper

logger = logging.getLogger(__name__)


class LyricsComScraper(LyricsScraper):
    SOURCE_NAME = "lyrics_com"

    BASE = "https://www.lyrics.com"
    SEARCH_URL = "https://www.lyrics.com/serp.php?st={query}&qtype=1"

    def find_url(self, title: str, artist: str) -> Optional[str]:
        """Search Lyrics.com and pick the best-matching result."""
        query = quote(f"{title} {artist}")
        search_url = self.SEARCH_URL.format(query=query)

        html, status = fetch(search_url, subdir="lyrics_com_search")
        if not html:
            return None

        soup = BeautifulSoup(html, "lxml")

        target_title = normalize(title)
        target_artist = normalize(artist)

        # Search results live in .sec-lyric blocks. Each has a song link and
        # an artist link.
        best_url: Optional[str] = None
        best_score = -1

        for block in soup.select("div.sec-lyric, .sec-lyric"):
            link = block.find("a", href=re.compile(r"/lyric/"))
            if not link:
                continue
            hit_title = normalize(link.get_text())
            artist_link = block.find("a", href=re.compile(r"/artist/"))
            hit_artist = normalize(artist_link.get_text()) if artist_link else ""

            score = 0
            if hit_title == target_title:
                score += 10
            elif target_title in hit_title or hit_title in target_title:
                score += 5
            if hit_artist == target_artist:
                score += 5
            elif (target_artist and hit_artist and
                  (target_artist in hit_artist or hit_artist in target_artist)):
                score += 2

            if score > best_score:
                best_score = score
                best_url = urljoin(self.BASE, link["href"])

        # Fallback: any link to a /lyric/ page on the search results
        if not best_url:
            link = soup.find("a", href=re.compile(r"/lyric/\d+"))
            if link:
                best_url = urljoin(self.BASE, link["href"])

        if best_score < 5 and best_score != -1:
            logger.debug(f"[lyrics_com] low-confidence match (score={best_score}) "
                         f"for '{title}' / '{artist}'")
            return None

        return best_url

    def parse_lyrics(self, html: str) -> Optional[str]:
        """Lyrics live in <pre id="lyric-body-text"> on Lyrics.com."""
        soup = BeautifulSoup(html, "lxml")

        pre = soup.find("pre", id="lyric-body-text")
        if pre:
            return pre.get_text(separator="\n")

        # Fallback: a <pre> with class containing "lyric"
        pre = soup.find("pre", class_=re.compile(r"lyric", re.IGNORECASE))
        if pre:
            return pre.get_text(separator="\n")

        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    scraper = LyricsComScraper()
    result = scraper.fetch("Kill Bill", "SZA")
    if result:
        print(f"Source: {result.source}")
        print(f"URL: {result.url}")
        print(f"Length: {len(result.lyrics)} chars\n")
        print(result.lyrics[:400] + "...")
    else:
        print("No lyrics found.")