"""
sync_repo_file_contents.py

For each ds* repository listed in datasets/dataset_summaries/repo_contents.json:
  1. Checks for a local participants.tsv in datasets/dataset_repos/<repo>/.
     If absent, logs a message and skips the repository.
  2. Reads the participant_id column and finds the first ID that matches a
     top-level tree entry (directory) recorded in repo_contents.json.
  3. Fetches a full recursive file listing for that participant directory via
     two GitHub git-trees API calls:
       a. GET /repos/{org}/{repo}/git/trees/HEAD   → root tree (find participant SHA)
       b. GET /repos/{org}/{repo}/git/trees/{sha}?recursive=1  → all files
  4. Stores every path + SHA + size in datasets/dataset_summaries/repo_file_contents.json.
  5. Downloads only *_events.tsv and *_events.json files (with SHA-based
     incremental skip) into datasets/dataset_repos/<repo>/<participant_dir>/...

Usage:
    python sync_repo_file_contents.py [--repo NAME] [--workers N] [--force]
                                      [--retry-failed]
                                      [--contents PATH] [--datasets PATH]
                                      [--out PATH]

Options:
    --repo NAME        Only sync this single repository
    --workers N        Parallel download threads (default: 10)
    --force            Re-download even if SHA matches
    --retry-failed     Re-attempt files in the failures dict (skip=true excluded)
    --contents PATH    Path to repo_contents.json
                       (default: ../datasets/dataset_summaries/repo_contents.json)
    --datasets PATH    Root directory for local dataset folders
                       (default: ../datasets/dataset_repos)
    --out PATH         Path to repo_file_contents.json
                       (default: ../datasets/dataset_summaries/repo_file_contents.json)

Failure tracking (datasets/dataset_summaries/repo_file_contents_failures.json):
    Files that fail to download are recorded keyed by
    "<repo>/<participant_dir>/<rel_path>" (e.g.
    "ds007640/sub-01/func/sub-01_task-foo_events.tsv").
    On a subsequent successful download the entry is removed.  To permanently
    skip a file, set its "skip" field to true — it will then be ignored even
    with --retry-failed.
"""

import argparse
import base64
from datetime import datetime, timezone
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REST_URL = "https://api.github.com/repos/{org}/{repo}/contents/{path}"
GIT_TREES_URL = "https://api.github.com/repos/{org}/{repo}/git/trees/{sha}"

DEFAULT_WORKERS = 10
RETRY_LIMIT = 3
RETRY_DELAY = 5

SHA_CACHE_FILENAME = ".sha_cache.json"
FAILURES_FILENAME = "repo_file_contents_failures.json"

# Only files whose names end with these suffixes are downloaded.
EVENTS_SUFFIXES = ("_events.tsv", "_events.json")


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
# Rate-limit helpers  (shared pattern from sync_local_files.py)
# ---------------------------------------------------------------------------


def _is_rate_limited(response) -> bool:
    """Return True if the response indicates a GitHub rate-limit hit."""
    if response.status_code == 429:
        return True
    if response.status_code == 403:
        return response.headers.get("x-ratelimit-remaining") == "0"
    return False


def _wait_for_rate_limit(response) -> None:
    """Block until the GitHub rate-limit window resets."""
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
# SHA cache  (one JSON file per dataset directory)
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()


def _load_sha_cache(repo_dir: str) -> dict:
    path = os.path.join(repo_dir, SHA_CACHE_FILENAME)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return {}


def _save_sha_cache(repo_dir: str, cache: dict) -> None:
    path = os.path.join(repo_dir, SHA_CACHE_FILENAME)
    with _cache_lock:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=2)
        _safe_replace(tmp, path)


# ---------------------------------------------------------------------------
# Failure tracking  (datasets/repo_file_contents_failures.json)
# ---------------------------------------------------------------------------


def _failures_path(out_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(out_path)), FAILURES_FILENAME)


def _load_failures(out_path: str) -> dict:
    path = _failures_path(out_path)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:
            print(f"Warning: could not read {path}: {exc}")
    return {}


def _save_failures(failures: dict, out_path: str) -> None:
    path = _failures_path(out_path)
    if not failures:
        if os.path.exists(path):
            os.remove(path)
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(failures, fh, indent=2, ensure_ascii=False)
    _safe_replace(tmp, path)


