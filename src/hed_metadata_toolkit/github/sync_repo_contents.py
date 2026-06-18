"""
sync_repo_contents.py

For every ``<prefix>*`` repository in a GitHub organization, fetch the full
**recursive** git-tree once
(``GET /repos/{org}/{repo}/git/trees/HEAD?recursive=1``) and derive a compact
per-repo metadata record into ``datasets/dataset_summaries/repo_contents.json``.

Output schema (one recursive call per repo):

{
  "nm000105": {
    "synced_at":  "2026-06-15T12:00:00Z",
    "updated_at": "2026-06-14T08:00:00Z",
    "truncated":  false,
    "top_level_files": [
      {"path": "dataset_description.json", "size": 512, "sha": "abc"},
      {"path": ".nemar/metadata.json",     "size": 200, "sha": "def"}
    ],
    "subjects":   ["sub-001", "sub-002"],
    "datatypes":  ["eeg", "emg"],
    "event_files": [
      {"path": "sub-001/eeg/sub-001_task-x_events.tsv", "size": 300, "sha": "e1"}
    ]
  },
  ...
}

``top_level_files`` are root-level (non-hidden) blobs plus blobs under any
``--include-subdir`` (e.g. ``.nemar``). ``subjects`` / ``datatypes`` /
``event_files`` are derived from the BIDS layout under each top-level ``sub-*``
directory (``derivatives/`` and other non-``sub-`` top-level dirs are ignored;
``phenotype`` is not treated as a datatype). See
``hed_metadata_toolkit.github.bids_tree`` for the exact rules.

``truncated`` is True when GitHub capped the recursive tree (>100k entries /
7 MB for the whole repo), in which case the derived lists may be incomplete; the
run prints how many repos truncated. Won't be hit for NEMAR-sized repos.

Incremental: a repo is re-fetched only when its ``updated_at`` (from
``datasets.tsv``) is newer than the stored ``synced_at`` (``--force`` re-fetches
all). Repos that error or are empty are recorded in a companion
``repo_contents_failures.json``; set ``"skip": true`` there to permanently
ignore a repo.

This replaces the earlier shallow GraphQL listing (which stored only top-level
``entries``). Consumers read either the new fields or the legacy ``entries`` for
backward compatibility, so a re-sync can be done incrementally.

Usage:
    hed-sync-repo-contents [--force] [--retry-failed] [--repo NAME]
        [--org ORG] [--prefix PFX] [--include-subdir SUBDIR] [--tsv PATH] [--out PATH]
    python -m hed_metadata_toolkit.github.sync_repo_contents ...
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone

import pandas as pd
import requests
from dotenv import load_dotenv

from hed_metadata_toolkit.github.bids_tree import derive_repo_metadata

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GIT_TREES_URL = "https://api.github.com/repos/{org}/{repo}/git/trees/{sha}"
RETRY_LIMIT = 4
RETRY_BASE_DELAY = 5  # seconds; doubled on each retry (exponential backoff)


# ---------------------------------------------------------------------------
# Windows-safe file replace
# ---------------------------------------------------------------------------


def _safe_replace(
    tmp_path: str, target_path: str, retries: int = 5, delay: float = 0.5
) -> None:
    """Replace target_path with tmp_path, retrying on Windows permission errors."""
    for attempt in range(1, retries + 1):
        try:
            os.replace(tmp_path, target_path)
            return
        except PermissionError:
            if attempt >= retries:
                raise
            print(
                f"    File locked, retrying in {delay}s... (attempt {attempt}/{retries})"
            )
            time.sleep(delay)
            delay *= 2


# ---------------------------------------------------------------------------
# Rate-limit awareness
# ---------------------------------------------------------------------------


def _is_rate_limited(response) -> bool:
    if response.status_code == 429:
        return True
    if response.status_code == 403:
        return response.headers.get("x-ratelimit-remaining") == "0"
    return False


def _wait_for_rate_limit(response) -> None:
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
        wait = 60
    print(f"\n  Rate limit hit — waiting {wait}s until reset...")
    time.sleep(wait)
    print("  Resuming after rate-limit wait.")


# ---------------------------------------------------------------------------
# Recursive git-tree fetch (one call per repo; no file contents downloaded)
# ---------------------------------------------------------------------------


def _fetch_recursive_tree(
    org: str, repo: str, headers: dict
) -> "tuple[list | None, bool, str | None]":
    """Return ``(tree_entries, truncated, error)`` for the whole repo.

    ``tree_entries`` is the raw ``tree`` array (both ``blob`` and ``tree``
    entries). ``truncated`` is True when GitHub capped the response.
    ``error`` is None on success, ``"not_found"`` for 404, or a message string.
    An empty repository (409, no commits) returns ``([], False, None)``.
    """
    url = GIT_TREES_URL.format(org=org, repo=repo, sha="HEAD") + "?recursive=1"
    delay = RETRY_BASE_DELAY
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=60)
        except requests.RequestException as exc:
            if attempt < RETRY_LIMIT:
                time.sleep(delay)
                delay *= 2
                continue
            return None, False, str(exc)

        if resp.status_code == 404:
            return None, False, "not_found"
        if resp.status_code == 409:
            return [], False, None  # empty repository (no commits)
        if _is_rate_limited(resp):
            _wait_for_rate_limit(resp)
            continue  # retry without consuming an attempt slot meaningfully

        try:
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            if attempt < RETRY_LIMIT:
                time.sleep(delay)
                delay *= 2
                continue
            return None, False, str(exc)

        truncated = bool(data.get("truncated"))
        if truncated:
            print(
                f"  WARNING: git-tree for {repo} was truncated (>100k entries / "
                "7MB across the whole repo) — derived lists may be incomplete."
            )
        return data.get("tree", []), truncated, None

    return None, False, "max retries exceeded"


# ---------------------------------------------------------------------------
# Failure tracking  (repo_contents_failures.json)
# ---------------------------------------------------------------------------


def _failures_path(out_path: str) -> str:
    base = os.path.splitext(out_path)[0]
    return base + "_failures.json"


def _load_failures(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:
            print(f"Warning: could not read {path}: {exc}")
    return {}


def _save_failures(failures: dict, path: str) -> None:
    if not failures:
        if os.path.exists(path):
            os.remove(path)
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(failures, fh, indent=2, ensure_ascii=False)
    _safe_replace(tmp, path)


def _save_json(data: dict, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    _safe_replace(tmp, path)


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


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

    headers: dict = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

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
    # Load existing repo_contents.json + failures dict
    # ------------------------------------------------------------------
    existing: dict = {}
    if os.path.exists(out_path):
        try:
            with open(out_path, "r", encoding="utf-8") as fh:
                existing = json.load(fh)
            print(f"Loaded existing data for {len(existing)} repos from {out_path}")
        except Exception as exc:
            print(f"Warning: could not read {out_path}: {exc}")

    fail_file = _failures_path(out_path)
    failures: dict = _load_failures(fail_file)
    print(
        f"Failures dict: {len(failures)} repos "
        f"({sum(1 for v in failures.values() if v.get('skip'))} skipped permanently)"
    )

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
                    continue  # permanently skipped
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

    # ------------------------------------------------------------------
    # Per-repo recursive fetch
    # ------------------------------------------------------------------
    n = len(to_fetch)
    fetched = errors = truncated_count = 0
    updated_lookup = {row["name"]: str(row.get("updated_at", "")) for _, row in df.iterrows()}

    for i, name in enumerate(to_fetch, 1):
        updated_at = updated_lookup.get(name, "")
        entries, truncated, err = _fetch_recursive_tree(organization, name, headers)

        if err is not None:
            errors += 1
            print(f"[{i}/{n}] {name}: ERROR ({err}) — recorded in failures dict")
            prev_skip = (failures.get(name) or {}).get("skip", False)
            failures[name] = {"reason": err, "failed_at": now_iso}
            if prev_skip:
                failures[name]["skip"] = True
            _save_failures(failures, fail_file)
            continue

        if not entries:
            errors += 1
            print(f"[{i}/{n}] {name}: empty repository — recorded in failures dict")
            prev_skip = (failures.get(name) or {}).get("skip", False)
            failures[name] = {"reason": "empty_repo", "failed_at": now_iso}
            if prev_skip:
                failures[name]["skip"] = True
            _save_failures(failures, fail_file)
            continue

        meta = derive_repo_metadata(entries, include_subdirs)
        existing[name] = {
            "synced_at": now_iso,
            "updated_at": updated_at,
            "truncated": truncated,
            **meta,
        }
        failures.pop(name, None)
        fetched += 1
        if truncated:
            truncated_count += 1
        print(
            f"[{i}/{n}] {name}: {len(meta['subjects'])} subjects, "
            f"{len(meta['datatypes'])} datatypes, {len(meta['event_files'])} event files, "
            f"{len(meta['top_level_files'])} top-level files"
            + ("  [TRUNCATED]" if truncated else "")
        )

        # Save incrementally so a crash mid-run keeps progress.
        _save_json(existing, out_path)
        _save_failures(failures, fail_file)

    print(
        f"\nDone.  Fetched: {fetched}  |  Errors: {errors}  |  "
        f"Skipped: {skipped}  |  Truncated: {truncated_count}"
    )
    if truncated_count:
        print(
            f"  {truncated_count} repo(s) had truncated git-trees — their derived "
            'lists may be incomplete (see "truncated": true in the manifest).'
        )
    if errors:
        print(f"Failures saved to: {fail_file}")
        print('  Set "skip": true on any permanently inaccessible repos in that file.')


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: "list[str] | None" = None) -> int:
    """Argparse wrapper around :func:`sync_repo_contents`."""
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Sync per-repo BIDS metadata from a GitHub organization to "
        "repo_contents.json (one recursive git-tree call per repo).",
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
        help="Treat blobs under this repo subdirectory as top-level files, "
        "recorded as '<subdir>/<file>' (repeatable; e.g. '.nemar').",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="GitHub PAT (defaults to $GITHUB_TOKEN).",
    )
    args = parser.parse_args(argv)

    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Warning: GITHUB_TOKEN not set; requests will be rate-limited.")

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
