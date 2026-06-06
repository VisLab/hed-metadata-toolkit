"""
clients/osf.py — OSF (Open Science Framework) API client.

Written for openneuro-metadata Phase 2.5A; no upstream equivalent in
task-research (OSF-specific to the OpenNeuro citation workstream).

Public surface:
    lookup_guid(guid, *, cache_dir, today)         — /v2/guids/<guid>/
    lookup_typed(obj_type, obj_id, *, cache_dir, today) — /v2/<type>/<id>/
    extract_publication_metadata(typed_response)   — parse a typed response
    is_osf_project_doi(doi)                        — filter 10.17605/OSF.IO/*

Design notes (see .status/phase2_5_thinking_2026-05-06.md §2.1, §2.2):
  - OSF project DOIs (prefix 10.17605/OSF.IO/) are DataCite registrations
    for the project itself, NOT publication DOIs.  They must be filtered out
    of any candidate list.
  - HTTP 401 (private project) returns {} so callers get a clean miss;
    the empty result is cached to avoid hammering private projects.
  - All HTTP goes through cache_get_or_fetch with stable=True (OSF project
    metadata is effectively stable once published).
"""

import logging
import re
from pathlib import Path

try:
    import requests
except ImportError as err:
    raise ImportError("'requests' is required: pip install requests") from err

from hed_metadata_toolkit.cache import cache_get_or_fetch

logger = logging.getLogger(__name__)

_API_BASE = "https://api.osf.io/v2"
_USER_AGENT = "HED-openneuro-metadata/0.1 (mailto:hedannotation@gmail.com)"

# OSF DataCite project DOI prefix — case-insensitive.
# See thinking doc §2.1: these are project DOIs, not publication DOIs.
_OSF_PROJECT_DOI_RE = re.compile(r"^10\.17605/OSF\.IO/", re.IGNORECASE)

# Conservative DOI regex for description text mining.
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")


def is_osf_project_doi(doi: str) -> bool:
    """True iff the DOI starts with '10.17605/OSF.IO/' (case-insensitive).

    These are OSF's DataCite-registered project DOIs and are NOT
    publication DOIs.  See thinking doc §2.1.  Uses match against
    a compiled regex; no network call.
    """
    return bool(_OSF_PROJECT_DOI_RE.match(doi))


def _http_get(url: str) -> dict | None:
    """Single HTTP GET with error handling.  Returns None on transient error."""
    try:
        resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=15)
    except requests.RequestException as exc:
        logger.warning("osf network error for %s: %s", url, exc)
        return None

    status = resp.status_code
    if status == 200:
        return resp.json()
    if status == 404:
        logger.info("osf 404 for %s", url)
        return {}
    if status == 401:
        # Private project.  Cache {} so we don't hammer it on re-runs.
        logger.info("osf 401 (private) for %s", url)
        return {}
    # 429 / 5xx — transient; do not cache so the next run retries.
    logger.warning("osf HTTP %d for %s", status, url)
    return None


def lookup_guid(guid: str, *, cache_dir: Path, today: str | None = None) -> dict:
    """Return the OSF API record for a GUID via /v2/guids/<guid>/.

    Returns the parsed JSON dict on success, {} on 404 or 401 (private),
    raises on transient failure.  Caches under <cache_dir>/osf/stable/.

    The returned dict has shape:
        {"data": {"relationships": {"referent": {"data": {"type": "nodes",
                                                           "id": "bxvhr"}}}}}
    Callers extract data["relationships"]["referent"]["data"] to get
    (type, id) for the follow-up lookup_typed call.
    """
    guid = guid.strip().lower()
    url = f"{_API_BASE}/guids/{guid}/"

    def _fetch() -> dict | None:
        return _http_get(url)

    return cache_get_or_fetch(
        cache_dir=cache_dir,
        source="osf",
        key=f"guid:{guid}",
        fetch=_fetch,
        today=today,
        stable=True,
    )


def lookup_typed(
    obj_type: str,
    obj_id: str,
    *,
    cache_dir: Path,
    today: str | None = None,
) -> dict:
    """Follow up a guid lookup with /v2/<type>/<id>/.  Same caching.

    obj_type is one of 'nodes', 'preprints', 'registrations', 'files'.
    Returns {} on miss or private.
    """
    obj_id = obj_id.strip().lower()
    url = f"{_API_BASE}/{obj_type}/{obj_id}/"

    def _fetch() -> dict | None:
        return _http_get(url)

    return cache_get_or_fetch(
        cache_dir=cache_dir,
        source="osf",
        key=f"{obj_type}:{obj_id}",
        fetch=_fetch,
        today=today,
        stable=True,
    )


def extract_publication_metadata(typed_response: dict) -> dict:
    """Return a dict with keys:
      type:                 'preprints' | 'nodes' | 'registrations' | 'files' | None
      is_publication:       True only for 'preprints' with attrs.doi
      publication_doi:      str or None (always None for non-preprints)
      title:                str or None
      contributor_families: list[str]   (first-author-family first if discoverable)
      description_excerpt:  str or None  (first 600 chars)
      description_doi_candidates: list[str]  (DOIs regex-extracted from description,
                                               with 10.17605/OSF.IO/* filtered out)

    Pass {} (the empty-dict miss result) to get the all-None default.
    """
    _empty: dict = {
        "type": None,
        "is_publication": False,
        "publication_doi": None,
        "title": None,
        "contributor_families": [],
        "description_excerpt": None,
        "description_doi_candidates": [],
    }

    if not typed_response:
        return _empty

    data = typed_response.get("data", {})
    if not data:
        return _empty

    obj_type = data.get("type")
    attrs = data.get("attributes", {})

    title = attrs.get("title")
    description = attrs.get("description") or ""
    description_excerpt = description[:600] if description else None

    # Extract DOIs from description text, filtering out OSF project DOIs.
    raw_dois = _DOI_RE.findall(description)
    description_doi_candidates = [d for d in raw_dois if not is_osf_project_doi(d)]

    # Contributors: try embedded data first (present when ?embed=contributors
    # was passed); fall back to empty list (caller can follow the relationship
    # link separately if needed).
    contributor_families: list[str] = []
    embeds = data.get("embeds", {})
    contrib_embed = embeds.get("contributors", {})
    for contrib in contrib_embed.get("data", []):
        # Embedded contributor shape:
        # {"embeds": {"users": {"data": {"attributes": {"family_name": "Smith"}}}}}
        user_attrs = (
            contrib.get("embeds", {})
            .get("users", {})
            .get("data", {})
            .get("attributes", {})
        )
        family = user_attrs.get("family_name", "")
        if not family:
            full = user_attrs.get("full_name", "")
            if full:
                family = full.rsplit(" ", 1)[-1]
        if family:
            contributor_families.append(family)

    # Publication DOI: only meaningful for preprints (type == "preprints").
    publication_doi: str | None = None
    is_publication = False
    if obj_type == "preprints":
        raw_doi = attrs.get("doi") or ""
        if raw_doi and not is_osf_project_doi(raw_doi):
            publication_doi = raw_doi
            is_publication = True

    return {
        "type": obj_type,
        "is_publication": is_publication,
        "publication_doi": publication_doi,
        "title": title,
        "contributor_families": contributor_families,
        "description_excerpt": description_excerpt,
        "description_doi_candidates": description_doi_candidates,
    }
