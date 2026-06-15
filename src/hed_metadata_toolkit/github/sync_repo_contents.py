"""
sync_repo_contents.py

Fetches the top-level file/directory listing for every ds* repository in the
OpenNeuroDatasets GitHub organization and stores the results in
datasets/dataset_summaries/repo_contents.json.

Improvements over get_repo_files.py:
  - Uses the GitHub GraphQL API to batch 20 repos per request (~90 requests
    instead of 1,800 individual REST calls).
  - Stores entry type (blob / tree), file size, and git SHA for each item so
    that sync_local_files.py can do incremental, SHA-based downloads.
  - Incremental mode: compares updated_at from datasets_ordered.tsv against the
    stored synced_at timestamp and skips repos that have not changed.

Output schema (datasets/dataset_summaries/repo_contents.json):
{
  "ds000001": {
    "synced_at": "2026-04-14T12:00:00Z",
    "entries": [
      {"name": "README",                   "type": "blob", "size": 2048, "sha": "abc123"},
      {"name": "dataset_description.json", "type": "blob", "size":  512, "sha": "def456"},
      {"name": "sub-01",                   "type": "tree"}
    ]
  },
  ...
}

Failure tracking (datasets/dataset_summaries/repo_contents_failures.json):
Repos that return empty entries are recorded in a companion JSON dict keyed by
repo name.  On a subsequent successful fetch the entry is removed.  To
permanently skip a repo (e.g. a private / unreleased dataset), set its
"skip" field to true — it will then be ignored even when --retry-failed is used.

{
  "ds004169": {"reason": "empty_entries", "failed_at": "2026-04-14T17:41:55Z"},
  "ds004186": {"reason": "empty_entries", "failed_at": "2026-04-14T17:41:55Z", "skip": true}
}

Usage:
    python sync_repo_contents.py [--force] [--retry-failed] [--repo ds000001] [--tsv PATH] [--out PATH]

Options:
    --force         Re-fetch all repos even if synced_at >= updated_at
    --retry-failed  Re-attempt repos recorded in the failures dict (skip=true entries are always excluded)
    --repo NAME     Only process a single repository (useful for testing)
    --tsv PATH      Path to datasets.tsv (output of create_repo_list.py, default: ../datasets/dataset_summaries/datasets.tsv)
    --out PATH      Path to output repo_contents.json       (default: ../datasets/dataset_summaries/repo_contents.json)
"""

import argparse
import json
import os
import time
from datetime import datetime, timezone

import pandas as pd
import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GRAPHQL_URL = "https://api.github.com/graphql"
BATCH_SIZE = 10  # repos per GraphQL request; large repos (many sub-* dirs) can
# cause 502s with bigger batches — keep <=10 to stay under complexity limits
RETRY_LIMIT = 4
RETRY_BASE_DELAY = 5  # seconds; doubled on each retry (exponential backoff)
MIN_BATCH_SIZE = 1  # floor when auto-halving a batch on 5xx errors


# ---------------------------------------------------------------------------
# Windows-safe file replace
# ---------------------------------------------------------------------------


def _safe_replace(
    tmp_path: str, target_path: str, retries: int = 5, delay: float = 0.5
) -> None:
    """
    Replace target_path with tmp_path, retrying on Windows permission errors.

    On Windows, os.replace() can fail with PermissionError if the target file
    is open in an editor or locked by another process. This function retries
    with exponential backoff.
    """
    for attempt in range(1, retries + 1):
        try:
            os.replace(tmp_path, target_path)
            return
        except PermissionError:
            if attempt >= retries:
                raise  # exhausted retries
            print(
                f"    File locked, retrying in {delay}s... (attempt {attempt}/{retries})"
            )
            time.sleep(delay)
            delay *= 2  # exponential backoff


# ---------------------------------------------------------------------------
# GraphQL helpers
# ---------------------------------------------------------------------------


_TREE_ENTRIES = (
    "... on Tree { entries { name type object { ... on Blob { byteSize oid } } } }"
)


def _build_graphql_query(
    repo_names: list[str], org: str, include_subdirs: "list[str] | None" = None
) -> str:
    """Build a single GraphQL query that fetches the root tree for each repo.

    If ``include_subdirs`` is given (e.g. ``[".nemar"]``), each repo fragment
    also fetches the tree of those subdirectories, so their contents can be
    included alongside the root-level entries.
    """
    include_subdirs = include_subdirs or []
    fragments = []
    for i, name in enumerate(repo_names):
        safe = f"r{i}"  # alias must be a valid GraphQL identifier
        lines = [
            f'  {safe}: repository(owner: "{org}", name: "{name}") {{',
            "    nameWithOwner",
            f'    object(expression: "HEAD:") {{ {_TREE_ENTRIES} }}',
        ]
        for j, sub in enumerate(include_subdirs):
            lines.append(
                f'    s{j}: object(expression: "HEAD:{sub}") {{ {_TREE_ENTRIES} }}'
            )
        lines.append("  }")
        fragments.append("\n".join(lines))
    return "{\n" + "\n".join(fragments) + "\n}"


