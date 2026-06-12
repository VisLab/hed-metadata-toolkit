"""generate_review_queue.py — Emit a JSON template for manual curator review.

Curation loop (see src/apply_manual_fills.py for step 3 details):

    1. python src/generate_review_queue.py
         → writes datasets/dataset_summaries/manual_review_<date>.json
    2. Curator opens the JSON in an editor.  For each entry, fills ONE of:
         a. {"doi": "10.xxxx/yyyy"}              ← preferred when DOI is found
         b. {"family": "Lastname",
             "year": 2020,
             "title": "Full title string"}      ← when no DOI exists
         c. {"rejected": "reason text"}          ← when no paper / wrong link
       The curator may also delete entries they don't want to act on this
       round; missing entries simply don't get processed.
    3. python src/apply_manual_fills.py \\
               --input datasets/dataset_summaries/manual_review_<date>.json \\
               --write-back
    4. python src/enrich_pub_ids.py --write-back
         → enriches any DOIs the curator just supplied
    5. python src/generate_review_queue.py
         → writes the next iteration's manual_review_<next-date>.json
         → loops until queue is empty or known-irreducible

Hint computation:
  - OSF hints: pure cache reads from 2.5C's Path D stable cache (no new API calls).
  - Crossref candidates: fresh title-search query per run, cached date-stamped (30 days).

Usage:
    python src/generate_review_queue.py [--registry PATH]
                                        [--output PATH]
                                        [--no-hints]
                                        [--limit N]
                                        [--cache-dir PATH]
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# Default data/output paths live under the current working directory (run the
# command from the consumer repo root); all are overridable via CLI flags.
_ROOT = Path.cwd()

from hed_metadata_toolkit.cache import cache_get_or_fetch  # noqa: E402
from hed_metadata_toolkit.clients.osf import extract_publication_metadata  # noqa: E402

try:
    import requests
except ImportError as err:
    raise ImportError("'requests' is required: pip install requests") from err

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"resolved", "rejected", "not_a_citation"}
_DEFAULT_CACHE_DIR = _ROOT / "outputs" / "cache"
_REGISTRY_DEFAULT = _ROOT / "datasets" / "dataset_summaries" / "citation_registry.tsv"

_OSF_URL_RE = re.compile(
    r"^https?://(www\.)?osf\.io/(?P<rest>[^?&#\s]+)", re.IGNORECASE
)
_CR_API_BASE = "https://api.crossref.org"
_CR_RATE_SEC = 0.2

# Module-level rate-limit tracker for Crossref (mutable list avoids 'global')
_cr_last: list[float] = [0.0]


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def today_iso() -> str:
    return datetime.date.today().isoformat()


def _ascii_fold_lower(s: str) -> str:
    norm = unicodedata.normalize("NFKD", s)
    return "".join(c for c in norm if unicodedata.combining(c) == 0).lower()


def _title_token_overlap(t1: str, t2: str) -> float:
    """Jaccard coefficient of lowercased alphanumeric title tokens."""

    def tokens(t: str) -> set[str]:
        return set(re.sub(r"[^a-z0-9]", " ", _ascii_fold_lower(t)).split())

    a, b = tokens(t1), tokens(t2)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _stable_cache_exists(cache_dir: Path, source: str, key: str) -> bool:
    cache_hex = hashlib.sha1(key.encode()).hexdigest()[:16]
    return (cache_dir / source / "stable" / f"{cache_hex}.json").exists()


# ---------------------------------------------------------------------------
# OSF hint extraction (cache-only reads)
# ---------------------------------------------------------------------------


def _parse_osf_guid(url: str) -> str | None:
    """Extract the GUID from an OSF URL; returns None if not an OSF URL."""
    m = _OSF_URL_RE.match(url)
    if not m:
        return None
    rest = m.group("rest").strip("/")
    parts = rest.split("/")
    if not parts or not parts[0]:
        return None
    # osf.io/preprints/<provider>/<guid>
    if parts[0] == "preprints" and len(parts) >= 3:
        return parts[2]
    # osf.io/<guid> or osf.io/<guid>/files/...
    return parts[0]


def _read_osf_stable(cache_dir: Path, key: str) -> dict:
    """Read from the OSF stable cache without ever making a network call."""
    return cache_get_or_fetch(
        cache_dir=cache_dir,
        source="osf",
        key=key,
        fetch=lambda: None,  # never fetch fresh; return {} on miss
        stable=True,
    )


def _build_osf_hints(url: str, cache_dir: Path) -> dict:
    """Build OSF-specific hints from cached GUID + typed API responses.

    Pure cache reads — no fresh OSF API calls.  Returns {} when the cache
    has no entry for this GUID (not an error; the resolver may not have
    visited this row's URL yet).  Returns {"osf_private": True} when the
    cache holds a cached 401/404 response ({}).
    """
    guid = _parse_osf_guid(url)
    if not guid:
        return {}

    guid_key = f"guid:{guid}"
    guid_data = _read_osf_stable(cache_dir, guid_key)

    if not guid_data:
        # cache_get_or_fetch returns {} for both "not cached" and "cached {}".
        # Distinguish by checking whether the stable cache file actually exists.
        if _stable_cache_exists(cache_dir, "osf", guid_key):
            return {"osf_private": True}
        return {}

    # Two possible GUID response shapes from the OSF API:
    # (A) GUID response IS the object: data.type in {nodes, registrations, ...}
    # (B) GUID response contains a referent link: data.relationships.referent.data
    data_block = guid_data.get("data", {})
    obj_type_direct = data_block.get("type", "")
    obj_id_direct = data_block.get("id", "")

    if obj_type_direct in ("nodes", "registrations", "preprints", "files"):
        # Shape (A): GUID response is the object — use it directly.
        typed_data = guid_data
    else:
        # Shape (B): extract referent to get (type, id), then read typed cache.
        referent = (
            data_block.get("relationships", {}).get("referent", {}).get("data", {})
        )
        obj_type_direct = referent.get("type", "")
        obj_id_direct = referent.get("id", "")
        if not obj_type_direct or not obj_id_direct:
            return {}
        typed_data = _read_osf_stable(cache_dir, f"{obj_type_direct}:{obj_id_direct}")
        if not typed_data:
            return {"osf_type": obj_type_direct}

    meta = extract_publication_metadata(typed_data)

    hints: dict = {}
    if meta.get("type"):
        hints["osf_type"] = meta["type"]
    if meta.get("title"):
        hints["osf_title"] = meta["title"]

    contributors = meta.get("contributor_families") or []
    if contributors:
        hints["osf_contributors"] = contributors[:5]

    desc = meta.get("description_excerpt") or ""
    if desc:
        hints["osf_description_excerpt"] = desc[:300]

    desc_dois = meta.get("description_doi_candidates") or []
    if desc_dois:
        hints["osf_description_dois"] = desc_dois

    return hints


# ---------------------------------------------------------------------------
# Crossref title-search candidates (fresh call, date-stamped cache)
# ---------------------------------------------------------------------------


def _throttle_crossref() -> None:
    now = time.monotonic()
    gap = now - _cr_last[0]
    if gap < _CR_RATE_SEC:
        time.sleep(_CR_RATE_SEC - gap)
    _cr_last[0] = time.monotonic()


def _crossref_title_search_items(
    title: str,
    cache_dir: Path,
    today: str,
    email: str,
) -> list[dict]:
    """Fetch top 5 Crossref works for a title query.

    Returns the raw 'items' list, or [] on miss/error.
    Result is cached date-stamped (30-day staleness window).
    """
    title_norm = re.sub(r"\s+", " ", title.strip().lower())
    cache_key = f"crossref_title_search|{title_norm}"

    def _fetch() -> dict | None:
        try:
            _throttle_crossref()
            resp = requests.get(
                f"{_CR_API_BASE}/works",
                headers={"User-Agent": f"hed-task/1.0 (mailto:{email})"},
                params={"query.title": title, "rows": 5, "mailto": email},
                timeout=15,
            )
        except Exception as exc:
            logger.warning("crossref title-search network error: %s", exc)
            return None

        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            logger.warning("crossref 429 on title search; will retry next run")
            return None
        if resp.status_code >= 500:
            return None
        return {}

    raw = cache_get_or_fetch(
        cache_dir=cache_dir,
        source="crossref",
        key=cache_key,
        fetch=_fetch,
        today=today,
        stable=False,
    )

    if not raw:
        return []
    return (raw.get("message") or {}).get("items", [])


def _candidate_from_item(item: dict, query_title: str) -> dict:
    """Build a crossref_candidate dict from a Crossref works item."""
    doi = (item.get("DOI") or "").strip().lower()
    titles = item.get("title") or []
    title = titles[0] if titles else ""
    container_titles = item.get("container-title") or []
    container = container_titles[0] if container_titles else ""

    year = None
    for key in ("published", "published-print", "published-online"):
        pub = item.get(key) or {}
        dp = pub.get("date-parts") or []
        if dp and dp[0]:
            try:
                year = int(dp[0][0])
                break
            except (TypeError, ValueError):
                pass

    authors = item.get("author") or []
    first_family = ""
    if authors:
        fa = (
            next((a for a in authors if a.get("sequence") == "first"), None)
            or authors[0]
        )
        first_family = fa.get("family", "")

    overlap = _title_token_overlap(title, query_title)
    return {
        "doi": doi,
        "title": title,
        "container": container,
        "year": year,
        "first_author_family": first_family,
        "title_overlap_score": round(overlap, 3),
    }


def _build_crossref_candidates(
    query_title: str,
    contributor_families: list[str],
    cache_dir: Path,
    today: str,
    email: str,
) -> list[dict]:
    """Return top 1-2 Crossref title-search hits that pass the relevance filter.

    A candidate passes if:
      (a) first-author family matches one of the OSF contributor families
          (case-insensitive, ASCII-folded, substring match), OR
      (b) title token-overlap with query_title >= 0.5.
    """
    if not query_title.strip():
        return []

    items = _crossref_title_search_items(query_title, cache_dir, today, email)
    family_set = {_ascii_fold_lower(f) for f in contributor_families if f}

    candidates: list[dict] = []
    for item in items:
        cand = _candidate_from_item(item, query_title)
        if not cand["doi"]:
            continue

        faf = _ascii_fold_lower(cand["first_author_family"])
        author_match = bool(family_set) and any(
            faf == fam or fam in faf or faf in fam for fam in family_set
        )
        title_match = cand["title_overlap_score"] >= 0.5

        if author_match or title_match:
            candidates.append(cand)
        if len(candidates) >= 2:
            break

    return candidates


# ---------------------------------------------------------------------------
# Hint assembly
# ---------------------------------------------------------------------------


def build_hints(
    row: dict,
    cache_dir: Path,
    today: str,
    email: str,
) -> dict:
    """Build the hints dict for one queue entry.

    OSF hints come from cached API responses (no new OSF calls).
    Crossref candidates come from a fresh title-search query (cached date-stamped).
    Returns {} when no useful hints are available.
    """
    url = (row.get("url") or "").strip()
    hints: dict = {}
    osf_title = ""
    osf_families: list[str] = []

    if url and _OSF_URL_RE.match(url):
        osf_hints = _build_osf_hints(url, cache_dir)
        hints.update(osf_hints)
        osf_title = osf_hints.get("osf_title", "")
        osf_families = osf_hints.get("osf_contributors", [])

    # Build Crossref query from OSF title first, then registry title field.
    query_title = osf_title or (row.get("title") or "").strip()
    if query_title:
        candidates = _build_crossref_candidates(
            query_title, osf_families, cache_dir, today, email
        )
        if candidates:
            hints["crossref_candidates"] = candidates

    return hints


# ---------------------------------------------------------------------------
# Registry I/O
# ---------------------------------------------------------------------------


def load_registry(path: Path) -> tuple[dict[str, dict], list[str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        columns = list(reader.fieldnames or [])
        rows: dict[str, dict] = {}
        for row in reader:
            rows[row["citation_id"]] = dict(row)
    return rows, columns


# ---------------------------------------------------------------------------
# Queue generation
# ---------------------------------------------------------------------------


def generate_queue(
    registry: dict[str, dict],
    cache_dir: Path,
    today: str,
    include_hints: bool,
    limit: int | None,
    email: str,
) -> list[dict]:
    """Build the review queue list from the registry.

    Selects rows where pub_id is empty AND status is not terminal AND the
    row has either a url or a doi.
    """
    entries: list[dict] = []

    for cit_id, row in registry.items():
        if (row.get("pub_id") or "").strip():
            continue
        if (row.get("status") or "").strip() in TERMINAL_STATUSES:
            continue
        url = (row.get("url") or "").strip()
        doi = (row.get("doi") or "").strip()
        if not url and not doi:
            continue

        if limit is not None and len(entries) >= limit:
            break

        entry: dict = {
            "citation_id": cit_id,
            "url": url or f"doi:{doi}",
            "status": "needs_review",
            "doi": doi or None,
            "resolved_url": None,
            "notes": (row.get("notes") or "").strip(),
            "manual_fill": None,
        }

        if include_hints:
            hints = build_hints(row, cache_dir, today, email)
            if hints:
                entry["hints"] = hints

        entries.append(entry)

    return entries


# ---------------------------------------------------------------------------
# Atomic JSON write
# ---------------------------------------------------------------------------


def write_json_atomic(path: Path, data: list) -> None:
    """Serialise data to path via tmp-then-rename with fsync."""
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        tmp.replace(path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_email() -> str:
    """Read polite-pool email from .env (CROSSREF_MAILTO or OPENALEX_MAILTO)."""
    email = "hedannotation@gmail.com"
    env_path = _ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(("CROSSREF_MAILTO=", "OPENALEX_MAILTO=")):
                val = line.split("=", 1)[1].strip()
                if val:
                    email = val
                    break
    return email


# ---------------------------------------------------------------------------
# Library API
# ---------------------------------------------------------------------------


@dataclass
class ReviewQueueResult:
    """Outcome of :func:`run_review_queue`."""

    entries: list[dict]
    output_path: Path
    today: str
    cache_dir: Path
    include_hints: bool


def run_review_queue(
    *,
    registry_path: Path,
    output_path: Path,
    cache_dir: Path,
    email: str | None = None,
    include_hints: bool = True,
    limit: int | None = None,
    today: str | None = None,
) -> ReviewQueueResult:
    """Generate a curator review queue JSON from the citation registry.

    Library entry point.  Walks rows in ``needs_review`` state,
    enriches them with OSF metadata + Crossref candidate hints
    (when ``include_hints=True``), and writes the resulting queue to
    ``output_path``.

    Parameters
    ----------
    registry_path
        Path to ``citation_registry.tsv``.
    output_path
        Destination JSON path for the queue.
    cache_dir
        Cache root passed through to the OSF / Crossref clients.
    email
        Polite-pool address for Crossref calls.  ``None`` →
        :func:`_resolve_email` (env / repo .env / default).
    include_hints
        If False, skip OSF + Crossref enrichment (offline mode).
    limit
        Cap on the number of entries emitted.
    today
        ISO date stamped onto each entry.  ``None`` → today's UTC.
    """
    if today is None:
        today = today_iso()
    if email is None:
        email = _resolve_email()

    registry, _ = load_registry(registry_path)
    entries = generate_queue(
        registry,
        cache_dir,
        today,
        include_hints,
        limit,
        email,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(output_path, entries)

    return ReviewQueueResult(
        entries=entries,
        output_path=output_path,
        today=today,
        cache_dir=cache_dir,
        include_hints=include_hints,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_cli_cache_dir(arg_value: str | None) -> Path:
    """``--cache-dir`` > ``$HED_CACHE_DIR`` > module default."""
    if arg_value:
        return Path(arg_value)
    env_val = os.environ.get("HED_CACHE_DIR")
    if env_val:
        return Path(env_val)
    return _DEFAULT_CACHE_DIR


def main(argv: list[str] | None = None) -> int:
    """Argparse wrapper around :func:`run_review_queue`."""
    parser = argparse.ArgumentParser(
        description="Generate a manual curator review queue from the citation registry."
    )
    parser.add_argument(
        "--registry",
        default=str(_REGISTRY_DEFAULT),
        help="Path to citation_registry.tsv",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON path (default: datasets/dataset_summaries/manual_review_<date>.json)",
    )
    parser.add_argument(
        "--no-hints",
        dest="include_hints",
        action="store_false",
        default=True,
        help="Disable hint generation (OSF metadata + Crossref candidates)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of entries to emit",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help=(
            "Cache directory root.  Checked in order: this arg, $HED_CACHE_DIR, "
            "outputs/cache/ (repo-relative default)."
        ),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    today = today_iso()
    output_path = (
        Path(args.output)
        if args.output
        else _ROOT / "datasets" / "dataset_summaries" / f"manual_review_{today}.json"
    )

    result = run_review_queue(
        registry_path=Path(args.registry),
        output_path=output_path,
        cache_dir=_resolve_cli_cache_dir(args.cache_dir),
        include_hints=args.include_hints,
        limit=args.limit,
        today=today,
    )

    print(f"Generated {len(result.entries)} entries -> {result.output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
