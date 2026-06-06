"""
clients/semanticscholar.py — Semantic Scholar Graph API client.

Endpoints:
    DOI lookup:
        GET https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}
    Text search:
        GET https://api.semanticscholar.org/graph/v1/paper/search
    Citation expansion (Stage B):
        GET https://api.semanticscholar.org/graph/v1/paper/{paperId}/citations
    Reference list (unwired, future):
        GET https://api.semanticscholar.org/graph/v1/paper/{paperId}/references

_fetch() return semantics:
    None  -> network or 5xx error; caller will NOT cache.
    {}    -> any 4xx response (404 not found, 400 bad request, etc.);
             caller WILL cache so the same query is not retried.
             The exact status code is logged at INFO by _get(), so
             upstream WARNING lines should reference "no data" rather
             than hard-coding a status.
    {...} -> success; caller WILL cache.

API key (optional): improves reliability and priority queue access.
Rate limit: always 1 rps regardless of key presence.  The free-tier key
does not increase the rps ceiling; supply it via S2_API_KEY env var or
code/.apikeys for stable access, not for speed.
"""

import hashlib
import logging
import time
from pathlib import Path

try:
    import requests
except ImportError as err:
    raise ImportError("'requests' is required: pip install requests") from err

from hed_metadata_toolkit.cache import cache_get_or_fetch

logger = logging.getLogger(__name__)

_BASE = "https://api.semanticscholar.org/graph/v1"

_LOOKUP_FIELDS = (
    "externalIds,title,authors,year,venue,abstract,"
    "openAccessPdf,isOpenAccess,citationCount"
)

# Fields for /paper/search (v3 extended set: tldr, influentialCitationCount, fieldsOfStudy, paperId).
FIELDS_SEARCH = (
    "title,abstract,tldr,year,authors,venue,"
    "publicationTypes,fieldsOfStudy,"
    "citationCount,influentialCitationCount,"
    "externalIds,openAccessPdf,paperId"
)

# Fields for /paper/{id}/citations.
#
# Each citation result is wrapped in a `citingPaper` object, so paper-level
# fields must be prefixed `citingPaper.<field>`. Edge-level fields
# (`intents`, `isInfluential`, `contexts`, `contextsWithIntent`) are at the
# top level — no prefix.
#
# IMPORTANT: `tldr` is NOT supported on this endpoint, even as
# `citingPaper.tldr`. S2 returns HTTP 400 with body
# `{"error":"Unrecognized or unsupported fields: [tldr]"}`. If TLDRs are
# needed for citing papers, fetch them in a separate /paper/{id} call.
#
# An earlier version of this constant listed paper fields without the
# `citingPaper.` prefix; S2 returns 400 on those too. Diagnosed via the
# response-body logging added 2026-04-28 in `_get()`.
FIELDS_CITATIONS = (
    "intents,isInfluential,"
    "citingPaper.title,citingPaper.abstract,"
    "citingPaper.year,citingPaper.authors,citingPaper.venue,"
    "citingPaper.publicationTypes,citingPaper.fieldsOfStudy,"
    "citingPaper.citationCount,citingPaper.influentialCitationCount,"
    "citingPaper.externalIds,citingPaper.openAccessPdf,citingPaper.paperId"
)

# Same idea for /paper/{id}/references — wrapping object is `citedPaper`.
# Same `tldr` restriction is assumed (the references endpoint mirrors
# citations); if it's actually supported there, add `citedPaper.tldr` back.
FIELDS_REFERENCES = (
    "intents,isInfluential,"
    "citedPaper.title,citedPaper.abstract,"
    "citedPaper.year,citedPaper.authors,citedPaper.venue,"
    "citedPaper.publicationTypes,citedPaper.fieldsOfStudy,"
    "citedPaper.citationCount,citedPaper.influentialCitationCount,"
    "citedPaper.externalIds,citedPaper.openAccessPdf,citedPaper.paperId"
)

# Always 1 rps: S2 free-tier keys do not provide a higher search rate.
_RATE_SEC = 1.0
_last_call: dict[str, float] = {}


def _throttle(host: str) -> None:
    """Enforce at most 1 request per second to the given host."""
    now = time.monotonic()
    gap = now - _last_call.get(host, 0.0)
    if gap < _RATE_SEC:
        time.sleep(_RATE_SEC - gap)
    _last_call[host] = time.monotonic()


