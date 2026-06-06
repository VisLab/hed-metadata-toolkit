"""
test_cache.py — Tests for cache.py.

Covers:
  - happy-path round-trip: write then read returns the same response
  - the {} "not found" case is cached
  - the None "transient error" case is NOT cached
  - corrupted on-disk JSON falls through to a re-fetch
  - key-collision metadata mismatch falls through to a re-fetch
  - atomic write: no .tmp leftover after a successful write
  - atomic write: an error during the rename path leaves no partial cache_path
  - atomic write: tmp filenames are per-PID and unique-ish
  - staleness window: cache files within max_age_days are served, older ones
    are ignored, newest in-window file wins, corrupt newer files fall through
    to valid older ones in the window, future-dated buckets are ignored,
    writes always go to today's bucket.

Run:
    pytest Claude-research/code/literature_search/test_cache.py -v

All tests use tmp_path; no network, no real cache directory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from hed_metadata_toolkit.cache import cache_get_or_fetch


# ---------------------------------------------------------------------------
# Round-trip behaviour
# ---------------------------------------------------------------------------

def test_round_trip_writes_and_reads_back(tmp_path: Path) -> None:
    """A successful fetch is persisted; the next call returns the cached body."""
    calls = {"n": 0}

    def fetch() -> dict:
        calls["n"] += 1
        return {"hello": "world", "n": calls["n"]}

    first = cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="10.1/foo",
        fetch=fetch, today="2026-05-01", stable=True,
    )
    second = cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="10.1/foo",
        fetch=fetch, today="2026-05-01", stable=True,
    )
    assert first == {"hello": "world", "n": 1}
    assert second == {"hello": "world", "n": 1}
    assert calls["n"] == 1


def test_empty_dict_is_cached_as_not_found(tmp_path: Path) -> None:
    """A 404-style {} is persisted and served; second call doesn't re-fetch."""
    calls = {"n": 0}

    def fetch() -> dict:
        calls["n"] += 1
        return {}

    first = cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="10.1/missing",
        fetch=fetch, stable=True,
    )
    second = cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="10.1/missing",
        fetch=fetch, stable=True,
    )
    assert first == {}
    assert second == {}
    assert calls["n"] == 1


def test_none_signals_transient_error_and_is_not_cached(tmp_path: Path) -> None:
    """A None return (network failure) does NOT write to disk; next call re-fetches."""
    state = {"n": 0}

    def fetch() -> dict | None:
        state["n"] += 1
        if state["n"] < 3:
            return None
        return {"ok": True}

    first = cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="10.1/flaky",
        fetch=fetch, stable=True,
    )
    second = cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="10.1/flaky",
        fetch=fetch, stable=True,
    )
    third = cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="10.1/flaky",
        fetch=fetch, stable=True,
    )
    assert first == {}
    assert second == {}
    assert third == {"ok": True}
    assert state["n"] == 3


# ---------------------------------------------------------------------------
# Corruption / collision fall-through
# ---------------------------------------------------------------------------

def test_corrupted_json_falls_through_to_refetch(tmp_path: Path) -> None:
    """A half-written cache file (invalid JSON) triggers a re-fetch and overwrite."""
    cache_path = tmp_path / "crossref" / "stable" / _expected_hex("10.1/corrupt")
    cache_path = cache_path.with_suffix(".json")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("{ not valid json", encoding="utf-8")

    def fetch() -> dict:
        return {"recovered": True}

    result = cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="10.1/corrupt",
        fetch=fetch, stable=True,
    )
    assert result == {"recovered": True}
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    assert data["response"] == {"recovered": True}


def test_key_metadata_mismatch_falls_through(tmp_path: Path) -> None:
    """A SHA-1 collision (mismatched stored key) is caught and re-fetched."""
    cache_path = tmp_path / "crossref" / "stable" / _expected_hex("10.1/key-A")
    cache_path = cache_path.with_suffix(".json")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({
            "source": "crossref",
            "key": "10.1/key-B",
            "fetched_on": "2026-05-01",
            "response": {"wrong": True},
        }),
        encoding="utf-8",
    )

    def fetch() -> dict:
        return {"correct": True}

    result = cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="10.1/key-A",
        fetch=fetch, stable=True,
    )
    assert result == {"correct": True}