# ---------------------------------------------------------------------------
# repo_file_contents.json  (main output artifact)
# ---------------------------------------------------------------------------


def _load_file_contents(out_path: str) -> dict:
    if os.path.exists(out_path):
        try:
            with open(out_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:
            print(f"Warning: could not read {out_path}: {exc}")
    return {}


def _save_file_contents(file_contents: dict, out_path: str) -> None:
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(file_contents, fh, indent=2, ensure_ascii=False)
    _safe_replace(tmp, out_path)


# ---------------------------------------------------------------------------
# Participants TSV helpers
# ---------------------------------------------------------------------------


def _read_participant_ids(tsv_path: str) -> list[str]:
    """
    Read participant_id values from a BIDS participants.tsv file.
    Returns an empty list on any error.
    """
    try:
        with open(tsv_path, "r", encoding="utf-8") as fh:
            header = fh.readline().rstrip("\n").split("\t")
            if "participant_id" not in header:
                return []
            col_idx = header.index("participant_id")
            ids = []
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if col_idx < len(parts):
                    val = parts[col_idx].strip()
                    if val:
                        ids.append(val)
            return ids
    except Exception as exc:
        print(f"  Warning: could not read {tsv_path}: {exc}")
        return []


def _find_participant_dir(
    participant_ids: list[str], repo_entries: list[dict]
) -> str | None:
    """
    Return the first participant_id that has a matching top-level tree entry.
    """
    tree_names = {e["name"] for e in repo_entries if e.get("type") == "tree"}
    for pid in participant_ids:
        if pid in tree_names:
            return pid
    return None


def _repo_tree_entries(meta) -> list[dict]:
    """Return top-level directory entries as ``{name, type: "tree"}`` dicts.

    Supports both repo_contents.json schemas:
      - new: ``meta["subjects"]`` = list of ``sub-*`` directory names.
      - legacy: ``meta["entries"]`` = mixed blob/tree entries (trees kept).
    """
    if not isinstance(meta, dict):
        return []
    if "subjects" in meta:
        return [{"name": s, "type": "tree"} for s in meta.get("subjects", [])]
    return meta.get("entries", [])


# ---------------------------------------------------------------------------
# GitHub git-trees API helpers
# ---------------------------------------------------------------------------


def _fetch_root_tree(
    org: str, repo: str, headers: dict
) -> tuple[list[dict] | None, str | None]:
    """
    Fetch the top-level tree entries for a repository via the git-trees API.
    Returns (entries, error_message).  Entries include trees and blobs with SHAs.
    """
    url = GIT_TREES_URL.format(org=org, repo=repo, sha="HEAD")
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=30)
        except requests.RequestException as exc:
            if attempt < RETRY_LIMIT:
                time.sleep(RETRY_DELAY)
                continue
            return None, str(exc)

        if _is_rate_limited(resp):
            _wait_for_rate_limit(resp)
            continue  # retry immediately

        if resp.status_code == 404:
            return None, "not_found"

        try:
            resp.raise_for_status()
            data = resp.json()
            return data.get("tree", []), None
        except Exception as exc:
            if attempt < RETRY_LIMIT:
                time.sleep(RETRY_DELAY)
                continue
            return None, str(exc)

    return None, "max retries exceeded"


def _fetch_recursive_tree(
    org: str, repo: str, tree_sha: str, headers: dict
) -> tuple[list[dict] | None, str | None]:
    """
    Fetch a fully recursive listing for a directory tree by its SHA.
    Returns (entries, error_message).
    Each blob entry: {"path": "...", "type": "blob", "sha": "...", "size": N}
    Paths are relative to the tree root (i.e., the participant directory).
    Warns if the API response was truncated due to size limits.
    """
    url = GIT_TREES_URL.format(org=org, repo=repo, sha=tree_sha) + "?recursive=1"
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=60)
        except requests.RequestException as exc:
            if attempt < RETRY_LIMIT:
                time.sleep(RETRY_DELAY)
                continue
            return None, str(exc)

        if _is_rate_limited(resp):
            _wait_for_rate_limit(resp)
            continue

        if resp.status_code == 404:
            return None, "not_found"

        try:
            resp.raise_for_status()
            data = resp.json()
            if data.get("truncated"):
                print(
                    "  Warning: recursive tree response was truncated "
                    "(repository too large). File list may be incomplete."
                )
            return data.get("tree", []), None
        except Exception as exc:
            if attempt < RETRY_LIMIT:
                time.sleep(RETRY_DELAY)
                continue
            return None, str(exc)

    return None, "max retries exceeded"


