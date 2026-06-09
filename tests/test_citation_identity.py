"""
test_identity.py — Parametrized tests for identity.py.

Run:
    pytest outputs/literature_search/test_identity.py -v

All inputs are literals.  No network, no fixtures, no external dependencies.
Tests cover the 15 fixture rows specified in
task_literature_search_phase1_instructions.md §Sub-phase 1.2.

The three test functions (test_canonical, test_pub_id, test_pdf_filename)
each exercise all 15 rows.  Row 15 is identical to Row 1 and asserts
determinism (DOI-discovered-later round-trip invariant).
"""

import hashlib
import re

import pytest

from hed_metadata_toolkit.citation_identity import (
    build_canonical_string,
    build_pdf_filename,
    build_pub_id,
)

# ---------------------------------------------------------------------------
# Fixture table  (15 rows × 3 columns)
# ---------------------------------------------------------------------------
# Each tuple: (first_author_family, year, title, notes)
# 'notes' is for human readability only; not used in assertions.

FIXTURES = [
    # 1 — baseline
    (
        "Badre",
        2012,
        "Cognitive control, hierarchy, and the rostro-caudal organization of the frontal lobes",
        "baseline",
    ),
    # 2 — pre-DOI era
    (
        "Stroop",
        1935,
        "Studies of interference in serial verbal reactions",
        "pre-DOI era",
    ),
    # 3
    (
        "Eriksen",
        1974,
        "Effects of noise letters upon the identification of a target letter in a nonsearch task",
        "noise letters",
    ),
    # 4 — short title
    (
        "Posner",
        1980,
        "Orienting of attention",
        "short title",
    ),
    # 5 — leading article
    (
        "Miller",
        1956,
        "The magical number seven, plus or minus two",
        "leading article The",
    ),
    # 6 — accented family name
    (
        "Schönberg",
        2009,
        "A test of diacritics",
        "accented family name",
    ),
    # 7 — apostrophe in family name
    (
        "O'Keefe",
        1971,
        "The hippocampus as a spatial map",
        "apostrophe in family name",
    ),
    # 8 — compound particle
    (
        "van der Berg",
        2020,
        "Compound particle test",
        "compound particle",
    ),
    # 9
    (
        "de Fockert",
        2001,
        "The role of working memory in visual selective attention",
        "de Fockert",
    ),
    # 10 — preserve acronyms: fMRI, EEG, ADHD
    (
        "Wagenmakers",
        2016,
        "fMRI and EEG study of cognitive control: an ADHD cohort",
        "preserve fMRI EEG ADHD in filename",
    ),
    # 11 — very long title (hits both 100-char truncations)
    (
        "Smith",
        2015,
        "A" * 400,
        "very long title",
    ),
    # 12 — no first author
    (
        None,
        2010,
        "Anonymous report",
        "no first author",
    ),
    # 13 — no year
    (
        "Brown",
        None,
        "Undated paper",
        "no year",
    ),
    # 14 — non-Latin title folds empty
    (
        "Li",
        2018,
        "工作记忆",
        "non-Latin title folds empty",
    ),
    # 15 — identical to Row 1: determinism / DOI-discovered-later round-trip
    (
        "Badre",
        2012,
        "Cognitive control, hierarchy, and the rostro-caudal organization of the frontal lobes",
        "determinism repeat of row 1",
    ),
]

# Parametrize IDs for readable output.
_IDS = [f"row{i + 1}_{row[3].replace(' ', '_')[:30]}" for i, row in enumerate(FIXTURES)]


# ---------------------------------------------------------------------------
# Helper: the exact SHA-1 hash expected for each row.
# We compute it from build_canonical_string so we don't hand-code 15 hashes,
# but we also cross-check the structural properties independently.
# ---------------------------------------------------------------------------


def _expected_hash8(family, year, title) -> str:
    cs = build_canonical_string(family, year, title)
    return hashlib.sha1(cs.encode("utf-8")).hexdigest()[:8]


# ---------------------------------------------------------------------------
# Tests: build_canonical_string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("family,year,title,notes", FIXTURES, ids=_IDS)
def test_canonical(family, year, title, notes):
    cs = build_canonical_string(family, year, title)

    # Always returns a non-empty string.
    assert isinstance(cs, str)
    assert len(cs) > 0

    # Never exceeds 100 characters.
    assert len(cs) <= 100, f"canonical too long ({len(cs)}): {cs!r}"

    # Contains only lowercase alphanumeric characters.
    assert re.fullmatch(r"[a-z0-9]+", cs), f"canonical contains forbidden chars: {cs!r}"

    # ---- Row-specific assertions ----

    if notes == "baseline":
        # §11.7 example: full canonical spelled out
        expected = (
            "badre2012cognitivecontrolhierarchyandtherostrocaudal"
            "organizationofthefrontallobes"
        )
        assert cs == expected, (
            f"Row 1 canonical mismatch:\n  got:      {cs!r}\n  expected: {expected!r}"
        )

    elif notes == "pre-DOI era":
        assert cs.startswith("stroop1935")

    elif notes == "accented family name":
        # Schönberg → schonberg
        assert cs.startswith("schonberg2009")

    elif notes == "apostrophe in family name":
        # O'Keefe → okeefe
        assert cs.startswith("okeefe1971")

    elif notes == "compound particle":
        # van der Berg → vanderberg
        assert cs.startswith("vanderberg2020")

    elif notes == "no first author":
        assert cs.startswith("anonymous")

    elif notes == "no year":
        # year=None → "0000"
        assert "0000" in cs
        assert cs.startswith("brown0000")

    elif notes == "non-Latin title folds empty":
        # Title 工作记忆 folds to empty → "untitled"
        assert cs.endswith("untitled")
        assert cs == "li2018untitled"

    elif notes == "very long title":
        # Hits the 100-char truncation
        assert len(cs) == 100
        assert cs.startswith("smith2015")

    elif notes == "determinism repeat of row 1":
        # Must be byte-for-byte identical to row 1.
        row1_cs = build_canonical_string(
            "Badre",
            2012,
            "Cognitive control, hierarchy, and the rostro-caudal organization of the frontal lobes",
        )
        assert cs == row1_cs


