"""
clients/pmc.py — PMC BioC REST API client.

Two entry points:
  lookup_by_pmcid(pmcid, cache_dir)  — fetch a BioC JSON document
                                       for a PMC Open Access article.
  fetch_image(pmcid, filename)       — fetch figure bytes for a
                                       BioC-referenced filename
                                       (added 2026-05-30 for PR-G,
                                       plan v2 §13 figure-bytes pass;
                                       see "PMC image URL story"
                                       below).

Fresh sync implementation written to match this project's client
family (sibling of ``crossref.py``); not vendored from opencite
because opencite's PMC client is async and importing async code into
this otherwise-synchronous pipeline is brittle.  See
``.status/plan_2026-05-19_rec1_v2.md`` §3.5 for the rationale.

The PMC BioC OA endpoint:

  GET https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json/<PMCID>/unicode

No auth.  NCBI's general 3 req/s policy applies.

Return semantics for ``lookup_by_pmcid`` match the rest of the
client family:

  None  → network/5xx error; caller will NOT cache.
  {}    → 404 / PMCID not in OA subset; caller WILL cache as miss.
  {...} → success; caller WILL cache.

The returned dict (on success from ``lookup_by_pmcid``) is the
parsed BioC collection — a dict with ``documents``, ``source``,
``date``, ``infons``.  Callers who want Markdown should pass
``documents[0]`` to ``vendored.opencite.bioc_to_markdown``.

============================================================
PMC image URL story (post 2024 restructure)
============================================================

Plan v2 §13's original sketch had us hit
``https://www.ncbi.nlm.nih.gov/pmc/articles/<PMCID>/bin/<filename>``
for figure bytes.  That URL still resolves, but only as a
redirect to the article landing page (PMC restructured their
image hosting and binary URLs in 2024 — see PR-G wet-run notes
in ``.status/session_2026-05-30_pr_g_session1.md``).  Image
bytes now live on a CDN at hash-based paths like
``https://cdn.ncbi.nlm.nih.gov/pmc/blobs/<shard>/<digits>/<hash>/<filename>``;
the shard and per-image hash are not predictable from PMCID
+ filename.

``fetch_image`` therefore does a two-stage fetch:

  1. Fetch the article landing page HTML at
     ``https://pmc.ncbi.nlm.nih.gov/articles/<PMCID>/``.  Parse
     all ``<img src="...cdn.ncbi.nlm.nih.gov/.../<filename>">``
     occurrences into a ``{filename → CDN URL}`` map.  The map
     is cached per-process by canonical PMCID via
     :data:`_image_url_cache`, so a paper with N figures pays
     1 landing fetch + N image fetches, not 2N.

  2. Look up the requested filename in the map; if present,
     fetch the bytes from the CDN URL.

``fetch_image`` returns ``bytes`` on success, ``None`` on any
failure (network error, non-200 status, non-image content-type,
oversize body, filename not in landing page).  No exceptions
surface.

Per D-G5 (locked 2026-05-30) there is no on-disk binary cache
in v1 — the in-process map cache covers the per-PMCID
amortisation, and the orchestrator's idempotency
(``should_skip`` on the ``.md`` path) keeps re-fetches off the
network across full runs.
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    import requests
except ImportError as err:
    raise ImportError("'requests' is required: pip install requests") from err

from hed_metadata_toolkit.cache import cache_get_or_fetch


logger = logging.getLogger(__name__)

_BASE = "https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json"

# PMC OA Web Service — returns XML listing the OA-distributable
# download URLs (PDF + TGZ + source XML) for articles in the PMC
# Open Access subset.  PR-H5 (2026-06-04): this is the principled
# way to discover PMC PDF URLs.  The old approach (guessing
# /pmc/articles/<PMCID>/pdf/) was broken by the 2024 PMC viewer
# migration; the new viewer gates plain HTTP behind reCAPTCHA and
# JS, but the OA service is a separate XML API designed for
# programmatic access and is not captcha-gated.  Articles not in
# the OA subset (e.g. NIH-deposited manuscripts whose publisher
# retains restrictions) return an ``idIsNotOpenAccess`` error —
# we cache that as a miss and skip the ref.
_OA_BASE = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"

# Landing-page URL on the new PMC domain.  The old
# www.ncbi.nlm.nih.gov/pmc/articles/<PMCID>/ URL also works but
# 301s here, so we save a hop by going direct.
_LANDING_BASE = "https://pmc.ncbi.nlm.nih.gov/articles"

_RATE_SEC = 0.34  # ~3 req/s per NCBI policy
_last_call: dict[str, float] = {}

# Cap on an individual image fetch.  Journal figures are typically
# 50 KiB – 2 MiB; the cap exists to prevent a misconfigured
# response from streaming indefinitely.  Legitimate figures past
# this size simply skip with a warning (caller logs + continues).
_IMAGE_MAX_BYTES_DEFAULT: int = 20 * 1024 * 1024

# Cap on the landing-page HTML.  Real PMC landing pages run
# ~100-300 KiB; 5 MiB is comfortably larger and would only trip
# on a misconfigured response.
_LANDING_MAX_BYTES_DEFAULT: int = 5 * 1024 * 1024

# Per-process cache: canonical PMCID -> {filename: CDN URL}.
# Populated lazily on first call to :func:`_fetch_image_url_map`
# for a given PMCID; cleared by :func:`reset_image_url_cache` in
# tests.  Missing key = not yet fetched; present key (even with
# empty dict) = landing-page fetch succeeded and parsed.
_image_url_cache: dict[str, dict[str, str]] = {}


def reset_image_url_cache() -> None:
    """Clear the per-process landing-page URL map cache.

    Tests call this between cases so an earlier test's cached map
    does not leak into a later test.
    """
    _image_url_cache.clear()


# Match ``<img ... src="<url>...<filename>.<ext>">`` where ``<url>``
# contains ``cdn.ncbi.nlm.nih.gov`` (the CDN host PMC uses for
# figure bytes).  Group 1 captures the full URL; group 2 captures
# just the trailing filename so the caller can index by it.
#
# The regex deliberately tolerates either single or double quotes
# and any attribute order inside the ``<img>`` tag.  HTML5 self-
# closing ``/>`` is also matched.
_IMG_SRC_RE: re.Pattern[str] = re.compile(
    r"""<img\b[^>]*?\bsrc=["']
        ([^"']*cdn\.ncbi\.nlm\.nih\.gov[^"']*?
            /([^"'/]+\.(?:jpg|jpeg|png|gif|svg|tif|tiff|webp)))
        ["']""",
    re.IGNORECASE | re.VERBOSE,
)


def _throttle(host: str) -> None:
    now = time.monotonic()
    gap = now - _last_call.get(host, 0.0)
    if gap < _RATE_SEC:
        time.sleep(_RATE_SEC - gap)
    _last_call[host] = time.monotonic()


def _normalise_pmcid(pmcid: str) -> str:
    """Normalise ``"PMC123456"`` / ``"123456"`` / ``" pmc 123456 "`` to ``"PMC123456"``.

    Empty / non-string / unparseable input returns an empty string —
    the caller short-circuits to a cache miss before any network call.
    """
    if not isinstance(pmcid, str):
        return ""
    s = pmcid.strip().upper()
    if not s:
        return ""
    if s.startswith("PMC"):
        rest = s[3:].strip()
        return f"PMC{rest}" if rest.isdigit() else ""
    if s.isdigit():
        return f"PMC{s}"
    return ""


def _get(url: str, headers: dict | None = None) -> dict | None:
    host = "ncbi.nlm.nih.gov"
    for attempt in range(3):
        _throttle(host)
        try:
            resp = requests.get(url, headers=headers, timeout=15)
        except requests.RequestException as exc:
            logger.warning("pmc network error (attempt %d): %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(2)
                continue
            return None

        status = resp.status_code
        if status == 200:
            # NCBI's BioC endpoint signals "PMCID not in OA subset" by
            # returning HTTP 200 with a text/html error page
            # (body starts "[Error] : No result can be found. <BR>...").
            # Treat that as a cacheable miss, not a parse error.
            ctype = resp.headers.get("content-type", "").lower()
            if "json" not in ctype:
                logger.info(
                    "pmc 200 non-json content-type=%r for %s (treating as not-in-OA)",
                    ctype, url,
                )
                return {}
            try:
                return resp.json()
            except ValueError as exc:
                # JSON content-type but unparseable -> transient error,
                # do NOT cache (return None so caller retries next session).
                logger.warning("pmc 200 json but invalid JSON for %s: %s", url, exc)
                return None
        if status == 404:
            return {}
        if status == 429:
            logger.warning("pmc 429 rate-limit; waiting 2 s")
            time.sleep(2)
            continue
        if status >= 500:
            logger.warning("pmc %d server error; waiting 2 s", status)
            time.sleep(2)
            continue
        logger.info("pmc %d for %s", status, url)
        return {}
    return None


def lookup_by_pmcid(
    pmcid: str,
    cache_dir: Path,
    email: str = "hedannotation@gmail.com",
) -> dict | None:
    """Return the PMC BioC document for ``pmcid``, or ``None`` if not found / error.

    The returned dict is the parsed BioC collection; useful sub-keys
    are ``documents`` (list of articles), ``source``, ``date``.
    Pass ``documents[0]`` to ``vendored.opencite.bioc_to_markdown``
    for a Markdown conversion.
    """
    canonical = _normalise_pmcid(pmcid)
    if not canonical:
        logger.info("source=pmc pmcid=%r status=invalid", pmcid)
        return None

    url = f"{_BASE}/{canonical}/unicode"
    headers = {"User-Agent": f"hed-task/1.0 (mailto:{email})"}

    def _fetch() -> dict | None:
        data = _get(url, headers=headers)
        if data is None:
            return None
        # Empty list / empty dict / falsy → not in OA subset; cache as miss.
        if not data:
            return {}
        # PMC sometimes returns a list wrapping the collection.
        if isinstance(data, list):
            if not data:
                return {}
            data = data[0]
        if not isinstance(data, dict):
            return {}
        return data

    cached = cache_get_or_fetch(
        cache_dir=cache_dir,
        source="pmc_bioc",
        key=canonical,
        fetch=_fetch,
        stable=True,  # BioC for a published article is stable.
    )

    if not cached:
        logger.info("source=pmc pmcid=%s status=not_found", canonical)
        return None

    cached["_source"] = "pmc_bioc"
    cached["_pmcid"] = canonical

    logger.info("source=pmc pmcid=%s status=200", canonical)
    return cached


# ---------------------------------------------------------------------------
# PMC OA Web Service (PR-H5, 2026-06-04)
# ---------------------------------------------------------------------------

def _normalise_oa_href(href: str) -> str:
    """Convert FTP-scheme hrefs to HTTPS.

    The OA service historically returned ``ftp://ftp.ncbi.nlm.nih.gov/...``
    URLs; the same paths are reachable over HTTPS at the same host
    and our HTTP fetcher doesn't speak FTP.  Other schemes pass
    through unchanged.
    """
    s = (href or "").strip()
    if s.lower().startswith("ftp://"):
        return "https://" + s[len("ftp://"):]
    return s


def lookup_oa_pdf_url(
    pmcid: str,
    cache_dir: Path,
    email: str = "hedannotation@gmail.com",
) -> str | None:
    """Return the OA PDF download URL for ``pmcid``, or None.

    Queries the PMC OA Web Service::

        GET https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id=<PMCID>

    Response is XML.  On an OA-subset hit the body contains one or
    more ``<link format="..." href="...">`` entries; we pick the
    first ``href`` whose ``format`` starts with ``"pdf"``.  On a
    non-OA-subset ref the body carries
    ``<error code="idIsNotOpenAccess">``; we cache that as a miss
    and return None.

    Caching is via :func:`cache.cache_get_or_fetch` with
    ``stable=True`` — OA status of a published article doesn't
    change, so the response is good indefinitely.

    Returns:
      The HTTPS PDF URL on success.
      ``None`` on: invalid PMCID, network error, non-OA-subset,
      OA-subset article with no PDF link in the response.
    """
    canonical = _normalise_pmcid(pmcid)
    if not canonical:
        logger.info("source=pmc_oa pmcid=%r status=invalid", pmcid)
        return None

    url = f"{_OA_BASE}?id={canonical}"
    headers = {"User-Agent": f"hed-task/1.0 (mailto:{email})"}

    def _fetch() -> dict | None:
        _throttle("www.ncbi.nlm.nih.gov")
        try:
            resp = requests.get(url, headers=headers, timeout=15)
        except requests.RequestException as exc:
            logger.info("pmc_oa network error %s: %s", canonical, exc)
            return None  # don't cache transient failures
        if resp.status_code != 200:
            logger.info("pmc_oa HTTP %d for %s", resp.status_code, canonical)
            return {}  # cache as miss
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            logger.info("pmc_oa XML parse error %s: %s", canonical, exc)
            return {}
        err = root.find(".//error")
        if err is not None:
            return {"_error_code": err.get("code") or "",
                    "_error_text": (err.text or "").strip()}
        links = []
        for link_el in root.findall(".//link"):
            links.append({
                "format": link_el.get("format", "") or "",
                "href":   link_el.get("href", "") or "",
            })
        return {"links": links}

    cached = cache_get_or_fetch(
        cache_dir=cache_dir,
        source="pmc_oa",
        key=canonical,
        fetch=_fetch,
        stable=True,
    )
    if not cached:
        logger.info("source=pmc_oa pmcid=%s status=not_found", canonical)
        return None
    if cached.get("_error_code"):
        logger.info("source=pmc_oa pmcid=%s status=%s",
                    canonical, cached["_error_code"])
        return None

    for link in cached.get("links") or []:
        fmt = (link.get("format") or "").lower()
        if fmt.startswith("pdf"):
            href = _normalise_oa_href(link.get("href") or "")
            if href:
                logger.info("source=pmc_oa pmcid=%s status=200 href=%s",
                            canonical, href)
                return href

    logger.info("source=pmc_oa pmcid=%s status=200_no_pdf_link", canonical)
    return None


# ---------------------------------------------------------------------------
# PMC image two-stage fetcher  (PR-G, plan v2 §13)
# ---------------------------------------------------------------------------

def _stream_body(
    resp: "requests.Response",
    *,
    max_bytes: int,
    label: str,
) -> bytes | None:
    """Stream ``resp.iter_content`` into a single ``bytes`` object,
    capped at ``max_bytes``.  Returns ``None`` on overflow or
    network error.  ``label`` is the diagnostic tag used in log
    messages so the caller can identify which call leg failed.
    """
    buf = bytearray()
    try:
        for chunk in resp.iter_content(chunk_size=65_536):
            if not chunk:
                continue
            buf.extend(chunk)
            if len(buf) > max_bytes:
                logger.info("%s oversize (>%d bytes)", label, max_bytes)
                return None
    except requests.RequestException as exc:
        logger.info("%s stream error: %s", label, exc)
        return None
    return bytes(buf)


def _fetch_image_url_map(
    pmcid: str,
    *,
    timeout: float = 30.0,
    max_bytes: int = _LANDING_MAX_BYTES_DEFAULT,
    email: str = "hedannotation@gmail.com",
    session: "requests.Session | None" = None,
) -> dict[str, str] | None:
    """Return the ``{filename → CDN URL}`` map for ``pmcid``,
    fetching and parsing the landing page on first call.

    Cached per process by canonical PMCID; subsequent calls return
    the cached value (possibly empty) without re-fetching.  Cache
    misses on transient errors (network exception, non-200, oversize)
    return ``None`` and are NOT cached, so a retry inside the same
    process gets another chance.

    ``pmcid`` is canonicalised by :func:`_normalise_pmcid` before
    caching / fetching — callers can pass any of the common
    shapes.
    """
    canonical = _normalise_pmcid(pmcid)
    if not canonical:
        return None

    cached = _image_url_cache.get(canonical)
    if cached is not None:
        return cached

    url = f"{_LANDING_BASE}/{canonical}/"
    host = "ncbi.nlm.nih.gov"
    _throttle(host)

    headers = {
        "User-Agent": f"hed-task/1.0 (mailto:{email})",
        "Accept": "text/html",
    }

    try:
        getter = session.get if session is not None else requests.get
        resp = getter(url, headers=headers, timeout=timeout, stream=True)
    except requests.RequestException as exc:
        logger.info("pmc_landing network error %s: %s", canonical, exc)
        return None

    try:
        if resp.status_code != 200:
            logger.info("pmc_landing %d for %s",
                        resp.status_code, canonical)
            return None

        body = _stream_body(resp, max_bytes=max_bytes,
                            label=f"pmc_landing {canonical}")
        if body is None:
            return None
    finally:
        try:
            resp.close()
        except Exception:                              # noqa: BLE001
            logger.debug("response close raised; ignored", exc_info=True)

    try:
        html = body.decode("utf-8", errors="replace")
    except Exception as exc:                           # noqa: BLE001
        logger.info("pmc_landing decode error %s: %s", canonical, exc)
        return None

    url_map: dict[str, str] = {}
    for match in _IMG_SRC_RE.finditer(html):
        full_url, fname = match.group(1), match.group(2)
        # First wins (a landing page may reference the same figure
        # in multiple places — thumbnail + full size — and we want
        # the first match, which is typically the full-size copy
        # near the figure caption).
        url_map.setdefault(fname, full_url)

    _image_url_cache[canonical] = url_map
    logger.info("pmc_landing parsed %s: %d image url(s)",
                canonical, len(url_map))
    return url_map


def fetch_image(
    pmcid: str,
    filename: str,
    *,
    timeout: float = 30.0,
    max_bytes: int = _IMAGE_MAX_BYTES_DEFAULT,
    email: str = "hedannotation@gmail.com",
    session: "requests.Session | None" = None,
) -> bytes | None:
    """Fetch a figure's bytes given a PMCID + filename.

    Two-stage flow (see module docstring "PMC image URL story"):

      1. Resolve the CDN URL for ``filename`` via
         :func:`_fetch_image_url_map`, which fetches the landing
         page once per PMCID and caches the parse.
      2. Download the bytes from the resolved CDN URL, checking
         status, content-type, and size as we go.

    Args:
        pmcid: A PMCID in any common shape (``"PMC4097944"``,
            ``"4097944"``, ``" pmc 4097944 "``).  Same normaliser
            as :func:`lookup_by_pmcid`.
        filename: The figure's filename as reported by the BioC
            ``infons["file"]`` field — e.g. ``"gr1.jpg"``.  Must
            match the URL's trailing filename component on PMC's
            CDN exactly (BioC ``infons["file"]`` values are
            kept verbatim by PMC, so this is a stable assumption).
        timeout: Per-request HTTP timeout, seconds.
        max_bytes: Body-size ceiling for the image download.
        email: Polite-pool identifier for the User-Agent header.
        session: Optional :class:`requests.Session` for tests to
            inject a fake.  Production callers omit it.

    Returns:
        Raw image bytes on success.  ``None`` on any failure
        (invalid PMCID, empty filename, landing-page fetch /
        parse error, filename not in landing-page map, image
        fetch error, non-image content-type, oversize body).
        No exceptions surface — the caller skips the image and
        logs.

    Shares the per-host throttle (3 req/s) with
    :func:`lookup_by_pmcid` via the module-level ``_last_call``
    map; back-to-back calls across both functions are coordinated
    against NCBI's rate-limit ceiling.
    """
    canonical = _normalise_pmcid(pmcid)
    if not canonical:
        logger.info("source=pmc_image pmcid=%r status=invalid", pmcid)
        return None
    if not isinstance(filename, str) or not filename.strip():
        logger.info("source=pmc_image pmcid=%s filename=%r status=invalid",
                    canonical, filename)
        return None
    filename = filename.strip()

    # ---- Stage 1: resolve CDN URL via the landing-page map.
    url_map = _fetch_image_url_map(
        canonical, timeout=timeout, email=email, session=session,
    )
    if url_map is None:
        # Transient landing-page fetch error; logged inside the helper.
        logger.info("source=pmc_image pmcid=%s filename=%s status=landing_fail",
                    canonical, filename)
        return None

    url = url_map.get(filename)
    if url is None:
        # Landing page parsed fine, but the filename is not there.
        # Treat as a miss — BioC ``infons["file"]`` may reference
        # a supplementary image absent from the article body, or
        # PMC may have changed its markup.
        logger.info("source=pmc_image pmcid=%s filename=%s status=not_in_landing",
                    canonical, filename)
        return None

    # ---- Stage 2: fetch bytes from the CDN URL.
    host = "ncbi.nlm.nih.gov"
    _throttle(host)

    headers = {
        "User-Agent": f"hed-task/1.0 (mailto:{email})",
        "Accept": "image/*",
    }

    try:
        getter = session.get if session is not None else requests.get
        resp = getter(url, headers=headers, timeout=timeout, stream=True)
    except requests.RequestException as exc:
        logger.info("pmc_image network error %s/%s: %s",
                    canonical, filename, exc)
        return None

    try:
        if resp.status_code != 200:
            logger.info("pmc_image %d for %s/%s",
                        resp.status_code, canonical, filename)
            return None

        raw_ctype = resp.headers.get("Content-Type", "") or ""
        ctype = raw_ctype.split(";", 1)[0].strip().lower()
        if not ctype.startswith("image/"):
            logger.info("pmc_image non-image content-type=%r for %s/%s",
                        ctype, canonical, filename)
            return None

        body = _stream_body(resp, max_bytes=max_bytes,
                            label=f"pmc_image {canonical}/{filename}")
        if body is None:
            return None
    finally:
        try:
            resp.close()
        except Exception:                              # noqa: BLE001
            logger.debug("response close raised; ignored", exc_info=True)

    logger.info("source=pmc_image pmcid=%s filename=%s status=200 bytes=%d",
                canonical, filename, len(body))
    return body
