# Synced from task-research:
#   Claude-research/code/literature_search/clients/crossref.py
# Do not edit in this repo — edit upstream and re-sync.
# Import path adapted from upstream: 'from cache' -> 'from src.cache'
# to work correctly when imported as the src.clients subpackage.
"""
clients/crossref.py — CrossRef Works API client.

Two entry points:
  lookup_by_doi(doi, cache_dir)   — validates an existing DOI.
  lookup_by_query(cache_dir, ...) — author + title + year query search.

_fetch() return semantics:
    None  → network/5xx error; caller will NOT cache.
    {}    → 404 / not found; caller WILL cache.
    {...} → success; caller WILL cache.
"""

import logging
import time
from pathlib import Path

try:
    import requests
except ImportError as err:
    raise ImportError("'requests' is required: pip install requests") from err

from hed_metadata_toolkit.cache import cache_get_or_fetch

logger = logging.getLogger(__name__)

_BASE = "https://api.crossref.org"
_RATE_SEC = 0.2
_last_call: dict[str, float] = {}


def _throttle(host: str) -> None:
    now = time.monotonic()
    gap = now - _last_call.get(host, 0.0)
    if gap < _RATE_SEC:
        time.sleep(_RATE_SEC - gap)
    _last_call[host] = time.monotonic()


def _get(
    url: str, headers: dict | None = None, params: dict | None = None
) -> dict | None:
    host = "api.crossref.org"
    for attempt in range(3):
        _throttle(host)
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
        except requests.RequestException as exc:
            logger.warning("crossref network error (attempt %d): %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(2)
                continue
            return None

        status = resp.status_code
        if status == 200:
            return resp.json()
        if status == 404:
            return {}
        if status == 429:
            logger.warning("crossref 429 rate-limit; waiting 2 s")
            time.sleep(2)
            continue
        if status >= 500:
            logger.warning("crossref %d server error; waiting 2 s", status)
            time.sleep(2)
            continue
        logger.info("crossref %d for %s", status, url)
        return {}
    return None


def lookup_by_doi(
    doi: str,
    cache_dir: Path,
    email: str = "hedannotation@gmail.com",
) -> dict | None:
    """Return CrossRef work metadata for this DOI, or None if not found / error."""
    doi = doi.lower().strip()
    url = f"{_BASE}/works/{doi}"
    headers = {"User-Agent": f"hed-task/1.0 (mailto:{email})"}

    def _fetch() -> dict | None:
        data = _get(url, headers=headers)
        if data is None:
            return None
        if not data:
            return {}
        message = data.get("message", {})
        return message if message else {}

    cached = cache_get_or_fetch(
        cache_dir=cache_dir,
        source="crossref",
        key=doi,
        fetch=_fetch,
        stable=True,  # DOI metadata is stable; skip date-stamping.
    )

    if not cached:
        logger.info("source=crossref doi=%s status=not_found", doi)
        return None

    cached["_source"] = "crossref"
    cached["_doi"] = doi
    cached["_fetched_on"] = cached.get("deposited", {}).get("date-time", "")

    logger.info("source=crossref doi=%s status=200", doi)
    return cached


def lookup_by_query(
    cache_dir: Path,
    author_family: str,
    title_terms: str,
    year: int,
    email: str = "hedannotation@gmail.com",
) -> dict | None:
    """Query CrossRef by author + title terms + year.  Return the top result's
    normalised metadata dict or None if no match / network error.

    Cache key: 'crossref_query|<author_lc>|<title_lc>|<year>'
    The same query on the same day costs zero extra network calls on rerun.

    Validation: returns None if the top result's first-author surname does
    not fuzzy-match *author_family* (case-insensitive substring check).
    """
    author_lc = author_family.strip().lower()
    title_lc = title_terms.strip().lower()
    cache_key = f"crossref_query|{author_lc}|{title_lc}|{year}"

    url = f"{_BASE}/works"
    headers = {"User-Agent": f"hed-task/1.0 (mailto:{email})"}

    def _fetch() -> dict | None:
        params: dict = {
            "query.author": author_family,
            "filter": f"from-pub-date:{year},until-pub-date:{year}",
            "rows": 1,
            "mailto": email,
        }
        if title_terms:
            params["query.title"] = title_terms
        raw = _get(url, headers=headers, params=params)
        if raw is None:
            return None
        if not raw:
            return {}
        # Return the full message so the items list is accessible
        return raw

    raw = cache_get_or_fetch(
        cache_dir=cache_dir,
        source="crossref",
        key=cache_key,
        fetch=_fetch,
    )

    if not raw:
        logger.info(
            "source=crossref query author=%s year=%d status=not_found",
            author_family,
            year,
        )
        return None

    items = raw.get("message", {}).get("items", [])
    if not items:
        logger.info(
            "source=crossref query author=%s year=%d status=empty_items",
            author_family,
            year,
        )
        return None

    item = items[0]

    # Validate: first-author surname must contain or be contained by author_family
    cr_authors = item.get("author", [])
    if cr_authors:
        cr_family = cr_authors[0].get("family", "").lower().strip()
        if author_lc not in cr_family and cr_family not in author_lc:
            logger.info(
                "source=crossref query: author mismatch expected=%s got=%s",
                author_lc,
                cr_family,
            )
            return None

    item["_source"] = "crossref_query"
    item["_cache_key"] = cache_key
    logger.info(
        "source=crossref query author=%s year=%d status=found doi=%s",
        author_family,
        year,
        item.get("DOI", ""),
    )
    return item
