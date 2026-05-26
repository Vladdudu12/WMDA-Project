"""Print the structure of Genius's Layla page to see what's actually there."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bs4 import BeautifulSoup
from src.http_client import fetch

url = "https://genius.com/Derek-and-the-dominos-layla-lyrics"
html, _ = fetch(url, subdir="genius")
soup = BeautifulSoup(html, "lxml")

containers = soup.select('div[data-lyrics-container="true"]')
print(f"Found {len(containers)} containers with data-lyrics-container='true'\n")

for i, c in enumerate(containers):
    print(f"=== Container {i} ===")
    # Show the chain of ancestor classes
    print("Ancestor classes (up to 5 levels):")
    for level, parent in enumerate(c.parents):
        if level >= 5 or parent.name == "body":
            break
        classes = parent.get("class", [])
        print(f"  L{level}: <{parent.name}> classes={classes}")

    print(f"\nContainer's own classes: {c.get('class', [])}")
    print(f"Has <br> tags inside? {bool(c.find('br'))}")

    text = c.get_text()[:200].replace("\n", " | ")
    print(f"First 200 chars of text: {text}")
    print()