# ---------------------------------------------------------------------------
# Single-file download  (GitHub contents API)
# ---------------------------------------------------------------------------


def _download_file(
    org: str,
    repo: str,
    repo_path: str,
    local_path: str,
    headers: dict,
) -> tuple[bool, str | None, str | None]:
    """
    Download one file from the GitHub REST contents API.
    repo_path is the path relative to the repository root.
    Returns (success, git_sha, error_message).
    """
    url = REST_URL.format(org=org, repo=repo, path=repo_path)
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=30)
        except requests.RequestException as exc:
            if attempt < RETRY_LIMIT:
                time.sleep(RETRY_DELAY)
                continue
            return False, None, str(exc)

        if resp.status_code == 404:
            return False, None, "not_found"

        if _is_rate_limited(resp):
            _wait_for_rate_limit(resp)
            continue

        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            if attempt < RETRY_LIMIT:
                time.sleep(RETRY_DELAY)
                continue
            return False, None, str(exc)

        data = resp.json()
        encoding = data.get("encoding", "")
        raw_content = data.get("content", "")
        git_sha = data.get("sha")

        try:
            if encoding == "base64":
                content_bytes = base64.b64decode(raw_content)
            else:
                content_bytes = raw_content.encode("utf-8")
        except Exception as exc:
            return False, None, f"decode error: {exc}"

        os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
        tmp = local_path + ".tmp"
        try:
            with open(tmp, "wb") as fh:
                fh.write(content_bytes)
            _safe_replace(tmp, local_path)
        except Exception as exc:
            return False, None, f"write error: {exc}"

        return True, git_sha, None

    return False, None, "max retries exceeded"


# ---------------------------------------------------------------------------
# Per-repo sync
# ---------------------------------------------------------------------------


