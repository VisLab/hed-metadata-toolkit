"""
cache.py — Deterministic, immutable disk cache for API responses.

One public function: cache_get_or_fetch.

Two cache layouts, picked per-call via the `stable` flag:

  Date-stamped (default; for queries whose results drift over time):
      <cache_dir>/<source>/<YYYY-MM-DD>/<sha1_16hex>.json
      Writes go into today's bucket.  Reads scan every date-stamped
      bucket and serve the newest copy whose date is within
      `max_age_days` (default 30).  Earlier date subdirectories are
      retained as an audit trail of when each fetch happened; they are
      no longer treated as stale just because the calendar rolled over.
      Use for search endpoints, where new papers appear over time but
      not so fast that a 30-day-old result is misleading.

  Stable (for lookups whose results are essentially fixed):
      <cache_dir>/<source>/stable/<sha1_16hex>.json
      No date in the path. Once written, served indefinitely.
      Use for lookup-by-DOI calls (a paper's metadata, OA status, etc.
      is stable; daily re-fetching is wasteful). Pass `stable=True`
      from the caller.

Each cache file is self-describing:
    {"source": "...", "key": "...", "fetched_on": "...", "response": {...}}

The key and source are stored alongside the response so that a SHA-1
key collision is detectable on read (mismatch between the stored key and
the requested key).

Stable cache entries have no automatic expiry. To force a refresh of a
stable entry, delete the file (or the whole `<source>/stable/` directory).

To force a refresh of a date-stamped query, either pass `max_age_days=0`
for one call (which makes the lookback include today only — a
file from yesterday will not match) or delete the matching
`<source>/<YYYY-MM-DD>/<hash>.json` files older than today.

This mirrors the raw/ convention in OpenAlex/pull_openalex.py.

Error semantics:
    fetch() returning None  → server/network error; do NOT write to disk so
                              the next run retries the live API.
    fetch() returning {}    → legitimate "not found" (e.g. 404); write to
                              disk to avoid pointless retries.
    fetch() returning {...} → success; write to disk.

Write atomicity:
    Cache writes use a write-to-tmp-then-rename pattern.  The payload is
    serialised into a per-PID sibling file (`<hash>.json.<pid>.tmp`) and
    only after that write completes is the tmp file atomically renamed
    onto the final cache_path.  This guarantees:
      - A process killed mid-write leaves no partial cache_path; either
        the rename happened (full file present) or it did not (no file
        or the previous good file).
      - Two writers using the same key produce two different tmp
        filenames; whichever rename lands second wins cleanly without
        interleaved bytes.
    POSIX rename(2) and Windows MoveFileExW (called by Path.replace)
    are atomic on the same filesystem.  The tmp file is created next
    to the destination, so the rename never crosses a filesystem
    boundary.  We deliberately do NOT fsync — the cache is ephemeral
    and "lose the cache on power-loss" is acceptable.
"""

import hashlib
import json
import logging
import os
import re
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


# Date subdirectory pattern: YYYY-MM-DD.  Matches what cache writes produce
# and rejects other directory names like "stable" or scratch dirs.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _cache_key_hex(key: str) -> str:
    """Return 16 hex characters of SHA-1 over the key string."""
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _find_recent_date_stamped(
    source_dir: Path,
    cache_hex: str,
    today: str,
    max_age_days: int,
) -> list[Path]:
    """Return matching cache files within the staleness window, newest first.

    Walks every `<source_dir>/<YYYY-MM-DD>/` subdirectory, keeping only
    those whose date string lies in the closed interval
    [today − max_age_days, today].  Among the survivors, returns the
    cache files that exist for `cache_hex`, sorted newest-date first.

    Returning a list rather than a single best candidate lets the
    caller fall through to an older copy when the newest one is
    corrupt or has a stored-key mismatch.

    `today` is passed in as an ISO string so callers (especially tests)
    can pin "today" deterministically.  `max_age_days=0` restricts the
    window to today only, reproducing the pre-staleness-window
    behaviour for callers that want it.
    """
    if not source_dir.exists() or max_age_days < 0:
        return []

    today_date = date.fromisoformat(today)
    cutoff_date = today_date - timedelta(days=max_age_days)
    cutoff_iso = cutoff_date.isoformat()

    candidates: list[tuple[str, Path]] = []
    for entry in source_dir.iterdir():
        if not entry.is_dir() or not _DATE_RE.match(entry.name):
            continue
        # Bound: include cutoff (inclusive) up to today (inclusive).
        # Exclude future-dated buckets defensively in case of clock skew
        # or testing with `today` overrides.
        if entry.name < cutoff_iso or entry.name > today:
            continue
        cache_path = entry / f"{cache_hex}.json"
        if cache_path.exists():
            candidates.append((entry.name, cache_path))

    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return [path for _, path in candidates]