def _entry_dict(name: str, entry: dict) -> dict:
    """Normalize one GraphQL tree entry into our stored shape."""
    if entry.get("type", "blob") == "blob":
        obj = entry.get("object") or {}
        return {
            "name": name,
            "type": "blob",
            "size": obj.get("byteSize"),
            "sha": obj.get("oid"),
        }
    return {"name": name, "type": "tree"}


def _parse_graphql_response(
    payload: dict, repo_names: list[str], include_subdirs: "list[str] | None" = None
) -> dict:
    """Extract per-repo entry lists from a successful GraphQL response payload.

    Root-level hidden entries (``.github`` etc.) are skipped as before. For any
    directory named in ``include_subdirs`` (e.g. ``.nemar``), its entries are
    appended with paths prefixed by the subdir (``.nemar/<name>``); hidden names
    *inside* an included subdir are kept.
    """
    include_subdirs = include_subdirs or []
    data = payload.get("data") or {}
    results = {}
    for i, name in enumerate(repo_names):
        alias = f"r{i}"
        repo_data = data.get(alias)
        if not repo_data:
            results[name] = []
            continue
        entries = []
        tree = (repo_data.get("object") or {}).get("entries") or []
        for entry in tree:
            e_name = entry.get("name", "")
            if e_name.startswith("."):
                continue  # skip hidden top-level files/dirs
            entries.append(_entry_dict(e_name, entry))
        for j, sub in enumerate(include_subdirs):
            sub_entries = (repo_data.get(f"s{j}") or {}).get("entries") or []
            for entry in sub_entries:
                e_name = entry.get("name", "")
                if not e_name:
                    continue
                entries.append(_entry_dict(f"{sub}/{e_name}", entry))
        results[name] = entries
    return results


def _fetch_batch_once(
    repo_names: list[str],
    org: str,
    headers: dict,
    include_subdirs: "list[str] | None" = None,
) -> tuple[dict | None, bool]:
    """
    Make a single GraphQL request for the given repos.

    Returns (results_dict, is_server_error).
    results_dict is None if the request failed outright.
    is_server_error is True for 5xx responses (signals caller to halve batch).
    """
    query = _build_graphql_query(repo_names, org, include_subdirs)
    try:
        response = requests.post(
            GRAPHQL_URL,
            json={"query": query},
            headers=headers,
            timeout=60,
        )
    except requests.RequestException as exc:
        print(f"  Request error: {exc}")
        return None, False

    if _is_rate_limited(response):
        _wait_for_rate_limit(response)
        return None, False  # outer retry loop will retry immediately after the wait

    if response.status_code >= 500:
        print(f"  Server error {response.status_code} for batch of {len(repo_names)}")
        return None, True

    try:
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        print(f"  Response error: {exc}")
        return None, False

    if "errors" in payload:
        print(f"  GraphQL errors: {payload['errors']}")

    return _parse_graphql_response(payload, repo_names, include_subdirs), False


def fetch_batch(
    repo_names: list[str],
    org: str,
    headers: dict,
    include_subdirs: "list[str] | None" = None,
) -> dict:
    """
    Execute a GraphQL request for the given repos with retries and exponential
    backoff.  On 5xx errors the batch is automatically halved and each half is
    retried independently, recursing down to MIN_BATCH_SIZE = 1 if needed.

    Returns a dict mapping repo_name -> list of entry dicts.
    """
    if not repo_names:
        return {}

    delay = RETRY_BASE_DELAY
    for attempt in range(1, RETRY_LIMIT + 1):
        results, is_server_error = _fetch_batch_once(
            repo_names, org, headers, include_subdirs
        )

        if results is not None:
            return results

        if is_server_error and len(repo_names) > MIN_BATCH_SIZE:
            # Halve the batch and retry each half independently
            mid = len(repo_names) // 2
            half_a, half_b = repo_names[:mid], repo_names[mid:]
            print(f"  Halving batch: {len(half_a)} + {len(half_b)} repos")
            time.sleep(delay)
            combined = {}
            combined.update(fetch_batch(half_a, org, headers, include_subdirs))
            combined.update(fetch_batch(half_b, org, headers, include_subdirs))
            return combined

        if attempt < RETRY_LIMIT:
            print(f"  [attempt {attempt}/{RETRY_LIMIT}] Retrying in {delay}s...")
            time.sleep(delay)
            delay *= 2  # exponential backoff

    print(f"  All retries exhausted for batch of {len(repo_names)}")
    return {name: [] for name in repo_names}


