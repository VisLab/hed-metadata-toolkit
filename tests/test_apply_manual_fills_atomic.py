"""test_apply_manual_fills_atomic.py — Regression tests for the
2026-05-06 truncation bug.

Background: an early version of `write_registry` opened the destination
file directly with `open("w", ...)` and streamed rows.  In production a
write was truncated mid-stream, leaving the registry with 1187 of its
1296 rows and the boundary row's status field cut off after "needs"
(the leading characters of "needs_review").  The fix is atomic
write-tmp-then-rename with fsync.  These tests pin the contract.

Kept in a separate file from `test_apply_manual_fills.py` so the
regression rationale stays clearly traceable; the existing 19 tests
in that file all use registries of <= 5 rows and would not have
caught a truncation that drops the last 100 rows.

Run:
    pytest tests/test_apply_manual_fills_atomic.py -v
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from hed_metadata_toolkit.citations.apply_manual_fills import (  # noqa: E402
    load_registry,
    write_registry,
)

COLUMNS = [
    "citation_id", "doi", "url", "source_link", "pub_id",
    "first_author_family", "year", "title",
    "status", "metadata_source", "verified_on", "notes",
]


def _empty_row(cit: str) -> dict:
    row = dict.fromkeys(COLUMNS, "")
    row["citation_id"] = cit
    return row


def test_write_registry_preserves_full_row_count(tmp_path: Path):
    """A 200-row write must produce a file with all 200 rows intact.

    Catches both row-count loss and trailing-field truncation — the
    production bug had both.
    """
    rows: dict[str, dict] = {}
    for i in range(1, 201):
        cit = f"cit_{i:06d}"
        r = _empty_row(cit)
        r["status"] = "needs_review"
        r["url"] = f"https://example.com/{cit}"
        rows[cit] = r

    out_path = tmp_path / "registry.tsv"
    write_registry(out_path, rows, COLUMNS)

    text = out_path.read_text(encoding="utf-8")
    nonempty_lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(nonempty_lines) == 201, (
        f"Expected 1 header + 200 rows = 201 non-empty lines, "
        f"got {len(nonempty_lines)}"
    )

    # Last data row must be cit_000200 with the full status string.
    fields = nonempty_lines[-1].split("\t")
    assert fields[0] == "cit_000200"
    assert len(fields) == len(COLUMNS), (
        f"Last row has {len(fields)} fields, expected {len(COLUMNS)}; "
        f"truncation indicator"
    )
    assert fields[COLUMNS.index("status")] == "needs_review", (
        f"Last row status is {fields[COLUMNS.index('status')]!r}; "
        f"expected 'needs_review' (truncation regression)"
    )

    # Round-trip via load_registry as a final sanity check.
    reg2, _ = load_registry(out_path)
    assert len(reg2) == 200
    assert reg2["cit_000200"]["status"] == "needs_review"


def test_write_registry_atomic_on_failure(tmp_path: Path, monkeypatch):
    """If the write fails mid-stream, the original file must be intact
    and no orphan .tmp file left behind.

    Pins the atomic-write contract so callers can recover from a
    write crash by re-running, with no manual repair of the registry.
    """
    out_path = tmp_path / "registry.tsv"

    # Initial successful write
    reg_v1 = {"cit_000001": {**_empty_row("cit_000001"), "status": "auto"}}
    write_registry(out_path, reg_v1, COLUMNS)
    original_bytes = out_path.read_bytes()

    # Force the next data writerow to raise mid-stream.
    real_writerow = csv.DictWriter.writerow
    call_count = {"n": 0}

    def boom(self, row):
        call_count["n"] += 1
        # Let the header write succeed; fail on the first data row.
        if call_count["n"] >= 2:
            raise OSError("simulated write failure")
        return real_writerow(self, row)

    monkeypatch.setattr(csv.DictWriter, "writerow", boom)

    reg_v2 = {
        "cit_000001": {**_empty_row("cit_000001"),
                       "status": "manual", "doi": "10.1000/new"},
        "cit_000002": {**_empty_row("cit_000002"),
                       "status": "manual"},
    }
    with pytest.raises(OSError, match="simulated write failure"):
        write_registry(out_path, reg_v2, COLUMNS)

    # Original file untouched.
    assert out_path.read_bytes() == original_bytes

    # No orphan .tmp left behind.
    leftovers = list(tmp_path.glob("registry.tsv.*.tmp"))
    assert leftovers == [], f"orphan tmp files left behind: {leftovers}"
