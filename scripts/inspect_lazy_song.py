import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bs4 import BeautifulSoup
from src.http_client import fetch

# Search for the URL first
from src.scrapers.genius import GeniusScraper
s = GeniusScraper()
url = s.find_url("The Lazy Song", "Bruno Mars")
print(f"URL: {url}\n")

html, _ = fetch(url, subdir="genius")
soup = BeautifulSoup(html, "lxml")

containers = soup.select('div[data-lyrics-container="true"]')
print(f"Found {len(containers)} containers\n")

for i, c in enumerate(containers):
    print(f"=== Container {i} ===")
    # Walk up showing classes
    for level, parent in enumerate(c.parents):
        if level >= 6 or parent.name == "body":
            break
        classes = parent.get("class", [])
        # Truncate generated hash classes for readability
        classes_short = [cls.split("-sc-")[0] if "-sc-" in cls else cls for cls in classes]
        print(f"  L{level}: <{parent.name}> {classes_short}")

    print(f"  Self classes: {[c.get('class', [])]}")
    text = c.get_text()[:150].replace("\n", " | ")
    print(f"  Preview: {text}")
    print(f"  Has <br>: {bool(c.find('br'))}")
    print()