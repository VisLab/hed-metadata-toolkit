"""
clients/europepmc.py — Europe PMC REST API client.

In v3, search() uses per-field TITLE/ABSTRACT phrase filter and explicit
PUB_TYPE filter to exclude non-journal outputs.
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

_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_RATE_SEC = 0.2
_last_call: dict[str, float] = {}


def _throttle(host: str) -> None:
    now = time.monotonic()
    gap = now - _last_call.get(host, 0.0)
    if gap < _RATE_SEC:
        time.sleep(_RATE_SEC - gap)
    _last_call[host] = time.monotonic()


def _get(params: dict) -> dict | None:
    host = "www.ebi.ac.uk"
    for attempt in range(3):
        _throttle(host)
        try:
            resp = requests.get(_BASE, params=params, timeout=20)
        except requests.RequestException as exc:
            logger.warning("europepmc network error (attempt %d): %s", attempt + 1, exc)
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
            logger.warning("europepmc 429 rate-limit; waiting 2 s")
            time.sleep(2)
            continue
        if status >= 500:
            logger.warning("europepmc %d server error; waiting 2 s", status)
            time.sleep(2)
            continue
        logger.info("europepmc %d for params=%s", status, params)
        return {}
    return None


def _extract_first(data: dict) -> dict | None:
    try:
        results = data["resultList"]["result"]
        if results:
            return results[0]
    except (KeyError, IndexError, TypeError):
        pass
    return None


def _build_phrase_query(phrases: list[str]) -> str:
    """Build (TITLE:"p1" OR ABSTRACT:"p1") OR (TITLE:"p2" OR ABSTRACT:"p2") ...

    The whole block is wrapped in parens so downstream AND operations bind
    correctly with date and PUB_TYPE filters.
    """
    blocks = [f'(TITLE:"{p}" OR ABSTRACT:"{p}")' for p in phrases]
    return "(" + " OR ".join(blocks) + ")"


def search(
    cache_dir: Path,
    query: str = "",
    phrases: list[str] | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    has_mesh: str | None = None,
    pub_type: str | None = None,
    max_results: int = 100,
) -> list[dict]:
    """Search Europe PMC using per-field TITLE/ABSTRACT phrase filter.

    Pass phrases (list of raw phrase strings) rather than query.  The function
    builds (TITLE:"p1" OR ABSTRACT:"p1") OR ... so EuropePMC matches the
    phrase in title or abstract rather than doing a full-text token search.

    Args:
        query:    Legacy full-text query; used only if phrases is absent.
        phrases:  Phrase list (primary name + aliases).
        pub_type: "research_article" -> PUB_TYPE:"research-article";
                  "review_article"   -> PUB_TYPE:"review-article".
                  Takes priority over has_mesh.
    """
    if phrases:
        query_base = _build_phrase_query(phrases)
    elif query:
        query_base = query
    else:
        query_base = ""

    parts: list[str] = [query_base] if query_base else []

    if year_min is not None or year_max is not None:
        lo = f"{year_min}-01-01" if year_min is not None else "0001-01-01"
        hi = f"{year_max}-12-31" if year_max is not None else "9999-12-31"
        parts.append(f"FIRST_PDATE:[{lo} TO {hi}]")

    if pub_type == "research_article":
        parts.append('PUB_TYPE:"research-article"')
    elif pub_type == "review_article":
        parts.append('PUB_TYPE:"review-article"')

    full_query = " AND ".join(parts)

    params = {
        "query": full_query,
        "format": "json",
        "resultType": "core",
        "pageSize": str(min(max_results, 1000)),
        "sort": "CITED desc",
    }

    phrases_key = "|".join(sorted(phrases)) if phrases else (query or "")
    cache_key = (
        f"search|phrases={phrases_key}|year_min={year_min}|year_max={year_max}"
        f"|pub_type={pub_type or has_mesh}|max={max_results}"
    )

    def _fetch() -> dict | None:
        data = _get(params)
        if data is None:
            return None
        results = (data.get("resultList") or {}).get("result") or []
        logger.info(
            "europepmc search raw_count=%d for phrases=%r",
            len(results),
            phrases_key[:60],
        )
        return {"results": results[:max_results]}

    cached = cache_get_or_fetch(
        cache_dir=cache_dir,
        source="europepmc",
        key=cache_key,
        fetch=_fetch,
    )
    return cached.get("results", [])


def lookup_by_doi(
    doi: str,
    cache_dir: Path,
    email: str = "hedannotation@gmail.com",
) -> dict | None:
    doi = doi.lower().strip()
    params = {
        "query": f"DOI:{doi}",
        "format": "json",
        "resultType": "core",
        "pageSize": "1",
    }
    cache_key = f"doi:{doi}"

    def _fetch() -> dict | None:
        data = _get(params)
        if data is None:
            return None
        hit = _extract_first(data)
        return hit or {}

    cached = cache_get_or_fetch(
        cache_dir=cache_dir,
        source="europepmc",
        key=cache_key,
        fetch=_fetch,
        stable=True,  # DOI metadata is stable; skip date-stamping.
    )
    if not cached:
        logger.info("source=europepmc doi=%s status=not_found", doi)
        return None
    cached["_source"] = "europepmc"
    cached["_doi"] = doi
    cached["_fetched_on"] = cached.get("firstPublicationDate", "")
    logger.info("source=europepmc doi=%s status=200", doi)
    return cached


def lookup_by_pmid(
    pmid: str,
    cache_dir: Path,
    email: str = "hedannotation@gmail.com",
) -> dict | None:
    pmid = str(pmid).strip()
    params = {
        "query": f"EXT_ID:{pmid} AND SRC:MED",
        "format": "json",
        "resultType": "core",
        "pageSize": "1",
    }
    cache_key = f"pmid:{pmid}"

    def _fetch() -> dict | None:
        data = _get(params)
        if data is None:
            return None
        hit = _extract_first(data)
        return hit or {}

    cached = cache_get_or_fetch(
        cache_dir=cache_dir,
        source="europepmc",
        key=cache_key,
        fetch=_fetch,
        stable=True,  # PMID metadata is stable; skip date-stamping.
    )
    if not cached:
        logger.info("source=europepmc pmid=%s status=not_found", pmid)
        return None
    cached["_source"] = "europepmc"
    cached["_doi"] = cached.get("doi", "")
    cached["_fetched_on"] = cached.get("firstPublicationDate", "")
    logger.info("source=europepmc pmid=%s status=200", pmid)
    return cached
