"""test_apply_manual_fills.py — Fixture-driven tests for apply_manual_fills.

Covers:
  - Each recognised JSON status value (with and without DOI)
  - Curator-intent gate: status-only no-DOI entries with empty notes
    AND empty resolved_url are deferred to the resolver, not rejected
    (the 2026-05-06 audit found 7 PsyArXiv/bioRxiv preprint_only rows
    of this shape that were auto-staged by an earlier classification
    step; auto-rejecting them was wrong).
  - Malformed resolved_url (cit_000445 case)
  - cit_id not in registry
  - Idempotency (two runs → identical registry)
  - Conflicting DOI (registry has X, JSON has Y → warn + JSON wins)
  - Already-resolved row (pub_id set → skipped)
  - is_url_shaped edge cases

No network.  No file I/O (uses in-memory dicts except where testing write_registry).

Run:
    pytest tests/test_apply_manual_fills.py -v
"""

from __future__ import annotations

import copy
from pathlib import Path


from hed_metadata_toolkit.citations.apply_manual_fills import (  # noqa: E402
    apply_fills,
    is_url_shaped,
    load_registry,
    write_registry,
)

TODAY = "2026-05-06"

COLUMNS = [
    "citation_id", "doi", "url", "source_link", "pub_id",
    "first_author_family", "year", "title",
    "status", "metadata_source", "verified_on", "notes",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row(citation_id: str, *, doi: str = "", pub_id: str = "",
         status: str = "auto", notes: str = "", verified_on: str = "") -> dict:
    return {
        "citation_id": citation_id, "doi": doi, "url": "",
        "source_link": "", "pub_id": pub_id,
        "first_author_family": "", "year": "", "title": "",
        "status": status, "metadata_source": "",
        "verified_on": verified_on, "notes": notes,
    }


def _reg(*rows: dict) -> dict[str, dict]:
    return {r["citation_id"]: r for r in rows}


def _entry(cit: str, *, status: str = "journal_article",
           doi: str | None = None, resolved_url: str | None = None,
           notes: str = "") -> dict:
    return {
        "citation_id": cit,
        "url": "https://osf.io/test",
        "status": status,
        "doi": doi,
        "resolved_url": resolved_url,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Tests: DOI-bearing statuses → applied as manual
# ---------------------------------------------------------------------------

def test_journal_article_with_doi_applied():
    """journal_article with doi → status=manual, doi set, notes prefixed."""
    reg = _reg(_row("cit_000001"))
    warns: list[str] = []
    stats = apply_fills([_entry("cit_000001", doi="10.1000/test")], reg, TODAY, warns)
    r = reg["cit_000001"]
    assert r["doi"] == "10.1000/test"
    assert r["status"] == "manual"
    assert r["notes"].startswith("manual: journal_article")
    assert r["verified_on"] == TODAY
    assert "cit_000001" in stats["applied_doi"]
    assert not warns


def test_preprint_only_with_doi_applied_as_manual():
    """preprint_only that has a DOI → DOI path runs, not rejected."""
    reg = _reg(_row("cit_000815"))
    warns: list[str] = []
    stats = apply_fills(
        [_entry("cit_000815", status="preprint_only", doi="10.31219/osf.io/j5v9b")],
        reg, TODAY, warns,
    )
    r = reg["cit_000815"]
    assert r["doi"] == "10.31219/osf.io/j5v9b"
    assert r["status"] == "manual"
    assert "manual: preprint_only" in r["notes"]
    assert "cit_000815" in stats["applied_doi"]


def test_valid_resolved_url_included_in_notes():
    """A proper https:// resolved_url is appended to notes."""
    reg = _reg(_row("cit_000187"))
    warns: list[str] = []
    apply_fills(
        [_entry("cit_000187", doi="10.1073/pnas.1711571115",
                resolved_url="https://www.pnas.org/doi/10.1073/pnas.1711571115",
                notes="Resolved to PNAS.")],
        reg, TODAY, warns,
    )
    r = reg["cit_000187"]
    assert "resolved_url: https://www.pnas.org" in r["notes"]
    assert "Resolved to PNAS." in r["notes"]
    assert not warns


# ---------------------------------------------------------------------------
# Tests: null-DOI statuses with curator intent (notes filled) → rejected
# ---------------------------------------------------------------------------
# These tests intentionally include a `notes` argument so they exercise the
# rejection path under the post-2026-05-06 policy: rejection requires that
# the curator left some intent (notes or resolved_url).  See the audit-
# triggered policy fix below for the no-intent (deferral) tests.

def test_supplement_no_doi_rejected():
    reg = _reg(_row("cit_000002"))
    warns: list[str] = []
    stats = apply_fills(
        [_entry("cit_000002", status="supplement",
                notes="OSF repository, no associated paper found.")],
        reg, TODAY, warns,
    )
    assert reg["cit_000002"]["status"] == "rejected"
    assert "manual-reject: supplement" in reg["cit_000002"]["notes"]
    assert "cit_000002" in stats["applied_rejected"]


def test_dataset_no_doi_rejected():
    reg = _reg(_row("cit_000003"))
    warns: list[str] = []
    apply_fills(
        [_entry("cit_000003", status="dataset",
                notes="OSF dataset only, no paper.")],
        reg, TODAY, warns,
    )
    assert reg["cit_000003"]["status"] == "rejected"
    assert "manual-reject: dataset" in reg["cit_000003"]["notes"]


def test_reject_no_doi_rejected():
    reg = _reg(_row("cit_000004"))
    warns: list[str] = []
    apply_fills(
        [_entry("cit_000004", status="reject",
                notes="duplicate of another entry.")],
        reg, TODAY, warns,
    )
    assert reg["cit_000004"]["status"] == "rejected"


def test_no_paper_no_doi_rejected():
    reg = _reg(_row("cit_000005"))
    warns: list[str] = []
    apply_fills(
        [_entry("cit_000005", status="no_paper",
                notes="OSF repository with no associated paper.")],
        reg, TODAY, warns,
    )
    assert reg["cit_000005"]["status"] == "rejected"


def test_unresolved_no_doi_rejected():
    reg = _reg(_row("cit_000777"))
    warns: list[str] = []
    apply_fills(
        [_entry("cit_000777", status="unresolved", notes="Could not locate a paper.")],
        reg, TODAY, warns,
    )
    r = reg["cit_000777"]
    assert r["status"] == "rejected"
    assert "Could not locate a paper." in r["notes"]


def test_conference_proceeding_no_doi_rejected():
    reg = _reg(_row("cit_000732"))
    warns: list[str] = []
    apply_fills(
        [_entry("cit_000732", status="conference_proceeding",
                notes="Cognitive Science Society proceedings.")],
        reg, TODAY, warns,
    )
    assert reg["cit_000732"]["status"] == "rejected"
    assert not warns  # known status, no spurious warning


def test_preprint_only_no_doi_rejected_with_notes():
    """preprint_only with curator-supplied notes is a real rejection."""
    reg = _reg(_row("cit_000888"))
    warns: list[str] = []
    apply_fills(
        [_entry("cit_000888", status="preprint_only",
                notes="No journal version exists; checked Crossref and Google Scholar.")],
        reg, TODAY, warns,
    )
    assert reg["cit_000888"]["status"] == "rejected"
    assert "manual-reject: preprint_only" in reg["cit_000888"]["notes"]


# ---------------------------------------------------------------------------
# Tests: null-DOI status-only entries (no curator intent) → DEFERRED
# ---------------------------------------------------------------------------
# These pin the 2026-05-06 audit-triggered policy.  A JSON entry with
# status set but empty notes AND empty resolved_url AND null doi is
# treated as auto-staged from a previous classification step rather
# than as a curator decision.  Such entries must be deferred to the
# resolver (which can extract preprint DOIs from PsyArXiv/bioRxiv URLs
# via Path B), not auto-rejected.

def test_status_only_preprint_only_deferred_to_resolver():
    """preprint_only with no notes, no doi, no resolved_url → deferred,
    NOT rejected.  Catches the 7 PsyArXiv/bioRxiv auto-stagers found
    in resolved_references_050526.json on 2026-05-06."""
    reg = _reg(_row("cit_000992"))  # status='auto' from _row default
    warns: list[str] = []
    stats = apply_fills(
        [_entry("cit_000992", status="preprint_only")],
        reg, TODAY, warns,
    )
    # Registry row left as-is; resolver will attempt this row later.
    r = reg["cit_000992"]
    assert r["status"] == "auto"  # unchanged from fixture
    assert r["doi"] == ""
    assert r["notes"] == ""
    # Stats record the deferral
    assert "cit_000992" in stats["deferred_no_intent"]
    assert "cit_000992" not in stats["applied_rejected"]
    # Exactly one warning explaining the deferral
    assert len(warns) == 1
    w = warns[0].lower()
    assert "cit_000992" in warns[0]
    assert ("auto-staged" in w) or ("deferring" in w) or ("no curator" in w)


def test_status_only_with_resolved_url_is_real_rejection():
    """If resolved_url is present (curator filled it), reject as before
    even if notes are empty."""
    reg = _reg(_row("cit_000777"))
    warns: list[str] = []
    apply_fills(
        [_entry("cit_000777", status="unresolved",
                resolved_url="https://example.com/page-i-checked")],
        reg, TODAY, warns,
    )
    assert reg["cit_000777"]["status"] == "rejected"


# ---------------------------------------------------------------------------
# Test: malformed resolved_url (cit_000445 case)
# ---------------------------------------------------------------------------

def test_malformed_resolved_url_warning_and_applied():
    """resolved_url that is a title, not a URL → warning + applied with prefix in notes."""
    reg = _reg(_row("cit_000445"))
    title = "Intersubject correlations in reward and mentalizing brain circuits"
    warns: list[str] = []
    stats = apply_fills(
        [_entry("cit_000445", doi="10.1038/s41598-024-62341-3",
                resolved_url=title,
                notes="Note: original URL was in error.")],
        reg, TODAY, warns,
    )
    r = reg["cit_000445"]
    # DOI applied correctly
    assert r["doi"] == "10.1038/s41598-024-62341-3"
    assert r["status"] == "manual"
    # Title lands in notes with marker prefix
    assert "[malformed resolved_url]" in r["notes"]
    assert title in r["notes"]
    # Exactly one warning about the malformed URL
    assert len(warns) == 1
    assert "malformed" in warns[0].lower()
    assert "cit_000445" in stats["applied_doi"]


# ---------------------------------------------------------------------------
# Test: cit_id not in registry
# ---------------------------------------------------------------------------

def test_cit_not_in_registry_warns_and_skips():
    reg = _reg(_row("cit_000001"))
    warns: list[str] = []
    stats = apply_fills([_entry("cit_999999", doi="10.1000/x")], reg, TODAY, warns)
    assert "cit_999999" not in reg
    assert len(warns) == 1
    assert "cit_999999" in warns[0]
    assert "cit_999999" in stats["skipped_not_in_registry"]


# ---------------------------------------------------------------------------
# Test: idempotency
# ---------------------------------------------------------------------------

def test_idempotency_two_runs_identical():
    """Running apply_fills twice on the same input leaves the registry unchanged."""
    reg = _reg(
        _row("cit_000001"),
        _row("cit_000002"),
        _row("cit_000003"),
    )
    entries = [
        _entry("cit_000001", doi="10.1000/abc",
               resolved_url="https://example.com/abc", notes="Some note."),
        _entry("cit_000002", status="supplement",
               notes="OSF supplement, no paper."),
        _entry("cit_000003", status="unresolved",
               notes="Could not find a paper."),
    ]

    # First run
    apply_fills(entries, reg, TODAY, [])
    snapshot1 = copy.deepcopy(reg)

    # Second run — must be a no-op
    warns2: list[str] = []
    apply_fills(entries, reg, TODAY, warns2)
    snapshot2 = copy.deepcopy(reg)

    assert snapshot1 == snapshot2, "Registry changed on second run (not idempotent)"
    # No warnings about conflicts on the second run
    conflict_warns = [w for w in warns2 if "preferring json" in w]
    assert not conflict_warns


# ---------------------------------------------------------------------------
# Test: conflicting DOI (JSON wins)
# ---------------------------------------------------------------------------

def test_conflicting_doi_json_wins():
    """When registry has doi=X and JSON has doi=Y, warn and prefer JSON."""
    reg = _reg(_row("cit_000001", doi="10.1000/old"))
    warns: list[str] = []
    stats = apply_fills([_entry("cit_000001", doi="10.1000/new")], reg, TODAY, warns)
    assert reg["cit_000001"]["doi"] == "10.1000/new"
    assert len(warns) == 1
    assert "preferring json" in warns[0].lower()
    assert "cit_000001" in stats["applied_doi"]


# ---------------------------------------------------------------------------
# Test: already-resolved row (pub_id set) → skipped
# ---------------------------------------------------------------------------

def test_already_resolved_pub_id_skipped():
    """Row with pub_id set is never overwritten."""
    reg = _reg(_row("cit_000001", pub_id="pub_abc12345", doi="10.1000/existing"))
    original = copy.deepcopy(reg["cit_000001"])
    warns: list[str] = []
    stats = apply_fills([_entry("cit_000001", doi="10.1000/existing")], reg, TODAY, warns)
    assert reg["cit_000001"] == original
    assert "cit_000001" in stats["skipped_already_resolved"]


def test_already_resolved_pub_id_conflicting_doi_warns():
    """Resolved row with mismatched doi in JSON → warning, still skipped."""
    reg = _reg(_row("cit_000001", pub_id="pub_abc12345", doi="10.1000/old"))
    warns: list[str] = []
    stats = apply_fills([_entry("cit_000001", doi="10.1000/different")], reg, TODAY, warns)
    assert reg["cit_000001"]["doi"] == "10.1000/old"  # unchanged
    assert "cit_000001" in stats["skipped_already_resolved"]
    assert any("cit_000001" in w for w in warns)


# ---------------------------------------------------------------------------
# Test: is_url_shaped
# ---------------------------------------------------------------------------

def test_is_url_shaped_valid():
    assert is_url_shaped("https://example.com") is True
    assert is_url_shaped("http://example.com/path?q=1") is True
    assert is_url_shaped("doi:10.1000/test") is True
    assert is_url_shaped("DOI:10.1000/test") is True  # case-insensitive


def test_is_url_shaped_invalid():
    assert is_url_shaped("Some Paper Title") is False
    assert is_url_shaped("") is False
    assert is_url_shaped("10.1000/bare-doi") is False  # no doi: prefix
    assert is_url_shaped("ftp://not-accepted.example.com") is False


# ---------------------------------------------------------------------------
# Test: write_registry round-trip
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Tests: manual_fill sub-object schema (new in Session 2.5D)
# ---------------------------------------------------------------------------
# These test the new manual_fill field that generate_review_queue.py emits.
# When manual_fill is not None, it takes precedence over top-level fields.

def _mf_entry(cit: str, manual_fill: dict | None, *, doi: str | None = None,
               status: str = "needs_review") -> dict:
    """Build a review-queue-style entry with a manual_fill sub-object."""
    return {
        "citation_id": cit,
        "url": "https://osf.io/test",
        "status": status,
        "doi": doi,
        "resolved_url": None,
        "notes": "",
        "manual_fill": manual_fill,
    }


def test_manual_fill_doi_sets_doi_and_status_manual():
    """manual_fill {"doi": "..."} → row.doi set, row.status="manual"."""
    reg = _reg(_row("cit_000001"))
    warns: list[str] = []
    stats = apply_fills(
        [_mf_entry("cit_000001", {"doi": "10.1000/mftest"})],
        reg, TODAY, warns,
    )
    r = reg["cit_000001"]
    assert r["doi"] == "10.1000/mftest"
    assert r["status"] == "manual"
    assert r["verified_on"] == TODAY
    assert "cit_000001" in stats["applied_doi"]
    assert not warns


def test_manual_fill_doi_overrides_top_level_doi():
    """manual_fill.doi takes precedence over the entry's top-level doi field."""
    reg = _reg(_row("cit_000001"))
    warns: list[str] = []
    # top-level doi is "10.1000/old", manual_fill has "10.1000/new"
    entry = {
        "citation_id": "cit_000001",
        "url": "https://example.com",
        "status": "needs_review",
        "doi": "10.1000/old",      # top-level (pre-filled from registry)
        "resolved_url": None,
        "notes": "",
        "manual_fill": {"doi": "10.1000/new"},
    }
    apply_fills([entry], reg, TODAY, warns)
    assert reg["cit_000001"]["doi"] == "10.1000/new"


def test_manual_fill_family_year_title_sets_metadata_fields():
    """manual_fill with family/year/title writes fields for resolver Pass 1."""
    reg = _reg(_row("cit_000001"))
    warns: list[str] = []
    stats = apply_fills(
        [_mf_entry("cit_000001", {"family": "Smith", "year": 2021,
                                   "title": "A Study of Memory"})],
        reg, TODAY, warns,
    )
    r = reg["cit_000001"]
    assert r["first_author_family"] == "Smith"
    assert r["year"] == "2021"
    assert r["title"] == "A Study of Memory"
    assert r["verified_on"] == TODAY
    # doi and status should NOT be changed by this path
    assert r["doi"] == ""
    assert r["status"] == "auto"  # unchanged from _row default
    assert "cit_000001" in stats["applied_manual_fill_meta"]
    assert not warns


def test_manual_fill_rejected_sets_status_and_notes():
    """manual_fill {"rejected": "reason"} → status=rejected, notes prefixed."""
    reg = _reg(_row("cit_000001"))
    warns: list[str] = []
    stats = apply_fills(
        [_mf_entry("cit_000001", {"rejected": "no associated paper found"})],
        reg, TODAY, warns,
    )
    r = reg["cit_000001"]
    assert r["status"] == "rejected"
    assert "manual-reject: no associated paper found" in r["notes"]
    assert r["verified_on"] == TODAY
    assert "cit_000001" in stats["applied_rejected"]
    assert not warns


def test_manual_fill_none_falls_through_to_legacy_logic():
    """manual_fill=None uses the existing top-level doi/status logic."""
    reg = _reg(_row("cit_000001"))
    warns: list[str] = []
    stats = apply_fills(
        [_mf_entry("cit_000001", None, doi="10.1000/legacy",
                   status="journal_article")],
        reg, TODAY, warns,
    )
    # Legacy path: top-level doi is used
    assert reg["cit_000001"]["doi"] == "10.1000/legacy"
    assert reg["cit_000001"]["status"] == "manual"
    assert "cit_000001" in stats["applied_doi"]


def test_manual_fill_multiple_keys_warns_and_applies_priority():
    """Multiple manual_fill keys warn; doi > family > rejected priority applies."""
    reg = _reg(_row("cit_000001"))
    warns: list[str] = []
    stats = apply_fills(
        [_mf_entry("cit_000001", {"doi": "10.1000/win", "rejected": "reason"})],
        reg, TODAY, warns,
    )
    # doi wins over rejected
    assert reg["cit_000001"]["doi"] == "10.1000/win"
    assert reg["cit_000001"]["status"] == "manual"
    # A warning was emitted about the multiple keys
    assert len(warns) == 1
    assert "multiple" in warns[0].lower() or "keys" in warns[0].lower()
    assert "cit_000001" in stats["applied_doi"]


def test_manual_fill_doi_idempotency():
    """Applying manual_fill doi twice leaves the registry byte-identical."""
    reg = _reg(_row("cit_000001"))
    entry = _mf_entry("cit_000001", {"doi": "10.1000/idem"})

    apply_fills([entry], reg, TODAY, [])
    import copy
    snap1 = copy.deepcopy(reg["cit_000001"])

    apply_fills([entry], reg, TODAY, [])
    snap2 = copy.deepcopy(reg["cit_000001"])

    assert snap1 == snap2


def test_manual_fill_family_idempotency():
    """Applying manual_fill family/year/title twice leaves the registry unchanged."""
    reg = _reg(_row("cit_000001"))
    entry = _mf_entry("cit_000001", {"family": "Jones", "year": 2020,
                                      "title": "Test Title"})
    apply_fills([entry], reg, TODAY, [])
    import copy
    snap1 = copy.deepcopy(reg["cit_000001"])
    apply_fills([entry], reg, TODAY, [])
    assert reg["cit_000001"] == snap1


def test_manual_fill_family_incomplete_warns_and_skips():
    """Incomplete family/year/title (missing year) → warning, no change."""
    reg = _reg(_row("cit_000001"))
    warns: list[str] = []
    apply_fills(
        [_mf_entry("cit_000001", {"family": "Jones", "title": "Some Title"})],
        reg, TODAY, warns,
    )
    # Row unchanged
    assert reg["cit_000001"]["first_author_family"] == ""
    assert len(warns) == 1
    assert "cit_000001" in warns[0]


def test_write_registry_round_trip(tmp_path: Path):
    """write_registry produces a file that load_registry reads back identically."""
    reg = _reg(
        _row("cit_000001", doi="10.1000/abc", status="manual", notes="test note"),
        _row("cit_000002", status="rejected", notes="manual-reject: supplement"),
    )
    tsv_path = tmp_path / "registry.tsv"
    write_registry(tsv_path, reg, COLUMNS)
    reg2, cols2 = load_registry(tsv_path)
    assert cols2 == COLUMNS
    assert reg2["cit_000001"]["doi"] == "10.1000/abc"
    assert reg2["cit_000002"]["status"] == "rejected"
