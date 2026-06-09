"""
sync_local_files.py

Downloads every top-level *file* (blob) for each ds* repository listed in
datasets/dataset_summaries/repo_contents.json into a matching datasets/dataset_repos/<repo>/ directory.

Improvements over download_repo_files.py:
  - Downloads ALL top-level blobs, not just four specific patterns.
  - SHA-based incremental skip: if the local file's stored SHA matches the
    entry in repo_contents.json the file is not re-downloaded.
  - Parallel downloads via ThreadPoolExecutor (default 10 workers).
  - Configurable maximum file size (default 512 KB); larger blobs are logged
    and skipped to avoid pulling in large binary files.
  - Writes a sha_cache.json next to each dataset directory so SHA comparisons
    survive between runs without re-hashing local files.
  - Appends failures to datasets/download_failed.log.tsv.

Usage:
    python sync_local_files.py [--repo ds000001] [--workers N]
                               [--max-size BYTES] [--force]
                               [--contents PATH] [--datasets PATH]

Options:
    --repo NAME        Only sync this single repository
    --workers N        Parallel download threads (default: 10)
    --max-size BYTES   Skip blobs larger than this (default: 524288 = 512 KB)
    --force            Re-download even if SHA matches
    --retry-failed     Re-attempt files recorded in the failures dict (skip=true entries excluded)
    --contents PATH    Path to repo_contents.json (default: ../datasets/dataset_summaries/repo_contents.json)
    --datasets PATH    Root directory for local dataset folders (default: ../datasets/dataset_repos)

Failure tracking (datasets/dataset_summaries/download_failures.json):
    Files that fail to download are recorded in a companion JSON dict keyed by
    "<repoName>_<filename>" (e.g. "ds000001_participants.tsv").  On a subsequent
    successful download the entry is removed.  To permanently skip a file, set
    its "skip" field to true — it will then be ignored even with --retry-failed.
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
ORGANIZATION = "OpenNeuroDatasets"
REST_URL = "https://api.github.com/repos/{org}/{repo}/contents/{path}"
DEFAULT_WORKERS = 10
DEFAULT_MAX_SIZE = 512 * 1024  # 512 KB
RETRY_LIMIT = 3
RETRY_DELAY = 5
SHA_CACHE_FILENAME = ".sha_cache.json"
FAILURES_FILENAME = "download_failures.json"


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
# Rate-limit helpers
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
# Single-file download
# ---------------------------------------------------------------------------


def _download_file(
    org: str,
    repo: str,
    filename: str,
    local_path: str,
    expected_sha: str | None,
    headers: dict,
) -> tuple[bool, str | None, str | None]:
    """
    Download one file from the GitHub REST API.

    Returns (success, git_sha, error_message).
    git_sha is the blob sha from the API response (for cache storage).
    """
    url = REST_URL.format(org=org, repo=repo, path=filename)

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
            continue  # retry without counting this as a regular failure
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
# Failure tracking  (datasets/download_failures.json)
# ---------------------------------------------------------------------------


def _failures_path(contents_path: str) -> str:
    return os.path.join(
        os.path.dirname(os.path.abspath(contents_path)), FAILURES_FILENAME
    )


def _load_failures(contents_path: str) -> dict:
    """Load the failures dict, returning an empty dict if absent or corrupt."""
    path = _failures_path(contents_path)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:
            print(f"Warning: could not read {path}: {exc}")
    return {}


def _save_failures(failures: dict, contents_path: str) -> None:
    """Persist the failures dict; removes the file entirely when empty."""
    path = _failures_path(contents_path)
    if not failures:
        if os.path.exists(path):
            os.remove(path)
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(failures, fh, indent=2, ensure_ascii=False)
    _safe_replace(tmp, path)


# ---------------------------------------------------------------------------
# Per-repo sync
# ---------------------------------------------------------------------------


def sync_repo(
    repo_name: str,
    entries: list[dict],
    datasets_dir: str,
    headers: dict,
    max_size: int,
    force: bool,
    failures: dict,
    failures_lock: threading.Lock,
    retry_failed: bool,
    workers: int,
) -> dict:
    """Sync all blob entries for one repository. Returns stats dict."""
    repo_dir = os.path.join(datasets_dir, repo_name)
    os.makedirs(repo_dir, exist_ok=True)

    sha_cache = _load_sha_cache(repo_dir)
    cache_dirty = False

    blobs = [e for e in entries if e.get("type") == "blob"]
    stats = {
        "downloaded": 0,
        "skipped_sha": 0,
        "skipped_size": 0,
        "skipped_failed": 0,
        "not_found": 0,
        "errors": 0,
    }

    def handle_blob(entry: dict):
        nonlocal cache_dirty
        name = entry["name"]
        key = f"{repo_name}_{name}"
        remote_sha = entry.get("sha")
        size = entry.get("size") or 0
        local_path = os.path.join(repo_dir, name)

        # Size gate
        if size and size > max_size:
            stats["skipped_size"] += 1
            return

        # Failure-dict skip: permanent always applies; others skip unless retrying
        with failures_lock:
            fail_entry = failures.get(key)
        if fail_entry:
            if fail_entry.get("skip") or (not force and not retry_failed):
                stats["skipped_failed"] += 1
                return

        # SHA-based skip
        if (
            not force
            and remote_sha
            and sha_cache.get(name) == remote_sha
            and os.path.exists(local_path)
        ):
            stats["skipped_sha"] += 1
            return

        success, returned_sha, err = _download_file(
            ORGANIZATION, repo_name, name, local_path, remote_sha, headers
        )

        if success:
            stats["downloaded"] += 1
            store_sha = returned_sha or remote_sha
            if store_sha:
                sha_cache[name] = store_sha
                cache_dirty = True
            with failures_lock:
                failures.pop(key, None)
        else:
            if err == "not_found":
                stats["not_found"] += 1
            else:
                stats["errors"] += 1
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            with failures_lock:
                # Preserve skip=true if already set on this entry
                existing_skip = (failures.get(key) or {}).get("skip", False)
                failures[key] = {"reason": err or "unknown", "failed_at": now_iso}
                if existing_skip:
                    failures[key]["skip"] = True

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(handle_blob, e) for e in blobs]
            for f in as_completed(futures):
                f.result()  # re-raise any unexpected exceptions
    else:
        for e in blobs:
            handle_blob(e)

    if cache_dirty:
        _save_sha_cache(repo_dir, sha_cache)

    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def sync_all(
    contents_path: str,
    datasets_dir: str,
    token: str | None,
    test_repo: str | None = None,
    workers: int = DEFAULT_WORKERS,
    max_size: int = DEFAULT_MAX_SIZE,
    force: bool = False,
    retry_failed: bool = False,
) -> None:
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    # Load repo_contents.json
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

    # Load failures dict
    failures = _load_failures(contents_path)
    perm_skipped = sum(1 for v in failures.values() if v.get("skip"))
    print(
        f"Failures dict: {len(failures)} entries ({perm_skipped} permanently skipped)"
    )

    failures_lock = threading.Lock()
    totals = {
        "downloaded": 0,
        "skipped_sha": 0,
        "skipped_size": 0,
        "skipped_failed": 0,
        "not_found": 0,
        "errors": 0,
    }

    n = len(repo_contents)
    for idx, (repo_name, meta) in enumerate(repo_contents.items(), 1):
        entries = meta.get("entries", []) if isinstance(meta, dict) else []
        blob_count = sum(1 for e in entries if e.get("type") == "blob")
        print(f"[{idx}/{n}] {repo_name}: {blob_count} blobs", end=" ... ", flush=True)

        stats = sync_repo(
            repo_name=repo_name,
            entries=entries,
            datasets_dir=datasets_dir,
            headers=headers,
            max_size=max_size,
            force=force,
            failures=failures,
            failures_lock=failures_lock,
            retry_failed=retry_failed,
            workers=workers,
        )

        print(
            f"downloaded={stats['downloaded']}  "
            f"skipped(sha)={stats['skipped_sha']}  "
            f"skipped(size)={stats['skipped_size']}  "
            f"skipped(failed)={stats['skipped_failed']}  "
            f"errors={stats['errors']}"
        )

        for k in totals:
            totals[k] += stats[k]

        # Persist failures after each repo
        _save_failures(failures, contents_path)

    print("\n" + "=" * 55)
    print("SYNC COMPLETE")
    print(f"  Downloaded      : {totals['downloaded']}")
    print(f"  Skipped SHA     : {totals['skipped_sha']}")
    print(f"  Skipped size    : {totals['skipped_size']}")
    print(f"  Skipped failed  : {totals['skipped_failed']}")
    print(f"  Not found       : {totals['not_found']}")
    print(f"  Errors          : {totals['errors']}")
    if totals["errors"] or totals["not_found"]:
        print(f"  Failures file   : {_failures_path(contents_path)}")
        print('  Set "skip": true on any permanently inaccessible files in that file.')


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
        description="Download top-level files for every dataset repo.",
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
        help="Parallel download threads",
    )
    parser.add_argument(
        "--max-size",
        type=int,
        default=DEFAULT_MAX_SIZE,
        help="Skip blobs larger than this (bytes, default 524288 = 512 KB)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if SHA matches",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Re-attempt files recorded in the failures dict "
        "(skip=true entries excluded)",
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
        "--token",
        default=None,
        help="GitHub PAT (defaults to $GITHUB_TOKEN).",
    )
    args = parser.parse_args(argv)

    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Warning: GITHUB_TOKEN not set. Downloads will be rate-limited.")

    sync_all(
        contents_path=args.contents,
        datasets_dir=args.datasets,
        token=token,
        test_repo=args.repo,
        workers=args.workers,
        max_size=args.max_size,
        force=args.force,
        retry_failed=args.retry_failed,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
