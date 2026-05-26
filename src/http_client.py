"""
HTTP client with retries, caching, rate limiting, and UA rotation.

Why a custom wrapper instead of just requests?
- Caching: during development we hit the same URLs over and over. Save responses to disk
  so we don't get rate-limited or banned while iterating on parsers.
- Per-domain rate limiting: AZLyrics is aggressive about blocking; we throttle it more.
- Retries with backoff: transient 429/503 are common; tenacity handles them cleanly.
- UA rotation: not for evading detection, but because some sites serve different HTML
  to obvious bots. We use real browser UAs.
"""

import hashlib
import random
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, before_sleep_log,
)
import logging

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent.parent / "data" / "raw"

# Realistic browser UAs (rotated). Add more if needed.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]

# Per-domain minimum seconds between requests. Be polite.
RATE_LIMITS = {
    "billboard.com":  1.5,
    "genius.com":     1.0,
    "azlyrics.com":   5.0,   # aggressive bot detection — throttle hard
    "lyrics.com":     2.0,
    "default":        1.5,
}

_last_request_time: dict[str, float] = {}


class FetchError(Exception):
    """Raised when a fetch fails after all retries."""


def _domain_key(url: str) -> str:
    host = urlparse(url).netloc.lower()
    for known in RATE_LIMITS:
        if known != "default" and known in host:
            return known
    return "default"


def _wait_for_rate_limit(url: str) -> None:
    domain = _domain_key(url)
    min_gap = RATE_LIMITS[domain]
    last = _last_request_time.get(domain, 0)
    elapsed = time.time() - last
    if elapsed < min_gap:
        # Add jitter to avoid being too regular (looks more human, helps with bot detection)
        sleep_time = (min_gap - elapsed) + random.uniform(0, 0.5)
        time.sleep(sleep_time)
    _last_request_time[domain] = time.time()


def _cache_path(url: str, subdir: str) -> Path:
    """Generate a stable filename from URL hash. Subdir keeps sources organized."""
    h = hashlib.sha256(url.encode()).hexdigest()[:16]
    p = CACHE_DIR / subdir
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{h}.html"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type((requests.RequestException, FetchError)),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _do_request(url: str, headers: dict, timeout: int) -> requests.Response:
    resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    # 429 = rate limited, 5xx = server error. Both worth retrying.
    if resp.status_code == 429 or resp.status_code >= 500:
        raise FetchError(f"HTTP {resp.status_code} — will retry")
    return resp


def fetch(
    url: str,
    subdir: str = "misc",
    use_cache: bool = True,
    timeout: int = 20,
    extra_headers: Optional[dict] = None,
) -> tuple[Optional[str], int]:
    """
    Fetch a URL with full resilience stack.
    Returns (html_text, http_status). html_text is None on hard failure.
    Caches successful responses to disk.
    """
    cache_file = _cache_path(url, subdir)

    if use_cache and cache_file.exists():
        logger.debug(f"Cache hit: {url}")
        return cache_file.read_text(encoding="utf-8"), 200

    _wait_for_rate_limit(url)

    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    if extra_headers:
        headers.update(extra_headers)

    try:
        resp = _do_request(url, headers, timeout)
    except Exception as e:
        logger.error(f"Fetch failed permanently for {url}: {e}")
        return None, 0

    if resp.status_code == 200:
        try:
            cache_file.write_text(resp.text, encoding="utf-8")
        except Exception as e:
            logger.warning(f"Cache write failed for {url}: {e}")
        return resp.text, 200

    # Non-200, non-retryable (e.g., 404). Don't cache these.
    logger.info(f"Got HTTP {resp.status_code} for {url}")
    return None, resp.status_code


def clear_cache(subdir: Optional[str] = None) -> int:
    """Wipe cache. Useful when you change parsers and want fresh fetches."""
    target = CACHE_DIR / subdir if subdir else CACHE_DIR
    if not target.exists():
        return 0
    count = 0
    for f in target.rglob("*.html"):
        f.unlink()
        count += 1
    return count