def _get(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    api_key: str | None = None,
) -> dict | None:
    """GET with retry on 429 and 5xx. Returns None on transient error, {} on 404."""
    host = "api.semanticscholar.org"
    req_headers = headers or {}
    if api_key and "x-api-key" not in req_headers:
        req_headers = dict(req_headers)
        req_headers["x-api-key"] = api_key

    for attempt in range(3):
        _throttle(host)
        try:
            resp = requests.get(url, params=params, headers=req_headers, timeout=15)
        except requests.RequestException as exc:
            logger.warning("semanticscholar network error (attempt %d): %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(2)
                continue
            return None

        status = resp.status_code
        if status == 200:
            return resp.json()
        if status == 404:
            logger.info("semanticscholar 404 for %s", url)
            return {}
        if status == 429:
            retry_after = int(resp.headers.get("Retry-After", 5))
            logger.warning("semanticscholar 429 rate-limit; waiting %d s", retry_after)
            time.sleep(retry_after)
            continue
        if status >= 500:
            logger.warning("semanticscholar %d server error; waiting 2 s", status)
            time.sleep(2)
            continue
        # Other 4xx (most commonly 400 Bad Request from a malformed `fields`
        # parameter on the citations/references endpoints). Log a snippet
        # of the response body so the actual S2 error message is visible
        # rather than just the status code.
        body_snippet = (resp.text or "")[:200].replace("\n", " ")
        logger.info(
            "semanticscholar %d for %s — body: %s",
            status, url, body_snippet,
        )
        return {}
    return None


def lookup_by_doi(
    doi: str,
    cache_dir: Path,
    email: str = "hedannotation@gmail.com",
) -> dict | None:
    """Return Semantic Scholar paper record for this DOI, or None if not found."""
    doi = doi.lower().strip()
    url = f"{_BASE}/paper/DOI:{doi}"

    def _fetch() -> dict | None:
        return _get(url, params={"fields": _LOOKUP_FIELDS})

    cached = cache_get_or_fetch(
        cache_dir=cache_dir,
        source="semanticscholar",
        key=f"doi:{doi}",
        fetch=_fetch,
        stable=True,   # DOI metadata is stable; skip date-stamping.
    )
    if not cached:
        logger.info("source=semanticscholar doi=%s status=not_found", doi)
        return None
    cached["_source"] = "semanticscholar"
    cached["_doi"] = doi
    logger.info("source=semanticscholar doi=%s status=200", doi)
    return cached


def search(
    cache_dir: Path,
    query: str,
    year_min: int | None = None,
    year_max: int | None = None,
    max_results: int = 100,
    api_key: str | None = None,
    fields_of_study: str | None = None,
    min_citation_count: int | None = None,
    publication_types: str | None = None,
) -> list[dict]:
    """Search Semantic Scholar by text query.

    Multi-word phrases should be passed as quoted strings, e.g.
    '"response inhibition"', built by search_queries._s2_queries().

    S2 hard-caps each /paper/search request at 100 results.  When
    max_results > 100 this function paginates automatically using the
    offset parameter, issuing one HTTP call per page.  Each call is
    throttled at 1 rps, so fetching 200 results takes ~2 s.

    Args:
        max_results:        Total results desired across all pages.
        min_citation_count: Passed as minCitationCount; applied to all_years
                            sub-query only (value 20).
        publication_types:  Comma-separated S2 type string, e.g.
                            "JournalArticle,Review".
    """
    _PER_PAGE = 100  # S2 per-request hard cap for /paper/search

    # Fixed portion of params shared across all pages.
    base_params: dict = {"query": query, "fields": FIELDS_SEARCH}
    if fields_of_study:
        base_params["fieldsOfStudy"] = fields_of_study
    if min_citation_count is not None:
        base_params["minCitationCount"] = min_citation_count
    if publication_types:
        base_params["publicationTypes"] = publication_types
    if year_min is not None and year_max is not None:
        base_params["year"] = f"{year_min}-{year_max}"
    elif year_min is not None:
        base_params["year"] = f"{year_min}-"
    elif year_max is not None:
        base_params["year"] = f"-{year_max}"

    # Cache key reflects the total requested count so that a 200-result
    # request is cached separately from a 100-result one.
    cache_key = (
        f"search|q={query}|year_min={year_min}|year_max={year_max}"
        f"|max={max_results}|fos={fields_of_study}"
        f"|min_cites={min_citation_count}|pub_types={publication_types}"
        f"|fields={FIELDS_SEARCH}"
    )

    def _fetch() -> dict | None:
        collected: list[dict] = []
        offset = 0
        remaining = max_results
        while remaining > 0:
            page_limit = min(remaining, _PER_PAGE)
            params = {**base_params, "limit": page_limit, "offset": offset}
            data = _get(f"{_BASE}/paper/search", params=params, api_key=api_key)
            if data is None:
                return None  # transient error — do not cache
            results = data.get("data") or []
            collected.extend(results)
            logger.info(
                "semanticscholar search offset=%d page_count=%d total_so_far=%d "
                "api_total=%s q=%r",
                offset, len(results), len(collected),
                data.get("total", "?"), query[:60],
            )
            # Fewer results than requested means no more pages available.
            if len(results) < page_limit:
                break
            offset += page_limit
            remaining -= page_limit
        return {"results": collected}

    cached = cache_get_or_fetch(
        cache_dir=cache_dir, source="semanticscholar",
        key=cache_key, fetch=_fetch,
    )
    return cached.get("results", [])


def fetch_citations(
    paper_id: str,
    cache_dir: Path,
    limit: int = 1000,
    api_key: str | None = None,
    fields: str = FIELDS_CITATIONS,
) -> list[dict]:
    """Fetch papers citing paper_id via GET /paper/{paperId}/citations.

    Returns a list of result dicts, each with top-level keys:
        intents       list[str]  -- citation intent tags
        isInfluential bool       -- S2 non-trivial citation flag
        citingPaper   dict       -- the citing paper record

    S2 supports up to 1000 results per citations request.
    Returns [] on 404 or HTTP error (logged, not raised).
    Cache key: outputs/cache/semanticscholar/YYYY-MM-DD/cit_<sha1>.json
    """
    cache_key = "cit_" + hashlib.sha1(
        f"{paper_id}|{limit}|{fields}".encode()
    ).hexdigest()

    def _fetch() -> dict | None:
        url = f"{_BASE}/paper/{paper_id}/citations"
        data = _get(url, params={"fields": fields, "limit": limit}, api_key=api_key)
        if data is None:
            return None
        if not data:
            # _get() already logged the actual HTTP status at INFO level
            # (404, 400, or other 4xx). 400 from this endpoint commonly
            # means S2 doesn't have a usable citation record for the
            # paperId (merged/aliased/restricted); 404 means paperId not
            # found at all. Both are operationally identical here: cache
            # empty, move on.
            logger.warning(
                "semanticscholar no citations available for paper_id=%s "
                "(see preceding INFO line for status)",
                paper_id,
            )
            return {"data": []}
        results = data.get("data") or []
        logger.info("semanticscholar citations paper_id=%s raw_count=%d", paper_id, len(results))
        return {"data": results[:limit]}

    cached = cache_get_or_fetch(
        cache_dir=cache_dir, source="semanticscholar",
        key=cache_key, fetch=_fetch,
    )
    return cached.get("data", [])


def fetch_references(
    paper_id: str,
    cache_dir: Path,
    limit: int = 1000,
    api_key: str | None = None,
    fields: str = FIELDS_REFERENCES,
) -> list[dict]:
    """Fetch papers cited by paper_id via GET /paper/{paperId}/references.

    Symmetric to fetch_citations.  Each result dict has keys
    intents, isInfluential, citedPaper (not citingPaper).
    Currently unwired -- present for future backward-citation expansion.
    """
    cache_key = "ref_" + hashlib.sha1(
        f"{paper_id}|{limit}|{fields}".encode()
    ).hexdigest()

    def _fetch() -> dict | None:
        url = f"{_BASE}/paper/{paper_id}/references"
        data = _get(url, params={"fields": fields, "limit": limit}, api_key=api_key)
        if data is None:
            return None
        if not data:
            # See fetch_citations() for the rationale on why both 400 and
            # 404 land here and are treated identically.
            logger.warning(
                "semanticscholar no references available for paper_id=%s "
                "(see preceding INFO line for status)",
                paper_id,
            )
            return {"data": []}
        results = data.get("data") or []
        return {"data": results[:limit]}

    cached = cache_get_or_fetch(
        cache_dir=cache_dir, source="semanticscholar",
        key=cache_key, fetch=_fetch,
    )
    return cached.get("data", [])