def cache_get_or_fetch(
    cache_dir: Path,
    source: str,
    key: str,
    fetch: Callable[[], dict | None],
    today: str | None = None,
    stable: bool = False,
    max_age_days: int = 30,
) -> dict:
    """Return the cached JSON for (source, key).

    If absent, call fetch() once.  If fetch() returns a non-None value,
    persist it and return it.  If fetch() returns None (server/network
    error), do NOT persist — return {} so the caller gets a miss and the
    next run can retry the live API.

    Args:
        cache_dir:     Root cache directory.
        source:        One of "openalex", "crossref", "europepmc",
                       "semanticscholar", "unpaywall".
        key:           Arbitrary string identifying the request (e.g. the DOI).
        fetch:         Zero-argument callable returning dict | None.
                       None signals a transient error (should not be cached).
                       {} signals a legitimate not-found (safe to cache).
        today:         "YYYY-MM-DD" override; defaults to date.today().isoformat().
                       Used for both write-bucket selection and the staleness
                       window's upper bound.  Ignored when stable=True for
                       reads (stable lookups have no date), but still used
                       for the `fetched_on` field of any new write.
        stable:        When True, use the stable (no-date) cache layout under
                       <cache_dir>/<source>/stable/. Use this for lookups whose
                       results are essentially fixed (e.g. lookup_by_doi calls);
                       delete the file manually to force a refresh. Default
                       False keeps the date-stamped layout for search-style
                       calls whose results drift over time.
        max_age_days:  Staleness window for date-stamped reads.  A cached
                       file is served if its date subdirectory lies within
                       [today − max_age_days, today].  Default 30.  Ignored
                       when stable=True.  Set to 0 to restrict the lookup
                       to today's bucket only.

    Returns:
        The response dict, or {} if not found / error.
    """
    if today is None:
        today = date.today().isoformat()

    cache_hex = _cache_key_hex(key)

    # Read path: build a list of candidate cache files, newest first.
    if stable:
        stable_path = Path(cache_dir) / source / "stable" / f"{cache_hex}.json"
        candidates = [stable_path] if stable_path.exists() else []
    else:
        candidates = _find_recent_date_stamped(
            Path(cache_dir) / source, cache_hex, today, max_age_days,
        )

    # Try each candidate, newest first.  A corrupt or wrong-keyed file
    # falls through to the next-older candidate; only when every
    # candidate fails do we fetch fresh.
    for cand_path in candidates:
        try:
            data = json.loads(cand_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning(
                "Corrupted cache file %s (%s); trying next-older candidate.",
                cand_path, exc,
            )
            continue
        if data.get("source") != source or data.get("key") != key:
            logger.warning(
                "Cache key collision or metadata mismatch for %s/%s "
                "(stored key=%r, stored source=%r); trying next-older candidate.",
                source, cache_hex, data.get("key"), data.get("source"),
            )
            continue
        logger.debug("cache_hit source=%s key=%r path=%s", source, key, cand_path)
        return data["response"]

    # Cache miss: call the fetch callable.
    response = fetch()

    if response is None:
        # Transient error (network failure, 5xx after retries).
        # Do not write to disk — allow the next run to retry the live API.
        logger.debug("cache_skip source=%s key=%r (fetch returned None)", source, key)
        return {}

    # Write to disk (includes the {} "not found" case).
    # Atomic: serialise to a per-PID sibling tmp file, then atomically
    # rename onto cache_path.  See module docstring "Write atomicity".
    # Stable writes go to <source>/stable/; date-stamped writes always
    # land in today's bucket regardless of max_age_days.
    bucket = "stable" if stable else today
    cache_path = Path(cache_dir) / source / bucket / f"{cache_hex}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": source,
        "key": key,
        "fetched_on": today,
        "response": response,
    }
    tmp_path = cache_path.with_suffix(cache_path.suffix + f".{os.getpid()}.tmp")
    try:
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(cache_path)
    except OSError:
        # Clean up a leftover tmp file before propagating, so the
        # cache directory does not accumulate orphans on disk-full
        # or permission errors.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    logger.debug("cache_write source=%s key=%r -> %s", source, key, cache_path)

    return response
