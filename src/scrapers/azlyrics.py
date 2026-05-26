"""
AZLyrics.com lyrics scraper — fallback for songs Genius can't find.

URL pattern: https://www.azlyrics.com/lyrics/{artist_slug}/{title_slug}.html

No search API. AZLyrics generates URLs deterministically from artist and title:
- Lowercase only
- Alphanumeric only (no spaces, hyphens, punctuation)
- Leading "the " is stripped from artist
- 'featuring' clauses are stripped from both fields

Their HTML has a quirk we exploit: the lyrics div has no class or id, but it's
preceded by a specific HTML comment. We locate it by that comment.

AZLyrics is aggressive about bot detection. The http_client rate-limits this
domain to 5s between requests. Don't lower that.
"""

import logging
import re
from typing import Optional

from bs4 import BeautifulSoup, Comment

from .base import LyricsScraper, slugify

logger = logging.getLogger(__name__)


class AZLyricsScraper(LyricsScraper):
    SOURCE_NAME = "azlyrics"

    URL_TEMPLATE = "https://www.azlyrics.com/lyrics/{artist}/{title}.html"

    def find_url(self, title: str, artist: str) -> Optional[str]:
        """AZLyrics uses deterministic URL construction — no search needed."""
        # Strip leading "the " from artist (AZ convention: "The Beatles" → "beatles")
        clean_artist = re.sub(r"^the\s+", "", artist, flags=re.IGNORECASE)
        # Strip featuring clauses
        clean_artist = re.split(
            r"\s+(?:feat\.?|featuring|ft\.?|&|and|with|vs\.?|x)\s+",
            clean_artist, maxsplit=1, flags=re.IGNORECASE
        )[0]

        # AZLyrics uses no separators — pure alphanumeric
        artist_slug = slugify(clean_artist, separator="")
        title_slug = slugify(title, separator="")

        if not artist_slug or not title_slug:
            return None

        return self.URL_TEMPLATE.format(artist=artist_slug, title=title_slug)

    def parse_lyrics(self, html: str) -> Optional[str]:
        """
        Find the lyrics div by locating a distinctive HTML comment AZLyrics
        places right before it. The div itself has no class or id, but the
        comment is stable across the entire site.
        """
        soup = BeautifulSoup(html, "lxml")

        # AZLyrics warns scrapers in a comment right before the lyrics:
        #   <!-- Usage of azlyrics.com content by any third-party lyrics provider... -->
        marker_pattern = re.compile(r"usage of azlyrics\.com", re.IGNORECASE)
        comment = soup.find(string=lambda t: isinstance(t, Comment)
                            and marker_pattern.search(str(t)))

        lyrics_div = None
        if comment:
            # The lyrics div is the first <div> sibling after the comment
            sib = comment.next_sibling
            while sib is not None:
                if getattr(sib, "name", None) == "div":
                    lyrics_div = sib
                    break
                sib = sib.next_sibling

        # Fallback: find a div inside the main content with no class attribute
        # AZLyrics deliberately leaves the lyrics div unclassed
        if not lyrics_div:
            main = soup.find("div", class_="ringtone")
            if main:
                lyrics_div = main.find_next_sibling("div")

        # Last resort: the largest unclassed div on the page
        if not lyrics_div:
            candidates = [d for d in soup.find_all("div")
                          if not d.get("class") and not d.get("id")]
            if candidates:
                lyrics_div = max(candidates, key=lambda d: len(d.get_text()))

        if not lyrics_div:
            return None

        # Strip script tags (AZ injects tracking scripts inside the lyrics div)
        for s in lyrics_div.find_all("script"):
            s.decompose()

        text = lyrics_div.get_text(separator="\n")
        # AZ wraps everything in italics tags that we don't need
        text = re.sub(r"^\s*\n+", "", text)
        return text


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    scraper = AZLyricsScraper()
    result = scraper.fetch("Flowers", "Miley Cyrus")
    if result:
        print(f"Source: {result.source}")
        print(f"URL: {result.url}")
        print(f"Length: {len(result.lyrics)} chars\n")
        print(result.lyrics[:400] + "...")
    else:
        print("No lyrics found.")