# ---------------------------------------------------------------------------
# Tests: build_pub_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("family,year,title,notes", FIXTURES, ids=_IDS)
def test_pub_id(family, year, title, notes):
    pid = build_pub_id(family, year, title)

    # Structural: "pub_" + 8 hex chars
    assert re.fullmatch(r"pub_[0-9a-f]{8}", pid), f"pub_id has wrong shape: {pid!r}"

    # Hash matches canonical_string.
    h8 = _expected_hash8(family, year, title)
    assert pid == f"pub_{h8}", f"pub_id hash mismatch: {pid!r} != 'pub_{h8}'"

    # ---- Row-specific assertions ----

    if notes == "baseline":
        cs = build_canonical_string(family, year, title)
        expected_hash = hashlib.sha1(cs.encode()).hexdigest()[:8]
        assert pid == f"pub_{expected_hash}"

    elif notes == "determinism repeat of row 1":
        row1_pid = build_pub_id(
            "Badre",
            2012,
            "Cognitive control, hierarchy, and the rostro-caudal organization of the frontal lobes",
        )
        assert pid == row1_pid, f"Determinism failed: row1={row1_pid!r}, row15={pid!r}"


# ---------------------------------------------------------------------------
# Tests: build_pdf_filename
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("family,year,title,notes", FIXTURES, ids=_IDS)
def test_pdf_filename(family, year, title, notes):
    fn = build_pdf_filename(family, year, title)

    # Must end in .pdf
    assert fn.endswith(".pdf"), f"filename does not end in .pdf: {fn!r}"

    # Shape: <LastName>_<Year>_<CamelCaseTitle>_<hash8>.pdf
    # Split off the .pdf and split on _ from the right.
    stem = fn[:-4]  # remove .pdf
    parts = stem.rsplit("_", 3)
    assert len(parts) == 4, (
        f"filename does not have 4 underscore-separated parts: {fn!r}"
    )
    last_part, year_part, title_part, hash_part = parts

    # hash_part is 8 hex chars matching pub_id suffix.
    assert re.fullmatch(r"[0-9a-f]{8}", hash_part), (
        f"filename hash part wrong: {hash_part!r}"
    )
    assert hash_part == _expected_hash8(family, year, title), (
        "filename hash does not match pub_id hash"
    )

    # last_part contains only [A-Za-z0-9]
    assert re.fullmatch(r"[A-Za-z0-9]+", last_part), (
        f"LastName part contains forbidden chars: {last_part!r}"
    )

    # title_part contains only [A-Za-z0-9]
    assert re.fullmatch(r"[A-Za-z0-9]+", title_part), (
        f"CamelCaseTitle part contains forbidden chars: {title_part!r}"
    )

    # CamelCaseTitle is at most 100 chars.
    assert len(title_part) <= 100, (
        f"CamelCaseTitle exceeds 100 chars ({len(title_part)}): {title_part!r}"
    )

    # ---- Row-specific assertions ----

    if notes == "baseline":
        assert last_part == "Badre"
        assert year_part == "2012"
        # Spot-check a few tokens in the title
        assert "CognitiveControl" in title_part
        assert (
            "Rostro" in title_part
            or "Rostrocaudal" in title_part.lower()
            or "RostroCaudal" in title_part
        )

    elif notes == "pre-DOI era":
        assert last_part == "Stroop"
        assert year_part == "1935"

    elif notes == "accented family name":
        # Schönberg → Schonberg
        assert last_part == "Schonberg"
        assert year_part == "2009"

    elif notes == "apostrophe in family name":
        # O'Keefe → OKeefe
        assert last_part == "OKeefe"
        assert year_part == "1971"

    elif notes == "compound particle":
        # van der Berg → VanDerBerg
        assert last_part == "VanDerBerg"
        assert year_part == "2020"

    elif notes == "de Fockert":
        assert last_part == "DeFockert"
        assert year_part == "2001"

    elif notes == "preserve fMRI EEG ADHD in filename":
        # Acronyms must be preserved in their original case
        assert "fMRI" in title_part, f"fMRI not preserved: {title_part!r}"
        assert "EEG" in title_part, f"EEG not preserved: {title_part!r}"
        assert "ADHD" in title_part, f"ADHD not preserved: {title_part!r}"

    elif notes == "very long title":
        # CamelCaseTitle must be at most 100 chars (checked above generically)
        assert len(title_part) == 100  # exactly hits the budget after hard-truncation

    elif notes == "no first author":
        # family=None → 'Anonymous'
        assert last_part == "Anonymous"
        assert year_part == "2010"

    elif notes == "no year":
        # year=None → 'nodate'
        assert year_part == "nodate"

    elif notes == "non-Latin title folds empty":
        # Non-Latin → 'UntitledNonLatin'
        assert title_part == "UntitledNonLatin", (
            f"expected 'UntitledNonLatin', got {title_part!r}"
        )

    elif notes == "determinism repeat of row 1":
        row1_fn = build_pdf_filename(
            "Badre",
            2012,
            "Cognitive control, hierarchy, and the rostro-caudal organization of the frontal lobes",
        )
        assert fn == row1_fn, f"Determinism failed: row1={row1_fn!r}, row15={fn!r}"
