"""
test_citation_identity.py — Drift detector for src/citation_identity.py.

src/citation_identity.py is a synced copy of task-research's identity.py.
The two copies must compute the same pub_id for the same input or the
cross-repo bridge to task-research/publications.json silently breaks.

These tests pin three known (family, year, title) -> pub_id triples.
The expected pub_id values were computed from the upstream identity.py
on 2026-05-01 and copied here as literals.  If a future edit to either
copy changes any of the helper functions in a way that affects pub_id,
one of these assertions will fail and the diff between the two copies
needs to be reconciled.

The triples are chosen to exercise:
  - a short title (Posner 1980)
  - a long title (Eriksen 1974)
  - a pre-DOI-era paper to stress the "no DOI required for pub_id"
    property (Stroop 1935)

Run:
    pytest tests/test_citation_identity.py -v

No network, no fixtures, no external dependencies.
"""

from __future__ import annotations


import pytest

from hed_metadata_toolkit.citation_identity import build_canonical_string, build_pub_id  # noqa: E402


# ---------------------------------------------------------------------------
# Pinned triples
# ---------------------------------------------------------------------------
# (family, year, title, expected_pub_id, expected_canonical_prefix)
#
# expected_pub_id was generated on 2026-05-01 by running
# task-research/Claude-research/code/literature_search/identity.py
# build_pub_id() on the inputs.  expected_canonical_prefix is the first
# 30 chars of the canonical string, which is enough to detect helper
# drift in _canonical_lastname, _canonical_year, or _canonical_title
# without pinning the entire 100-char string.

PINNED = [
    (
        "Stroop",
        1935,
        "Studies of interference in serial verbal reactions",
        "pub_bf64f49f",
        "stroop1935studiesofinterfere",
    ),
    (
        "Eriksen",
        1974,
        "Effects of noise letters upon the identification of a target letter "
        "in a nonsearch task",
        "pub_7e2a3692",
        "eriksen1974effectsofnoiselet",
    ),
    (
        "Posner",
        1980,
        "Orienting of attention",
        "pub_da038724",
        "posner1980orientingofattention",
    ),
]


@pytest.mark.parametrize(
    "family, year, title, expected_pub_id, _canonical_prefix",
    PINNED,
    ids=[t[0].lower() + str(t[1]) for t in PINNED],
)
def test_pub_id_matches_upstream(
    family: str,
    year: int,
    title: str,
    expected_pub_id: str,
    _canonical_prefix: str,
) -> None:
    """The computed pub_id must equal the literal pinned upstream value.

    Failure mode: someone has edited src/citation_identity.py (or the
    upstream identity.py in task-research) in a way that changes the
    canonical-string assembly or the SHA-1 prefix.  Reconcile the two
    copies before adding any rows to citation_registry.tsv with the
    new (drifted) pub_ids.
    """
    actual = build_pub_id(family, year, title)
    assert actual == expected_pub_id, (
        f"pub_id drift detected for ({family!r}, {year}, "
        f"{title[:40]!r}...): "
        f"expected {expected_pub_id}, got {actual}.  "
        f"src/citation_identity.py has diverged from "
        f"task-research/Claude-research/code/literature_search/identity.py."
    )


@pytest.mark.parametrize(
    "family, year, title, _expected_pub_id, expected_canonical_prefix",
    PINNED,
    ids=[t[0].lower() + str(t[1]) for t in PINNED],
)
def test_canonical_string_matches_upstream(
    family: str,
    year: int,
    title: str,
    _expected_pub_id: str,
    expected_canonical_prefix: str,
) -> None:
    """The canonical string must start with the pinned prefix.

    The canonical string is the SHA-1 input.  Pinning a 30-char prefix
    catches drift in _canonical_lastname, _canonical_year, or
    _canonical_title (any of which would also drift the pub_id), but
    surfaces it with a more diagnostic message: you see *which* part
    of the canonical-string assembly broke, not just that the hash
    changed.
    """
    actual = build_canonical_string(family, year, title)
    assert actual.startswith(expected_canonical_prefix), (
        f"canonical_string drift for ({family!r}, {year}, ...): "
        f"expected prefix {expected_canonical_prefix!r}, "
        f"got {actual[:30]!r}."
    )


def test_determinism() -> None:
    """build_pub_id is deterministic: same inputs always give same output."""
    a = build_pub_id(
        "Stroop", 1935, "Studies of interference in serial verbal reactions"
    )
    b = build_pub_id(
        "Stroop", 1935, "Studies of interference in serial verbal reactions"
    )
    assert a == b


def test_none_inputs_are_handled() -> None:
    """None inputs collapse to deterministic placeholder values, not errors.

    Matches upstream behaviour: missing family -> 'anonymous',
    missing year -> '0000', missing title -> 'untitled'.  The
    function returns a real pub_id rather than raising.
    """
    pid = build_pub_id(None, None, None)
    assert pid.startswith("pub_")
    assert len(pid) == 12  # "pub_" + 8 hex chars
