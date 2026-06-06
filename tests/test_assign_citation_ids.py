"""test_assign_citation_ids.py — Fixture tests for assign_citation_ids.

Tests cover:
  - DOI link → status=auto
  - URL-only link → status=needs_review
  - Skip-list match → status=not_a_citation
  - Existing registry key → reused without new assignment
  - Idempotency: running twice produces byte-identical registry + mapping

No network.  All I/O through tmp_path fixtures.

Run:
    pytest tests/test_assign_citation_ids.py -v
"""

from __future__ import annotations

import csv
from pathlib import Path


from hed_metadata_toolkit.citations.assign_citation_ids import (  # noqa: E402
    MAPPING_COLUMNS,
    REGISTRY_COLUMNS,
    assign,
    write_tsv,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_registry(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=REGISTRY_COLUMNS, delimiter="\t", lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in REGISTRY_COLUMNS})


def _write_citations(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=MAPPING_COLUMNS, delimiter="\t", lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in MAPPING_COLUMNS})


def _write_skip_list(path: Path, patterns: list[str]) -> None:
    path.write_text("\n".join(patterns) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests — status outcomes
# ---------------------------------------------------------------------------

def test_doi_link_gets_auto_status(tmp_path: Path) -> None:
    """A new DOI link gets cit_000001 with status=auto."""
    registry_path = tmp_path / "registry.tsv"
    citations_path = tmp_path / "citations.tsv"
    skip_path = tmp_path / "skip.txt"

    _write_registry(registry_path, [])
    _write_citations(citations_path, [
        {"dataset_id": "ds001", "citation_id": "",
         "raw_link": "https://doi.org/10.1234/xyz", "UnlinkedAck": "no"},
    ])
    _write_skip_list(skip_path, [])

    registry, mapping, new_count = assign(registry_path, citations_path, skip_path)

    assert new_count == 1
    assert len(registry) == 1
    assert registry[0]["citation_id"] == "cit_000001"
    assert registry[0]["doi"] == "10.1234/xyz"
    assert registry[0]["url"] == ""
    assert registry[0]["status"] == "auto"
    assert mapping[0]["citation_id"] == "cit_000001"


def test_url_only_gets_needs_review(tmp_path: Path) -> None:
    """A new URL-only link gets status=needs_review."""
    registry_path = tmp_path / "registry.tsv"
    citations_path = tmp_path / "citations.tsv"
    skip_path = tmp_path / "skip.txt"

    _write_registry(registry_path, [])
    _write_citations(citations_path, [
        {"dataset_id": "ds001", "citation_id": "",
         "raw_link": "https://example.com/some-paper", "UnlinkedAck": "no"},
    ])
    _write_skip_list(skip_path, [])

    registry, mapping, new_count = assign(registry_path, citations_path, skip_path)

    assert new_count == 1
    assert registry[0]["status"] == "needs_review"
    assert registry[0]["url"] == "https://example.com/some-paper"
    assert registry[0]["doi"] == ""
    assert mapping[0]["citation_id"] == "cit_000001"


def test_junk_link_gets_not_a_citation(tmp_path: Path) -> None:
    """A link matching the skip-list gets status=not_a_citation."""
    registry_path = tmp_path / "registry.tsv"
    citations_path = tmp_path / "citations.tsv"
    skip_path = tmp_path / "skip.txt"

    _write_registry(registry_path, [])
    _write_citations(citations_path, [
        {"dataset_id": "ds001", "citation_id": "",
         "raw_link": "https://www.fil.ion.ucl.ac.uk/spm/software/spm12/",
         "UnlinkedAck": "no"},
    ])
    _write_skip_list(skip_path, ["fil.ion.ucl.ac.uk/spm"])

    registry, mapping, new_count = assign(registry_path, citations_path, skip_path)

    assert new_count == 1
    assert registry[0]["status"] == "not_a_citation"
    assert mapping[0]["citation_id"] == "cit_000001"


# ---------------------------------------------------------------------------
# Test — reuse of existing registry key
# ---------------------------------------------------------------------------

def test_existing_key_reused_no_new_assignment(tmp_path: Path) -> None:
    """Same paper from two datasets shares one cit_id; new_count stays 0."""
    registry_path = tmp_path / "registry.tsv"
    citations_path = tmp_path / "citations.tsv"
    skip_path = tmp_path / "skip.txt"

    _write_registry(registry_path, [{
        "citation_id": "cit_000001", "doi": "10.1234/xyz", "url": "",
        "source_link": "https://doi.org/10.1234/xyz", "status": "auto",
        "pub_id": "", "first_author_family": "", "year": "", "title": "",
        "metadata_source": "", "verified_on": "", "notes": "",
    }])
    _write_citations(citations_path, [
        {"dataset_id": "ds001", "citation_id": "",
         "raw_link": "https://doi.org/10.1234/xyz", "UnlinkedAck": "no"},
        {"dataset_id": "ds002", "citation_id": "",
         "raw_link": "https://doi.org/10.1234/xyz", "UnlinkedAck": "no"},
    ])
    _write_skip_list(skip_path, [])

    registry, mapping, new_count = assign(registry_path, citations_path, skip_path)

    assert new_count == 0
    assert len(registry) == 1
    assert all(r["citation_id"] == "cit_000001" for r in mapping)


# ---------------------------------------------------------------------------
# Test — idempotency
# ---------------------------------------------------------------------------

def test_idempotency(tmp_path: Path) -> None:
    """Running assign twice produces byte-identical registry and mapping files."""
    registry_path = tmp_path / "registry.tsv"
    citations_path = tmp_path / "citations.tsv"
    skip_path = tmp_path / "skip.txt"

    _write_registry(registry_path, [])
    _write_citations(citations_path, [
        {"dataset_id": "ds001", "citation_id": "",
         "raw_link": "https://doi.org/10.1234/xyz", "UnlinkedAck": "no"},
        {"dataset_id": "ds002", "citation_id": "",
         "raw_link": "https://example.com/paper", "UnlinkedAck": "no"},
    ])
    _write_skip_list(skip_path, [])

    # First run — assigns new IDs
    registry1, mapping1, new_count1 = assign(registry_path, citations_path, skip_path)
    assert new_count1 == 2
    write_tsv(registry_path, registry1, REGISTRY_COLUMNS)
    write_tsv(citations_path, mapping1, MAPPING_COLUMNS)

    registry_bytes_1 = registry_path.read_bytes()
    citations_bytes_1 = citations_path.read_bytes()

    # Second run — everything already in registry
    registry2, mapping2, new_count2 = assign(registry_path, citations_path, skip_path)
    assert new_count2 == 0
    write_tsv(registry_path, registry2, REGISTRY_COLUMNS)
    write_tsv(citations_path, mapping2, MAPPING_COLUMNS)

    registry_bytes_2 = registry_path.read_bytes()
    citations_bytes_2 = citations_path.read_bytes()

    assert registry_bytes_1 == registry_bytes_2, (
        "Registry changed on second run — not idempotent"
    )
    assert citations_bytes_1 == citations_bytes_2, (
        "Mapping file changed on second run — not idempotent"
    )


# ---------------------------------------------------------------------------
# Test — counter continues above existing max ID
# ---------------------------------------------------------------------------

def test_new_id_follows_existing_max(tmp_path: Path) -> None:
    """New assignment picks up from max existing cit_id + 1."""
    registry_path = tmp_path / "registry.tsv"
    citations_path = tmp_path / "citations.tsv"
    skip_path = tmp_path / "skip.txt"

    _write_registry(registry_path, [{
        "citation_id": "cit_000042", "doi": "10.1000/existing", "url": "",
        "source_link": "https://doi.org/10.1000/existing", "status": "auto",
        "pub_id": "", "first_author_family": "", "year": "", "title": "",
        "metadata_source": "", "verified_on": "", "notes": "",
    }])
    _write_citations(citations_path, [
        {"dataset_id": "ds001", "citation_id": "",
         "raw_link": "https://doi.org/10.9999/new", "UnlinkedAck": "no"},
    ])
    _write_skip_list(skip_path, [])

    registry, mapping, new_count = assign(registry_path, citations_path, skip_path)

    assert new_count == 1
    new_row = next(r for r in registry if r["doi"] == "10.9999/new")
    assert new_row["citation_id"] == "cit_000043"


# ---------------------------------------------------------------------------
# Test — source_link secondary index (Phase 2D DOI upgrade scenario)
# ---------------------------------------------------------------------------

def test_source_link_fallback_after_doi_upgrade(tmp_path: Path) -> None:
    """Registry entry upgraded from URL-only to DOI still matches original raw_link.

    Simulates Phase 2D: a curator added doi='10.1234/xyz' to a registry row
    that was originally URL-only (url='https://example.com/paper').  The
    primary key is now ('doi', '10.1234/xyz'); the url column is empty.
    A mapping row still carries the original raw URL.  Without the source_link
    secondary index this would be treated as a new link and assigned a
    duplicate cit_id.
    """
    registry_path = tmp_path / "registry.tsv"
    citations_path = tmp_path / "citations.tsv"
    skip_path = tmp_path / "skip.txt"

    # Registry entry: doi added post-creation; url cleared; source_link is
    # the original raw URL that the mapping row still references.
    _write_registry(registry_path, [{
        "citation_id": "cit_000001",
        "doi": "10.1234/xyz",
        "url": "",
        "source_link": "https://example.com/paper",
        "status": "auto",
        "pub_id": "", "first_author_family": "", "year": "", "title": "",
        "metadata_source": "", "verified_on": "", "notes": "",
    }])
    # Mapping row still has the original URL as raw_link.
    _write_citations(citations_path, [
        {"dataset_id": "ds001", "citation_id": "",
         "raw_link": "https://example.com/paper", "UnlinkedAck": "no"},
    ])
    _write_skip_list(skip_path, [])

    registry, mapping, new_count = assign(registry_path, citations_path, skip_path)

    # Must be a no-op: no new ID should be assigned.
    assert new_count == 0, (
        "Expected 0 new IDs — source_link fallback should match cit_000001"
    )
    assert len(registry) == 1, "Registry should still have exactly 1 row"
    assert mapping[0]["citation_id"] == "cit_000001"
