"""list_event_files.py — list (do NOT download) BIDS event files per repo.

For every repo in a GitHub organization, fetch its full recursive git-tree
(one ``git/trees/HEAD?recursive=1`` request per repo — no file contents are
downloaded) and record the ``*_events.tsv`` / ``*_events.json`` files that are
either:

  - at the repository root (e.g. ``task-foo_events.json``), or
  - anywhere beneath a top-level ``sub-XXX/`` directory
    (e.g. ``sub-001/eeg/sub-001_task-foo_events.tsv``,
    ``sub-001/ses-01/eeg/..._events.tsv``).

Event files under any other top-level directory (``derivatives/``,
``sourcedata/``, ``code/``, ``stimuli/`` …) are ignored.

Incremental: a per-repo ``synced_at`` is stored in the manifest. On a re-run a
repo is re-listed only when its ``updated_at`` (from ``datasets.tsv``) is newer
than the stored ``synced_at`` (use ``--force`` to re-list everything). This
mirrors ``sync_repo_contents``.

Outputs (both written):
  - JSON manifest, keyed by repo::

        { "<repo>": {
            "synced_at": "...", "updated_at": "...",
            "event_files": [ {"path": ..., "size": ..., "sha": ...}, ... ]
        }, ... }

    ``size`` is the blob byte size and ``sha`` is the git blob SHA, both from the
    git-trees response.
  - flat TSV: columns ``repo``, ``path``, ``size``, ``sha`` (one row per file)

Usage:
    hed-list-event-files --org nemarDatasets --prefix nm
    python -m hed_metadata_toolkit.github.list_event_files ...
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from datetime import datetime, timezone

import pandas as pd
import requests
from dotenv import load_dotenv

GIT_TREES_URL = "https://api.github.com/repos/{org}/{repo}/git/trees/{sha}"
EVENTS_SUFFIXES = ("_events.tsv", "_events.json")
RETRY_LIMIT = 4
RETRY_DELAY = 5  # seconds, doubled on each retry


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def is_event_file(path: str) -> bool:
    """Keep BIDS event files at the repo root or under a top-level ``sub-*`` dir.

    ``path`` is repo-root-relative (POSIX separators). A path qualifies when it
    ends in ``_events.tsv`` / ``_events.json`` AND it is either at the root (no
    ``/``) or its first segment is a ``sub-`` directory (any depth below it).
    Event files under other top-level directories (``derivatives/`` etc.) are
    rejected.
    """
    if not path.endswith(EVENTS_SUFFIXES):
        return False
    if "/" not in path:
        return True  # repo root
    return path.split("/", 1)[0].startswith("sub-")


# ---------------------------------------------------------------------------
# GitHub git-trees fetch (recursive; no file contents)
# ---------------------------------------------------------------------------


def _fetch_recursive_tree(org: str, repo: str, headers: dict) -> tuple[list | None, str | None]:
    """Return (blob_entries, error) for the whole repo via one recursive call.

    Each entry is the raw git-trees blob dict (has ``path``, ``sha``, ``size``).
    ``error`` is None on success; "not_found" for 404; an empty list for an
    empty repo (409). Warns (does not fail) if GitHub truncated the response.
    """
    url = GIT_TREES_URL.format(org=org, repo=repo, sha="HEAD") + "?recursive=1"
    delay = RETRY_DELAY
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=60)
        except requests.RequestException as exc:
            if attempt < RETRY_LIMIT:
                time.sleep(delay)
                delay *= 2
                continue
            return None, str(exc)

        if resp.status_code == 404:
            return None, "not_found"
        if resp.status_code == 409:
            return [], None  # empty repository (no commits)
        if resp.status_code in (403, 429) and resp.headers.get("x-ratelimit-remaining") == "0":
            reset = int(resp.headers.get("x-ratelimit-reset", "0") or 0)
            wait = max(0, reset - int(time.time())) + 1
            print(f"  Rate limited; waiting {min(wait, 300)}s...")
            time.sleep(min(wait, 300))
            continue

        try:
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            if attempt < RETRY_LIMIT:
                time.sleep(delay)
                delay *= 2
                continue
            return None, str(exc)

        if data.get("truncated"):
            print(
                f"  WARNING: git-tree for {repo} was truncated (>100k entries / "
                "7MB) — event-file list may be incomplete."
            )
        blobs = [e for e in data.get("tree", []) if e.get("type") == "blob"]
        return blobs, None

    return None, "max retries exceeded"


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------


def _save_json(manifest: dict, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _write_tsv(manifest: dict, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["repo", "path", "size", "sha"])
        for repo in sorted(manifest):
            for ef in manifest[repo].get("event_files", []):
                writer.writerow([repo, ef.get("path"), ef.get("size"), ef.get("sha")])


# ---------------------------------------------------------------------------
# Library API
# ---------------------------------------------------------------------------


def list_event_files(
    *,
    tsv_path: str,
    out_path: str,
    token: str | None,
    organization: str = "OpenNeuroDatasets",
    prefix: str = "ds",
    force: bool = False,
    tsv_out_path: "str | None" = None,
) -> dict:
    """Build/update the event-file manifest. Returns the manifest dict."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    try:
        df = pd.read_csv(tsv_path, sep="\t")
    except Exception as exc:
        print(f"Error reading {tsv_path}: {exc}")
        return {}
    if prefix:
        df = df[df["name"].str.startswith(prefix)].reset_index(drop=True)
    print(f"{len(df)} {prefix or '(all)'}* repos to consider")

    manifest: dict = {}
    if os.path.exists(out_path):
        try:
            with open(out_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
        except Exception as exc:
            print(f"Warning: could not read existing {out_path}: {exc}")

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    listed = skipped = errors = 0
    n = len(df)

    for i, row in enumerate(df.itertuples(index=False), 1):
        name = getattr(row, "name")
        updated_at = str(getattr(row, "updated_at", "") or "")

        prev = manifest.get(name)
        if (
            not force
            and prev
            and prev.get("synced_at")
            and updated_at
            and prev["synced_at"] >= updated_at
        ):
            skipped += 1
            continue

        blobs, err = _fetch_recursive_tree(organization, name, headers)
        if err:
            errors += 1
            print(f"[{i}/{n}] {name}: ERROR ({err})")
            continue

        events = sorted(
            (
                {"path": b.get("path"), "size": b.get("size"), "sha": b.get("sha")}
                for b in blobs
                if is_event_file(b.get("path", ""))
            ),
            key=lambda e: e["path"],
        )
        manifest[name] = {
            "synced_at": now_iso,
            "updated_at": updated_at,
            "event_files": events,
        }
        listed += 1
        print(f"[{i}/{n}] {name}: {len(events)} event files")
        _save_json(manifest, out_path)

    if tsv_out_path:
        _write_tsv(manifest, tsv_out_path)

    print(
        f"\nDone. Listed: {listed}  |  Skipped (unchanged): {skipped}  |  "
        f"Errors: {errors}"
    )
    print(f"Manifest: {out_path}")
    if tsv_out_path:
        print(f"TSV:      {tsv_out_path}")
    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: "list[str] | None" = None) -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="List (without downloading) BIDS event files per repo: "
        "root-level and under top-level sub-*/ directories.",
    )
    parser.add_argument(
        "--tsv",
        default="datasets/dataset_summaries/datasets.tsv",
        help="Path to datasets.tsv (output of hed-fetch-repo-list).",
    )
    parser.add_argument(
        "--out",
        default="datasets/dataset_summaries/event_files.json",
        help="Output JSON manifest.",
    )
    parser.add_argument(
        "--tsv-out",
        default="datasets/dataset_summaries/event_files.tsv",
        help="Output flat TSV (repo, path). Pass '' to skip.",
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
        "(default: 'ds'; use 'nm' for NEMAR, '' for all).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-list every repo, ignoring stored synced_at.",
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

    list_event_files(
        tsv_path=args.tsv,
        out_path=args.out,
        token=token,
        organization=args.org,
        prefix=args.prefix,
        force=args.force,
        tsv_out_path=(args.tsv_out or None),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
