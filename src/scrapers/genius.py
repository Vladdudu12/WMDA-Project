"""
Genius.com lyrics scraper.

Strategy:
1. Use Genius's public search API (genius.com/api/search/multi?q=...)
   to find the lyrics page URL. This is what the website itself uses;
   no API key required. JSON response is structured and reliable.
2. Fetch the lyrics page HTML.
3. Parse using three selector strategies (newest → oldest):
   a. div[data-lyrics-container="true"]  ← current (2020+)
   b. div[class^="Lyrics__Container"]    ← intermediate (2018-2020)
   c. div.lyrics                          ← legacy (pre-2018)
"""

import json
import logging
import re
from typing import Optional
from urllib.parse import quote

from bs4 import BeautifulSoup, NavigableString, Tag

from ..http_client import fetch
from .base import LyricsScraper

logger = logging.getLogger(__name__)


class GeniusScraper(LyricsScraper):
    SOURCE_NAME = "genius"

    SEARCH_URL = "https://genius.com/api/search/multi?per_page=5&q={query}"

    def find_url(self, title: str, artist: str) -> Optional[str]:
        """Search Genius for the song; return the URL of the best match."""
        from ..storage import normalize

        query = quote(f"{title} {artist}")
        search_url = self.SEARCH_URL.format(query=query)

        html, status = fetch(
            search_url,
            subdir="genius_search",
            extra_headers={"Accept": "application/json"},
        )
        if not html:
            return None

        try:
            data = json.loads(html)
        except json.JSONDecodeError:
            logger.warning(f"[genius] search returned non-JSON for '{title}' / '{artist}'")
            return None

        # Walk the sections looking for song hits
        sections = data.get("response", {}).get("sections", [])
        target_title = normalize(title)
        target_artist = normalize(artist)

        best_url: Optional[str] = None
        best_score = -1

        # Genius puts results in multiple sections. The most common ones with
        # song results are "top_hit" (the algorithmically-best match — often the
        # only place a popular song appears) and "song" (general song matches).
        # We used to look only at "song" which silently missed ~20% of popular
        # tracks. We now scan any section whose hits contain song-type results.
        for section in sections:
            for hit in section.get("hits", []):
                # Filter at the hit level rather than section level: top_hit
                # sections also contain non-song results (artists, albums).
                if hit.get("type") != "song" and hit.get("index") != "song":
                    continue
                result = hit.get("result", {})
                if not result.get("url"):
                    continue

                hit_title = normalize(result.get("title", ""))
                hit_artist = normalize(
                    result.get("primary_artist", {}).get("name", "")
                )

                # Score: exact title match is most important; artist match secondary
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
                    best_url = result["url"]

        # Require some minimum confidence — avoid grabbing a completely wrong song
        if best_score < 5:
            logger.debug(f"[genius] no confident match for '{title}' / '{artist}' "
                         f"(best score: {best_score})")
            return None

        return best_url

    def parse_lyrics(self, html: str) -> Optional[str]:
        """Extract lyrics text using a cascade of selectors."""
        soup = BeautifulSoup(html, "lxml")

        # Strategy 1: current markup (2020+)
        # The first container often contains an editorial intro BEFORE the
        # actual lyrics ("Eric Clapton formed Derek and the Dominos…Read More
        # <lyrics>"). We don't filter containers — we strip the intro from
        # the combined text in clean_lyrics() instead.
        containers = soup.select('div[data-lyrics-container="true"]')
        if containers:
            return self._extract_text(containers)

        # Strategy 2: intermediate markup (2018–2020)
        containers = soup.find_all("div", class_=re.compile(r"^Lyrics__Container"))
        if containers:
            return self._extract_text(containers)

        # Strategy 3: legacy markup (pre-2018)
        legacy = soup.find("div", class_="lyrics")
        if legacy:
            return legacy.get_text(separator="\n")

        return None

    @staticmethod
    def _extract_text(containers: list[Tag]) -> str:
        """
        Walk container nodes preserving line breaks.
        Genius uses <br> tags between lines and wraps annotated phrases in <a>.
        BeautifulSoup's get_text(separator='\\n') doesn't quite do what we want
        because it inserts \\n around inline anchors too, breaking up phrases.
        """
        parts: list[str] = []
        for container in containers:
            for node in container.descendants:
                if isinstance(node, NavigableString):
                    parts.append(str(node))
                elif isinstance(node, Tag) and node.name == "br":
                    parts.append("\n")
            parts.append("\n\n")  # paragraph break between containers
        return "".join(parts)

    # Patterns that indicate a paragraph is editorial prose, not lyrics.
    # These were derived from auditing 6%+ contamination across 3000 songs.
    EDITORIAL_PATTERNS = [
        r"\bRead More\b",
        r"\b\d+\s*Contributors?\b",
        r"\bTranslations?\b",
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
        r"\bfirst (?:single|track) (?:from|off)\b",
        r"\bhit single\b",
        r"\bchart[- ]topping\b",
        r"\blead single\b",
    ]

    @classmethod
    def _is_editorial_paragraph(cls, paragraph: str) -> bool:
        """Detect whether a paragraph is editorial prose vs lyrics."""
        if not paragraph or len(paragraph) < 20:
            return False
        for pat in cls.EDITORIAL_PATTERNS:
            if re.search(pat, paragraph, flags=re.IGNORECASE):
                return True
        return False

    @classmethod
    def clean_lyrics(cls, text: str) -> str:
        """
        Genius-specific cleanup BEFORE handing off to the base cleaner.

        We've identified three contamination patterns on Genius pages:
        1. Contributors/Translations header crammed at the very start
        2. Editorial blurb ending in "Read More" (truncated long descriptions)
        3. Short editorial paragraphs with no "Read More" marker

        Strategy: split into paragraphs (blank-line separated), drop any
        leading paragraphs that look editorial. Stop dropping as soon as
        we find one that looks like lyrics.
        """
        # First strip the most obvious headers
        m = re.search(r"\bRead\s*More\b", text)
        if m:
            text = text[m.end():]
        else:
            m = re.search(r"\b\d+\s*Contributors?.*?Lyrics\b",
                          text, flags=re.DOTALL)
            if m:
                text = text[m.end():]

        # Now drop any leading paragraphs that still look editorial.
        # Paragraphs are blank-line separated; lyrics use line breaks within
        # a verse but blank lines between verses.
        paragraphs = re.split(r"\n\s*\n", text)
        while paragraphs and cls._is_editorial_paragraph(paragraphs[0]):
            paragraphs.pop(0)
        text = "\n\n".join(paragraphs)

        # Hand off to base cleaner for [Verse]/[Chorus] markers, whitespace,
        # Embed suffixes, etc.
        return LyricsScraper.clean_lyrics(text)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    scraper = GeniusScraper()
    result = scraper.fetch("Last Night", "Morgan Wallen")
    if result:
        print(f"Source: {result.source}")
        print(f"URL: {result.url}")
        print(f"Length: {len(result.lyrics)} chars\n")
        print(result.lyrics[:400] + "...")
    else:
        print("No lyrics found.")