# ---------------------------------------------------------------------------
# Atomic-write behaviour
# ---------------------------------------------------------------------------

def test_no_tmp_leftover_after_successful_write(tmp_path: Path) -> None:
    """A clean write leaves cache_path on disk and no orphan .tmp files."""
    cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="10.1/clean",
        fetch=lambda: {"ok": True}, stable=True,
    )
    cache_dir = tmp_path / "crossref" / "stable"
    files = list(cache_dir.iterdir())
    assert len(files) == 1, f"expected one file, got: {files}"
    assert files[0].suffix == ".json"
    assert not any(".tmp" in p.name for p in cache_dir.rglob("*"))


def test_rename_failure_leaves_no_partial_cache_path(tmp_path: Path) -> None:
    """If Path.replace raises, cache_path must not exist as a partial file."""
    target_dir = tmp_path / "crossref" / "stable"
    original_replace = Path.replace

    def boom(self: Path, target: Path) -> Path:
        raise OSError("simulated rename failure")

    with patch.object(Path, "replace", boom):
        with pytest.raises(OSError, match="simulated rename failure"):
            cache_get_or_fetch(
                cache_dir=tmp_path, source="crossref", key="10.1/fail",
                fetch=lambda: {"ok": True}, stable=True,
            )

    cache_files = list(target_dir.glob("*.json")) if target_dir.exists() else []
    assert cache_files == [], f"expected no cache_path; found: {cache_files}"
    tmp_files = list(target_dir.glob("*.tmp")) if target_dir.exists() else []
    assert tmp_files == [], f"expected no leftover tmp files; found: {tmp_files}"

    assert Path.replace is original_replace


def test_tmp_filename_includes_pid(tmp_path: Path) -> None:
    """Tmp filenames are per-PID so concurrent writers don't share one."""
    captured: list[Path] = []

    def fake_write(self: Path, *args, **kwargs) -> int:
        captured.append(self)
        return 0

    with patch.object(Path, "write_text", fake_write):
        with patch.object(Path, "replace", lambda self, target: None):
            cache_get_or_fetch(
                cache_dir=tmp_path, source="crossref", key="10.1/pid-check",
                fetch=lambda: {"ok": True}, stable=True,
            )

    assert captured, "write_text was not called"
    tmp_path_used = captured[0]
    assert f".{os.getpid()}.tmp" in tmp_path_used.name, (
        f"tmp filename {tmp_path_used.name!r} should embed the PID "
        f"({os.getpid()})"
    )


# ---------------------------------------------------------------------------
# Staleness window (date-stamped layout, max_age_days)
# ---------------------------------------------------------------------------
#
# These tests pin "today" via the cache_get_or_fetch `today` parameter so
# they do not depend on the system clock.  All tests use the date-stamped
# layout (stable=False, the default) -- that is the layout the staleness
# window applies to.

def test_recent_date_stamped_cache_is_served_within_window(tmp_path: Path) -> None:
    """A search-style cache from 16 days ago is served when window is 30 days."""
    counter = {"n": 0}

    def fetch() -> dict:
        counter["n"] += 1
        return {"version": counter["n"]}

    first = cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="search-x",
        fetch=fetch, today="2026-04-15",
    )
    second = cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="search-x",
        fetch=fetch, today="2026-05-01", max_age_days=30,
    )
    assert first == {"version": 1}
    assert second == {"version": 1}
    assert counter["n"] == 1


def test_outside_window_triggers_refetch(tmp_path: Path) -> None:
    """A cache file older than max_age_days is ignored; the call re-fetches."""
    counter = {"n": 0}

    def fetch() -> dict:
        counter["n"] += 1
        return {"version": counter["n"]}

    cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="search-x",
        fetch=fetch, today="2026-03-30",
    )
    second = cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="search-x",
        fetch=fetch, today="2026-05-01", max_age_days=30,
    )
    assert second == {"version": 2}
    assert counter["n"] == 2

    old = tmp_path / "crossref" / "2026-03-30" / f"{_expected_hex('search-x')}.json"
    assert old.exists()