def sync_repo(
    repo_name: str,
    repo_entries: list[dict],
    datasets_dir: str,
    headers: dict,
    force: bool,
    failures: dict,
    failures_lock: threading.Lock,
    retry_failed: bool,
    workers: int,
    organization: str = "OpenNeuroDatasets",
) -> tuple[dict, dict | None]:
    """
    Sync one repository.

    Returns (stats, file_contents_entry).
    file_contents_entry is None if the repo was skipped entirely; otherwise it
    is the dict to store under repo_name in repo_file_contents.json.
    """
    repo_dir = os.path.join(datasets_dir, repo_name)
    stats = {
        "downloaded": 0,
        "skipped_sha": 0,
        "skipped_failed": 0,
        "not_found": 0,
        "errors": 0,
        "skipped_no_participants": False,
        "events_files": 0,
    }

    # ------------------------------------------------------------------
    # 1. Check for local participants.tsv
    # ------------------------------------------------------------------
    participants_path = os.path.join(repo_dir, "participants.tsv")
    if not os.path.exists(participants_path):
        print("  No participants.tsv found — skipping")
        stats["skipped_no_participants"] = True
        return stats, None

    # ------------------------------------------------------------------
    # 2. Read participant IDs
    # ------------------------------------------------------------------
    participant_ids = _read_participant_ids(participants_path)
    if not participant_ids:
        print("  participants.tsv has no participant_id column or is empty — skipping")
        return stats, None

    # ------------------------------------------------------------------
    # 3. Find first participant directory in top-level repo entries
    # ------------------------------------------------------------------
    participant_dir = _find_participant_dir(participant_ids, repo_entries)
    if not participant_dir:
        print(
            f"  No participant_id matches a top-level directory "
            f"(checked {len(participant_ids)} IDs) — skipping"
        )
        return stats, None

    print(f"  Using participant directory: {participant_dir}", end=" ... ", flush=True)

    # ------------------------------------------------------------------
    # 4. Fetch root tree to resolve the participant directory's tree SHA
    # ------------------------------------------------------------------
    root_tree, err = _fetch_root_tree(organization, repo_name, headers)
    if err:
        print(f"\n  Error fetching root tree: {err}")
        return stats, None

    participant_sha = None
    for entry in root_tree:
        if entry.get("path") == participant_dir and entry.get("type") == "tree":
            participant_sha = entry.get("sha")
            break

    if not participant_sha:
        print(
            f"\n  Could not find tree SHA for '{participant_dir}' in root tree — skipping"
        )
        return stats, None

    # ------------------------------------------------------------------
    # 5. Fetch recursive file listing for the participant directory
    # ------------------------------------------------------------------
    tree_entries, err = _fetch_recursive_tree(
        organization, repo_name, participant_sha, headers
    )
    if err:
        print(f"\n  Error fetching recursive tree: {err}")
        return stats, None

    blob_entries = [e for e in tree_entries if e.get("type") == "blob"]

    # ------------------------------------------------------------------
    # 6. Build the full file listing (all blobs, paths prefixed with participant_dir)
    # ------------------------------------------------------------------
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    all_files = [
        {
            "path": f"{participant_dir}/{e['path']}",
            "sha": e.get("sha"),
            "size": e.get("size"),
        }
        for e in blob_entries
    ]
    file_contents_entry = {
        "participant_dir": participant_dir,
        "synced_at": now_iso,
        "files": all_files,
    }

    # ------------------------------------------------------------------
    # 7. Filter to events files and download
    # ------------------------------------------------------------------
    events_entries = [e for e in blob_entries if e["path"].endswith(EVENTS_SUFFIXES)]
    stats["events_files"] = len(events_entries)
    print(f"{len(blob_entries)} files total, {len(events_entries)} events files")

    sha_cache = _load_sha_cache(repo_dir)
    cache_dirty = False

    def handle_blob(entry: dict) -> None:
        nonlocal cache_dirty

        rel_path = entry["path"]  # relative to participant dir
        full_rel = f"{participant_dir}/{rel_path}"  # relative to repo dir
        key = f"{repo_name}/{full_rel}"  # failure-dict key
        remote_sha = entry.get("sha")
        local_path = os.path.join(repo_dir, participant_dir, *rel_path.split("/"))
        cache_key = full_rel

        # Permanent-skip or failure-dict skip
        with failures_lock:
            fail_entry = failures.get(key)
        if fail_entry:
            if fail_entry.get("skip") or (not force and not retry_failed):
                stats["skipped_failed"] += 1
                return

        # SHA-based incremental skip
        if (
            not force
            and remote_sha
            and sha_cache.get(cache_key) == remote_sha
            and os.path.exists(local_path)
        ):
            stats["skipped_sha"] += 1
            return

        success, returned_sha, dl_err = _download_file(
            organization, repo_name, full_rel, local_path, headers
        )

        if success:
            stats["downloaded"] += 1
            store_sha = returned_sha or remote_sha
            if store_sha:
                sha_cache[cache_key] = store_sha
                cache_dirty = True
            with failures_lock:
                failures.pop(key, None)
        else:
            if dl_err == "not_found":
                stats["not_found"] += 1
            else:
                stats["errors"] += 1
            now_err = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            with failures_lock:
                existing_skip = (failures.get(key) or {}).get("skip", False)
                failures[key] = {"reason": dl_err or "unknown", "failed_at": now_err}
                if existing_skip:
                    failures[key]["skip"] = True

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(handle_blob, e) for e in events_entries]
            for f in as_completed(futures):
                f.result()
    else:
        for e in events_entries:
            handle_blob(e)

    if cache_dirty:
        _save_sha_cache(repo_dir, sha_cache)

    return stats, file_contents_entry


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def sync_all(
    contents_path: str,
    datasets_dir: str,
    out_path: str,
    token: str | None,
    organization: str = "OpenNeuroDatasets",
    test_repo: str | None = None,
    workers: int = DEFAULT_WORKERS,
    force: bool = False,
    retry_failed: bool = False,
) -> None:
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    # ------------------------------------------------------------------
    # Load repo_contents.json  (source of repo list and top-level entries)
    # ------------------------------------------------------------------
    try:
        with open(contents_path, "r", encoding="utf-8") as fh:
            repo_contents: dict = json.load(fh)
        print(f"Loaded {len(repo_contents)} repos from {contents_path}")
    except Exception as exc:
        print(f"Error reading {contents_path}: {exc}")
        return

    if test_repo:
        if test_repo not in repo_contents:
            print(f"Repo '{test_repo}' not found in {contents_path}")
            return
        repo_contents = {test_repo: repo_contents[test_repo]}
        print(f"Single-repo mode: {test_repo}")

    # ------------------------------------------------------------------
    # Load existing repo_file_contents.json  (incremental output)
    # ------------------------------------------------------------------
    file_contents = _load_file_contents(out_path)
    print(f"Existing file-contents entries: {len(file_contents)}")

    # ------------------------------------------------------------------
    # Load failures dict
    # ------------------------------------------------------------------
    failures = _load_failures(out_path)
    perm_skipped = sum(1 for v in failures.values() if v.get("skip"))
    print(
        f"Failures dict: {len(failures)} entries ({perm_skipped} permanently skipped)"
    )

    failures_lock = threading.Lock()
    totals = {
        "downloaded": 0,
        "skipped_sha": 0,
        "skipped_failed": 0,
        "not_found": 0,
        "errors": 0,
        "skipped_no_participants": 0,
    }

    n = len(repo_contents)
    for idx, (repo_name, meta) in enumerate(repo_contents.items(), 1):
        entries = _repo_tree_entries(meta)
        print(f"[{idx}/{n}] {repo_name}:", end=" ", flush=True)

        stats, fc_entry = sync_repo(
            repo_name=repo_name,
            repo_entries=entries,
            datasets_dir=datasets_dir,
            headers=headers,
            force=force,
            failures=failures,
            failures_lock=failures_lock,
            retry_failed=retry_failed,
            workers=workers,
            organization=organization,
        )

        if fc_entry is not None:
            file_contents[repo_name] = fc_entry
            _save_file_contents(file_contents, out_path)

        if stats["skipped_no_participants"]:
            totals["skipped_no_participants"] += 1
        else:
            print(
                f"    downloaded={stats['downloaded']}  "
                f"skipped(sha)={stats['skipped_sha']}  "
                f"skipped(failed)={stats['skipped_failed']}  "
                f"not_found={stats['not_found']}  "
                f"errors={stats['errors']}"
            )
            for k in (
                "downloaded",
                "skipped_sha",
                "skipped_failed",
                "not_found",
                "errors",
            ):
                totals[k] += stats[k]

        # Persist failures after each repo
        _save_failures(failures, out_path)

    print("\n" + "=" * 60)
    print("SYNC COMPLETE")
    print(f"  Downloaded      : {totals['downloaded']}")
    print(f"  Skipped SHA     : {totals['skipped_sha']}")
    print(f"  Skipped failed  : {totals['skipped_failed']}")
    print(f"  Not found       : {totals['not_found']}")
    print(f"  Errors          : {totals['errors']}")
    print(f"  No participants : {totals['skipped_no_participants']}")
    print(f"  Output file     : {out_path}")
    if totals["errors"] or totals["not_found"]:
        print(f"  Failures file   : {_failures_path(out_path)}")
        print('  Set "skip": true on any permanently inaccessible files.')


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: "list[str] | None" = None) -> int:
    """Argparse wrapper around :func:`sync_all`.

    ``sync_all`` is the library entry point; consumers can call it
    directly with their own paths and config.
    """
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Download per-participant event files for every dataset repo.",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Only sync this single repo",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="Parallel download threads (default: 10)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if SHA matches",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Re-attempt files in the failures dict (skip=true excluded)",
    )
    parser.add_argument(
        "--contents",
        default="datasets/dataset_summaries/repo_contents.json",
        help="Path to repo_contents.json",
    )
    parser.add_argument(
        "--datasets",
        default="datasets/dataset_repos",
        help="Root directory for local dataset folders",
    )
    parser.add_argument(
        "--out",
        default="datasets/dataset_summaries/repo_file_contents.json",
        help="Path to repo_file_contents.json output file",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="GitHub PAT (defaults to $GITHUB_TOKEN).",
    )
    parser.add_argument(
        "--org",
        default="OpenNeuroDatasets",
        help="GitHub organization name (default: OpenNeuroDatasets).",
    )
    args = parser.parse_args(argv)

    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Warning: GITHUB_TOKEN not set. Downloads will be rate-limited.")

    sync_all(
        contents_path=args.contents,
        datasets_dir=args.datasets,
        out_path=args.out,
        token=token,
        organization=args.org,
        test_repo=args.repo,
        workers=args.workers,
        force=args.force,
        retry_failed=args.retry_failed,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
