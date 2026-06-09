"""
clients/unpaywall.py — Unpaywall API client, DOI lookup only.

Endpoint:
    GET https://api.unpaywall.org/v2/{doi}?email={email}

_fetch() return semantics:
    None  → network/5xx error; caller will NOT cache.
    {}    → 404 not in index; caller WILL cache.
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

_BASE = "https://api.unpaywall.org/v2"
_RATE_SEC = 0.2
_last_call: dict[str, float] = {}


def _throttle(host: str) -> None:
    now = time.monotonic()
    gap = now - _last_call.get(host, 0.0)
    if gap < _RATE_SEC:
        time.sleep(_RATE_SEC - gap)
    _last_call[host] = time.monotonic()


def _get(url: str, params: dict | None = None) -> dict | None:
    host = "api.unpaywall.org"
    for attempt in range(3):
        _throttle(host)
        try:
            resp = requests.get(url, params=params, timeout=15)
        except requests.RequestException as exc:
            logger.warning("unpaywall network error (attempt %d): %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(2)
                continue
            return None

        status = resp.status_code
        if status == 200:
            return resp.json()
        if status == 404:
            logger.info("unpaywall 404 for %s (not in index)", url)
            return {}
        if status == 429:
            logger.warning("unpaywall 429 rate-limit; waiting 2 s")
            time.sleep(2)
            continue
        if status >= 500:
            logger.warning("unpaywall %d server error; waiting 2 s", status)
            time.sleep(2)
            continue
        logger.info("unpaywall %d for %s", status, url)
        return {}
    return None


def lookup_by_doi(
    doi: str,
    cache_dir: Path,
    email: str = "hedannotation@gmail.com",
) -> dict | None:
    """Return Unpaywall record for this DOI, or None if not found / error."""
    doi = doi.lower().strip()
    url = f"{_BASE}/{doi}"

    def _fetch() -> dict | None:
        return _get(url, params={"email": email})

    cached = cache_get_or_fetch(
        cache_dir=cache_dir,
        source="unpaywall",
        key=doi,
        fetch=_fetch,
        stable=True,  # OA status is essentially fixed; skip date-stamping.
    )

    if not cached:
        logger.info("source=unpaywall doi=%s status=not_found", doi)
        return None

    cached["_source"] = "unpaywall"
    cached["_doi"] = doi
    cached["_fetched_on"] = cached.get("updated", "")

    logger.info(
        "source=unpaywall doi=%s is_oa=%s status=200",
        doi,
        cached.get("is_oa"),
    )
    return cached