def test_max_age_zero_restricts_lookup_to_today(tmp_path: Path) -> None:
    """max_age_days=0 only serves cache files in today's bucket."""
    counter = {"n": 0}

    def fetch() -> dict:
        counter["n"] += 1
        return {"version": counter["n"]}

    cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="search-x",
        fetch=fetch, today="2026-04-30",
    )
    second = cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="search-x",
        fetch=fetch, today="2026-05-01", max_age_days=0,
    )
    assert second == {"version": 2}
    assert counter["n"] == 2


def test_newest_within_window_wins(tmp_path: Path) -> None:
    """When multiple in-window cache files exist, the newest is served."""
    counter = {"n": 0}

    def fetch() -> dict:
        counter["n"] += 1
        return {"version": counter["n"]}

    cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="search-x",
        fetch=fetch, today="2026-04-10",
    )
    cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="search-x",
        fetch=fetch, today="2026-04-25", max_age_days=0,
    )
    result = cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="search-x",
        fetch=fetch, today="2026-05-01", max_age_days=30,
    )
    assert result == {"version": 2}
    assert counter["n"] == 2


def test_corrupted_recent_falls_through_to_older_in_window(tmp_path: Path) -> None:
    """A corrupt newer cache file falls through to a valid older one in window."""
    cache_hex = _expected_hex("search-x")

    (tmp_path / "crossref" / "2026-04-30").mkdir(parents=True)
    (tmp_path / "crossref" / "2026-04-30" / f"{cache_hex}.json").write_text(
        "{corrupt", encoding="utf-8",
    )
    (tmp_path / "crossref" / "2026-04-15").mkdir(parents=True)
    (tmp_path / "crossref" / "2026-04-15" / f"{cache_hex}.json").write_text(
        json.dumps({
            "source": "crossref",
            "key": "search-x",
            "fetched_on": "2026-04-15",
            "response": {"valid": True},
        }),
        encoding="utf-8",
    )

    def fetch() -> dict:
        raise AssertionError("fetch should not be called; older record is valid")

    result = cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="search-x",
        fetch=fetch, today="2026-05-01", max_age_days=30,
    )
    assert result == {"valid": True}


def test_future_dated_buckets_are_ignored(tmp_path: Path) -> None:
    """Date subdirectories newer than `today` are ignored (defensive)."""
    cache_hex = _expected_hex("search-x")

    (tmp_path / "crossref" / "2026-06-15").mkdir(parents=True)
    (tmp_path / "crossref" / "2026-06-15" / f"{cache_hex}.json").write_text(
        json.dumps({
            "source": "crossref",
            "key": "search-x",
            "fetched_on": "2026-06-15",
            "response": {"from_future": True},
        }),
        encoding="utf-8",
    )

    counter = {"n": 0}
    def fetch() -> dict:
        counter["n"] += 1
        return {"from_today": True}

    result = cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="search-x",
        fetch=fetch, today="2026-05-01", max_age_days=30,
    )
    assert result == {"from_today": True}
    assert counter["n"] == 1


def test_writes_always_go_to_today_bucket(tmp_path: Path) -> None:
    """A miss-then-fetch writes today's bucket regardless of max_age_days."""
    cache_get_or_fetch(
        cache_dir=tmp_path, source="crossref", key="search-x",
        fetch=lambda: {"ok": True},
        today="2026-05-01", max_age_days=30,
    )
    today_path = (
        tmp_path / "crossref" / "2026-05-01"
        / f"{_expected_hex('search-x')}.json"
    )
    assert today_path.exists()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _expected_hex(key: str) -> str:
    """Mirror of cache._cache_key_hex for use in test fixtures."""
    import hashlib
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