# ---------------------------------------------------------------------------
# Rate-limit awareness
# ---------------------------------------------------------------------------


def _is_rate_limited(response) -> bool:
    """Return True if the response indicates a GitHub rate-limit hit."""
    if response.status_code == 429:
        return True
    if response.status_code == 403:
        return response.headers.get("x-ratelimit-remaining") == "0"
    return False


def _wait_for_rate_limit(response) -> None:
    """
    Block until the GitHub rate-limit window resets.
    Reads retry-after or x-ratelimit-reset from the response headers.
    """
    wait = 0
    retry_after = response.headers.get("retry-after")
    if retry_after:
        try:
            wait = int(retry_after)
        except ValueError:
            pass
    if not wait:
        reset_ts = response.headers.get("x-ratelimit-reset")
        if reset_ts:
            try:
                wait = max(0, int(reset_ts) - int(time.time())) + 5
            except ValueError:
                pass
    if not wait:
        wait = 60  # fallback: 60 s
    print(f"\n  Rate limit hit — waiting {wait}s until reset...")
    time.sleep(wait)
    print("  Resuming after rate-limit wait.")


def _check_rate_limit(headers: dict) -> None:
    """Print remaining GraphQL rate-limit points."""
    query = "{ rateLimit { remaining resetAt } }"
    try:
        r = requests.post(
            GRAPHQL_URL, json={"query": query}, headers=headers, timeout=10
        )
        rl = r.json().get("data", {}).get("rateLimit", {})
        print(
            f"GraphQL rate limit: {rl.get('remaining')} points remaining, resets at {rl.get('resetAt')}"
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


def _failures_path(out_path: str) -> str:
    base = os.path.splitext(out_path)[0]
    return base + "_failures.json"


def _load_failures(path: str) -> dict:
    """Load the failures dict from JSON, returning an empty dict if absent/corrupt."""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:
            print(f"Warning: could not read {path}: {exc}")
    return {}


def _save_failures(failures: dict, path: str) -> None:
    """Persist the failures dict; removes the file entirely when failures is empty."""
    if not failures:
        if os.path.exists(path):
            os.remove(path)
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(failures, fh, indent=2, ensure_ascii=False)
    _safe_replace(tmp, path)


def sync_repo_contents(
    tsv_path: str,
    out_path: str,
    token: str | None,
    organization: str = "OpenNeuroDatasets",
    force: bool = False,
    retry_failed: bool = False,
    test_repo: str | None = None,
    prefix: str = "ds",
    include_subdirs: "list[str] | None" = None,
) -> None:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    headers: dict = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"bearer {token}"

    # ------------------------------------------------------------------
    # Load repo list
    # ------------------------------------------------------------------
    try:
        df = pd.read_csv(tsv_path, sep="\t")
        print(f"Loaded {len(df)} repos from {tsv_path}")
    except Exception as exc:
        print(f"Error reading {tsv_path}: {exc}")
        return

    # Keep only repos whose name starts with the configured prefix
    # ("ds" for OpenNeuro, "nm" for NEMAR). Empty prefix keeps all repos.
    if prefix:
        df = df[df["name"].str.startswith(prefix)].reset_index(drop=True)
    print(f"{len(df)} {prefix or '(all)'}* repos to consider")

    if test_repo:
        df = df[df["name"] == test_repo].reset_index(drop=True)
        if df.empty:
            print(f"Repo '{test_repo}' not found in TSV.")
            return
        print(f"Single-repo mode: {test_repo}")

    # ------------------------------------------------------------------
    # Load existing repo_contents.json
    # ------------------------------------------------------------------
    existing: dict = {}
    if os.path.exists(out_path):
        try:
            with open(out_path, "r", encoding="utf-8") as fh:
                existing = json.load(fh)
            print(f"Loaded existing data for {len(existing)} repos from {out_path}")
        except Exception as exc:
            print(f"Warning: could not read {out_path}: {exc}")

    # ------------------------------------------------------------------
    # Load failures dict (repo_contents_failures.json)
    # ------------------------------------------------------------------
    fail_file = _failures_path(out_path)
    failures: dict = _load_failures(fail_file)
    print(
        f"Failures dict: {len(failures)} repos ({sum(1 for v in failures.values() if v.get('skip'))} skipped permanently)"
    )

    # Migrate any legacy status="failed" entries from repo_contents.json
    migrated = 0
    for name, entry in list(existing.items()):
        if entry.get("status") == "failed":
            if name not in failures:
                failures[name] = {
                    "reason": "empty_entries",
                    "failed_at": entry.get("synced_at", now_iso),
                }
            # Remove status field; strip empty entry shell if no real entries kept
            entry.pop("status", None)
            if not entry.get("entries"):
                del existing[name]
            migrated += 1
    if migrated:
        print(f"Migrated {migrated} legacy status=failed entries to failures dict")
        _save_failures(failures, fail_file)

    # ------------------------------------------------------------------
    # Decide which repos need a refresh
    # ------------------------------------------------------------------
    to_fetch: list[str] = []
    skipped = 0

    for _, row in df.iterrows():
        name = row["name"]
        updated_at = str(row.get("updated_at", ""))

        if not force:
            if name in failures:
                if failures[name].get("skip"):
                    skipped += 1
                    continue  # permanently skipped — ignore even with --retry-failed
                if not retry_failed:
                    skipped += 1
                    continue
            elif name in existing:
                synced_at = existing[name].get("synced_at", "")
                if synced_at and synced_at >= updated_at:
                    skipped += 1
                    continue

        to_fetch.append(name)

    print(f"Repos to fetch: {len(to_fetch)}  |  Skipped (up-to-date): {skipped}")
    if not to_fetch:
        print("Nothing to do.")
        return

    _check_rate_limit(headers)

    # ------------------------------------------------------------------
    # Batch GraphQL fetches
    # ------------------------------------------------------------------
    total_batches = (len(to_fetch) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"Batch size: {BATCH_SIZE}  |  Total batches: {total_batches}")
    fetched = 0
    errors = 0

    for batch_num, start in enumerate(range(0, len(to_fetch), BATCH_SIZE), 1):
        batch = to_fetch[start : start + BATCH_SIZE]
        print(f"\nBatch {batch_num}/{total_batches}: {batch}")

        results = fetch_batch(batch, organization, headers, include_subdirs)

        for name, entries in results.items():
            if entries:
                existing[name] = {
                    "synced_at": now_iso,
                    "entries": entries,
                }
                if name in failures:
                    del failures[name]
                fetched += 1
            else:
                errors += 1
                print(
                    f"  Warning: empty entries for {name} — recorded in failures dict"
                )
                failures[name] = {"reason": "empty_entries", "failed_at": now_iso}
                # Keep any previously-fetched entries in repo_contents.json but
                # do not update synced_at — the repo is effectively unchanged.
                if name not in existing:
                    existing[name] = {"synced_at": "", "entries": []}

        # Save incrementally after each batch
        _save_json(existing, out_path)
        _save_failures(failures, fail_file)

        # Polite delay between batches
        if batch_num < total_batches:
            time.sleep(1)

    print(f"\nDone.  Fetched: {fetched}  |  Errors: {errors}  |  Skipped: {skipped}")
    if errors:
        print(f"Failures saved to: {fail_file}")
        print('  Set "skip": true on any permanently inaccessible repos in that file.')
    _check_rate_limit(headers)


def _save_json(data: dict, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    _safe_replace(tmp, path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: "list[str] | None" = None) -> int:
    """Argparse wrapper around :func:`sync_repo_contents`.

    ``sync_repo_contents`` is already the library entry point;
    consumers can call it directly with their own paths and config.
    """
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Sync repo contents from a GitHub organization to repo_contents.json",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch all repos ignoring synced_at",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Re-attempt repos in the failures dict "
        "(repos with skip=true are always excluded)",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Only process this single repo name",
    )
    parser.add_argument(
        "--tsv",
        default="datasets/dataset_summaries/datasets.tsv",
        help="Path to datasets.tsv (output of fetch_repo_list)",
    )
    parser.add_argument(
        "--out",
        default="datasets/dataset_summaries/repo_contents.json",
        help="Path to output repo_contents.json",
    )
    parser.add_argument(
        "--org",
        default="OpenNeuroDatasets",
        help="GitHub organization name (default: OpenNeuroDatasets).",
    )
    parser.add_argument(
        "--prefix",
        default="ds",
        help="Only process repos whose name starts with this prefix "
        "(default: 'ds'; use 'nm' for NEMAR, '' for all repos).",
    )
    parser.add_argument(
        "--include-subdir",
        action="append",
        default=[],
        metavar="SUBDIR",
        help="Also fetch this repo subdirectory's contents and include them as "
        "'<subdir>/<file>' entries (repeatable; e.g. '.nemar').",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="GitHub PAT (defaults to $GITHUB_TOKEN).",
    )
    args = parser.parse_args(argv)

    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token:
        print(
            "Warning: GITHUB_TOKEN not set. Unauthenticated GraphQL requests are not supported."
        )

    sync_repo_contents(
        tsv_path=args.tsv,
        out_path=args.out,
        token=token,
        organization=args.org,
        force=args.force,
        retry_failed=args.retry_failed,
        test_repo=args.repo,
        prefix=args.prefix,
        include_subdirs=args.include_subdir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
