"""
clients/openalex.py — OpenAlex Works API client.

Endpoints:
    Search:   GET https://api.openalex.org/works?filter=...
    DOI:      GET https://api.openalex.org/works/doi:{doi}

In v3, search_works uses phrase-aware title_and_abstract.search filter and
an explicit type filter (type:article|review or type:review).  The old
top-level search= parameter is replaced by filter= entries.
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

_BASE = "https://api.openalex.org"
_RATE_SEC = 0.2
_last_call: dict[str, float] = {}


def _throttle(host: str) -> None:
    now = time.monotonic()
    gap = now - _last_call.get(host, 0.0)
    if gap < _RATE_SEC:
        time.sleep(_RATE_SEC - gap)
    _last_call[host] = time.monotonic()


def _get(url: str, params: dict | None = None) -> dict | None:
    host = "api.openalex.org"
    for attempt in range(3):
        _throttle(host)
        try:
            resp = requests.get(url, params=params, timeout=15)
        except requests.RequestException as exc:
            logger.warning("openalex network error (attempt %d): %s", attempt + 1, exc)
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
            logger.warning("openalex 429 rate-limit; waiting 2 s")
            time.sleep(2)
            continue
        if status >= 500:
            logger.warning("openalex %d server error; waiting 2 s", status)
            time.sleep(2)
            continue
        logger.info("openalex %d for %s", status, url)
        return {}
    return None


def search_works(
    cache_dir: Path,
    query: str = "",
    phrases: list[str] | None = None,
    topic_ids: list[str] | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    is_review: bool | None = None,
    pub_type: str | None = None,
    max_results: int = 100,
    email: str = "hedannotation@gmail.com",
) -> list[dict]:
    """Search OpenAlex Works using phrase-aware title/abstract filter.

    Pass phrases (list of raw phrase strings) rather than query.  Each phrase
    becomes title_and_abstract.search:"<phrase>" in the filter, OR-joined with
    pipe so OpenAlex matches the exact phrase in title or abstract.

    Args:
        query:    Legacy: treated as a single phrase if phrases is absent.
        phrases:  List of phrase strings (primary name + aliases).
        pub_type: "article_review" -> type:article|review;
                  "review"         -> type:review.
                  Takes priority over is_review.
    """
    filter_parts: list[str] = []

    # Phrase filter
    if phrases:
        phrase_exprs = [f'title_and_abstract.search:"{p}"' for p in phrases]
        filter_parts.append("|".join(phrase_exprs))
    elif query:
        filter_parts.append(f'title_and_abstract.search:"{query}"')

    # Type filter (v3 pub_type takes priority over legacy is_review)
    if pub_type == "article_review":
        filter_parts.append("type:article|review")
    elif pub_type == "review":
        filter_parts.append("type:review")
    elif is_review is True:
        filter_parts.append("type:review")

    # Date range
    if year_min is not None:
        filter_parts.append(f"from_publication_date:{year_min}-01-01")
    if year_max is not None:
        filter_parts.append(f"to_publication_date:{year_max}-12-31")

    if topic_ids:
        filter_parts.append("topics.id:" + "|".join(topic_ids))

    select_fields = (
        "id,doi,title,authorships,publication_year,primary_location,"
        "abstract_inverted_index,type,cited_by_count,fwci,"
        "cited_by_percentile_year,open_access,mesh,topics"
    )

    params: dict = {
        "sort":     "cited_by_count:desc",
        "per-page": min(max_results, 200),
        "select":   select_fields,
        "mailto":   email,
    }
    if filter_parts:
        params["filter"] = ",".join(filter_parts)

    phrases_key = "|".join(sorted(phrases)) if phrases else (query or "")
    topics_str  = "|".join(sorted(topic_ids)) if topic_ids else ""
    effective_pub_type = pub_type or ("review" if is_review else None)
    cache_key = (
        f"search|phrases={phrases_key}|year_min={year_min}|year_max={year_max}"
        f"|pub_type={effective_pub_type}|topics={topics_str}|max={max_results}"
    )

    def _fetch() -> dict | None:
        data = _get(f"{_BASE}/works", params=params)
        if data is None:
            return None
        results = data.get("results") or []
        logger.info("openalex search raw_count=%d for phrases=%r",
                    len(results), phrases_key[:60])
        return {"results": results[:max_results]}

    cached = cache_get_or_fetch(
        cache_dir=cache_dir, source="openalex",
        key=cache_key, fetch=_fetch,
    )
    return cached.get("results", [])


def lookup_by_doi(
    doi: str,
    cache_dir: Path,
    email: str = "hedannotation@gmail.com",
) -> dict | None:
    doi = doi.lower().strip()
    url = f"{_BASE}/works/doi:{doi}"

    def _fetch() -> dict | None:
        return _get(url, params={"mailto": email})

    cached = cache_get_or_fetch(
        cache_dir=cache_dir, source="openalex", key=doi, fetch=_fetch,
        stable=True,   # DOI metadata is stable; skip date-stamping.
    )
    if not cached:
        logger.info("source=openalex doi=%s status=not_found cached=True", doi)
        return None
    cached["_source"] = "openalex"
    cached["_doi"] = doi
    cached["_fetched_on"] = cached.get("updated_date", "")
    logger.info("source=openalex doi=%s status=%s cached=True", doi, cached.get("id", "unknown"))
    return cached
