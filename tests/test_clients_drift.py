"""
test_clients_drift.py — Offline drift-detection tests for mirrored API clients.

Each test stubs cache_get_or_fetch to return a pinned fixture response
and then asserts the client produces the expected (family, year, title)
triple.  If task-research ships an upstream change that renames a response
field, one of these assertions will fail — reconcile the mirrored copy
before continuing.

Fixtures live under tests/fixtures/<source>/<id>.json.
No network calls are made; all HTTP is bypassed via the stub.

Run:
    pytest tests/test_clients_drift.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


_ROOT = Path(__file__).resolve().parent.parent

_FIXTURES = _ROOT / "tests" / "fixtures"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_fixture(source: str, filename: str) -> dict:
    path = _FIXTURES / source / filename
    return json.loads(path.read_text(encoding="utf-8"))


def _make_cache_stub(fixture: dict):
    """Return a side_effect function that ignores all args and returns fixture."""
    def _stub(*args, **kwargs):
        return fixture
    return _stub


# ---------------------------------------------------------------------------
# Crossref
# ---------------------------------------------------------------------------

class TestCrossrefDrift:
    """Parser extracts (first_author_family, year, title) from a pinned fixture."""

    FIXTURE = "10.1038_s41597-022-01407-x.json"
    PINNED = ("Markiewicz", 2021, "OpenNeuro: An open resource for sharing of neuroimaging data")

    def test_lookup_by_doi_returns_dict(self, tmp_path):
        fixture = _load_fixture("crossref", self.FIXTURE)
        with patch("hed_metadata_toolkit.clients.crossref.cache_get_or_fetch", side_effect=_make_cache_stub(fixture)):
            from hed_metadata_toolkit.clients import crossref
            result = crossref.lookup_by_doi("10.1038/s41597-022-01407-x", tmp_path)
        assert result is not None
        assert isinstance(result, dict)

    def test_first_author_family(self, tmp_path):
        fixture = _load_fixture("crossref", self.FIXTURE)
        with patch("hed_metadata_toolkit.clients.crossref.cache_get_or_fetch", side_effect=_make_cache_stub(fixture)):
            from hed_metadata_toolkit.clients import crossref
            result = crossref.lookup_by_doi("10.1038/s41597-022-01407-x", tmp_path)
        family = result["author"][0]["family"]
        assert family == self.PINNED[0], (
            f"Crossref first_author_family drift: expected {self.PINNED[0]!r}, got {family!r}"
        )

    def test_year(self, tmp_path):
        fixture = _load_fixture("crossref", self.FIXTURE)
        with patch("hed_metadata_toolkit.clients.crossref.cache_get_or_fetch", side_effect=_make_cache_stub(fixture)):
            from hed_metadata_toolkit.clients import crossref
            result = crossref.lookup_by_doi("10.1038/s41597-022-01407-x", tmp_path)
        # Year from 'published' or 'published-print', date-parts[0][0]
        year = (result.get("published") or result.get("published-print", {}))['date-parts'][0][0]
        assert year == self.PINNED[1], (
            f"Crossref year drift: expected {self.PINNED[1]}, got {year}"
        )

    def test_title(self, tmp_path):
        fixture = _load_fixture("crossref", self.FIXTURE)
        with patch("hed_metadata_toolkit.clients.crossref.cache_get_or_fetch", side_effect=_make_cache_stub(fixture)):
            from hed_metadata_toolkit.clients import crossref
            result = crossref.lookup_by_doi("10.1038/s41597-022-01407-x", tmp_path)
        title = result["title"][0]
        assert title == self.PINNED[2], (
            f"Crossref title drift: expected {self.PINNED[2]!r}, got {title!r}"
        )

    def test_source_key_added(self, tmp_path):
        fixture = _load_fixture("crossref", self.FIXTURE)
        with patch("hed_metadata_toolkit.clients.crossref.cache_get_or_fetch", side_effect=_make_cache_stub(fixture)):
            from hed_metadata_toolkit.clients import crossref
            result = crossref.lookup_by_doi("10.1038/s41597-022-01407-x", tmp_path)
        assert result['_source'] == 'crossref'
        assert '_doi' in result

    def test_empty_cache_returns_none(self, tmp_path):
        """lookup_by_doi returns None when cache returns {} (not found)."""
        with patch("hed_metadata_toolkit.clients.crossref.cache_get_or_fetch", side_effect=_make_cache_stub({})):
            from hed_metadata_toolkit.clients import crossref
            result = crossref.lookup_by_doi("10.9999/nonexistent", tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------

class TestOpenAlexDrift:
    """Parser extracts (first_author_family, year, title) from a pinned fixture."""

    FIXTURE = "10.1016_j.neuroimage.2021.118411.json"
    PINNED = ("Whitaker", 2022, "A unified framework for the study of mind, brain, and behavior")

    def test_lookup_by_doi_returns_dict(self, tmp_path):
        fixture = _load_fixture("openalex", self.FIXTURE)
        with patch("hed_metadata_toolkit.clients.openalex.cache_get_or_fetch", side_effect=_make_cache_stub(fixture)):
            from hed_metadata_toolkit.clients import openalex
            result = openalex.lookup_by_doi("10.1016/j.neuroimage.2021.118411", tmp_path)
        assert result is not None
        assert isinstance(result, dict)

    def test_first_author_family(self, tmp_path):
        fixture = _load_fixture("openalex", self.FIXTURE)
        with patch("hed_metadata_toolkit.clients.openalex.cache_get_or_fetch", side_effect=_make_cache_stub(fixture)):
            from hed_metadata_toolkit.clients import openalex
            result = openalex.lookup_by_doi("10.1016/j.neuroimage.2021.118411", tmp_path)
        # OpenAlex uses display_name; last token is the family name convention.
        display_name = result["authorships"][0]["author"]["display_name"]
        family = display_name.rsplit(" ", 1)[-1]
        assert family == self.PINNED[0], (
            f"OpenAlex first_author_family drift: expected {self.PINNED[0]!r}, "
            f"got {family!r} (from display_name={display_name!r})"
        )

    def test_year(self, tmp_path):
        fixture = _load_fixture("openalex", self.FIXTURE)
        with patch("hed_metadata_toolkit.clients.openalex.cache_get_or_fetch", side_effect=_make_cache_stub(fixture)):
            from hed_metadata_toolkit.clients import openalex
            result = openalex.lookup_by_doi("10.1016/j.neuroimage.2021.118411", tmp_path)
        year = result["publication_year"]
        assert year == self.PINNED[1], (
            f"OpenAlex year drift: expected {self.PINNED[1]}, got {year}"
        )

    def test_title(self, tmp_path):
        fixture = _load_fixture("openalex", self.FIXTURE)
        with patch("hed_metadata_toolkit.clients.openalex.cache_get_or_fetch", side_effect=_make_cache_stub(fixture)):
            from hed_metadata_toolkit.clients import openalex
            result = openalex.lookup_by_doi("10.1016/j.neuroimage.2021.118411", tmp_path)
        title = result["title"]
        assert title == self.PINNED[2], (
            f"OpenAlex title drift: expected {self.PINNED[2]!r}, got {title!r}"
        )

    def test_source_key_added(self, tmp_path):
        fixture = _load_fixture("openalex", self.FIXTURE)
        with patch("hed_metadata_toolkit.clients.openalex.cache_get_or_fetch", side_effect=_make_cache_stub(fixture)):
            from hed_metadata_toolkit.clients import openalex
            result = openalex.lookup_by_doi("10.1016/j.neuroimage.2021.118411", tmp_path)
        assert result["_source"] == "openalex"
        assert "_doi" in result

    def test_empty_cache_returns_none(self, tmp_path):
        with patch("hed_metadata_toolkit.clients.openalex.cache_get_or_fetch", side_effect=_make_cache_stub({})):
            from hed_metadata_toolkit.clients import openalex
            result = openalex.lookup_by_doi("10.9999/nonexistent", tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# Europe PMC
# ---------------------------------------------------------------------------

class TestEuropePmcDrift:
    """Parser extracts (first_author_family, year, title) from a pinned fixture."""

    FIXTURE = "pmid_35722095.json"
    PINNED = ("Cohen", 2022, "Neural correlates of cognitive control in the human brain")

    def test_lookup_by_pmid_returns_dict(self, tmp_path):
        fixture = _load_fixture("europepmc", self.FIXTURE)
        with patch("hed_metadata_toolkit.clients.europepmc.cache_get_or_fetch", side_effect=_make_cache_stub(fixture)):
            from hed_metadata_toolkit.clients import europepmc
            result = europepmc.lookup_by_pmid("35722095", tmp_path)
        assert result is not None
        assert isinstance(result, dict)

    def test_first_author_family(self, tmp_path):
        fixture = _load_fixture("europepmc", self.FIXTURE)
        with patch("hed_metadata_toolkit.clients.europepmc.cache_get_or_fetch", side_effect=_make_cache_stub(fixture)):
            from hed_metadata_toolkit.clients import europepmc
            result = europepmc.lookup_by_pmid("35722095", tmp_path)
        family = result["authorList"]["author"][0]["lastName"]
        assert family == self.PINNED[0], (
            f"EuropePMC first_author_family drift: expected {self.PINNED[0]!r}, got {family!r}"
        )

    def test_year(self, tmp_path):
        fixture = _load_fixture("europepmc", self.FIXTURE)
        with patch("hed_metadata_toolkit.clients.europepmc.cache_get_or_fetch", side_effect=_make_cache_stub(fixture)):
            from hed_metadata_toolkit.clients import europepmc
            result = europepmc.lookup_by_pmid("35722095", tmp_path)
        year = int(result["firstPublicationDate"][:4])
        assert year == self.PINNED[1], (
            f"EuropePMC year drift: expected {self.PINNED[1]}, got {year}"
        )

    def test_title(self, tmp_path):
        fixture = _load_fixture("europepmc", self.FIXTURE)
        with patch("hed_metadata_toolkit.clients.europepmc.cache_get_or_fetch", side_effect=_make_cache_stub(fixture)):
            from hed_metadata_toolkit.clients import europepmc
            result = europepmc.lookup_by_pmid("35722095", tmp_path)
        title = result["title"]
        assert title == self.PINNED[2], (
            f"EuropePMC title drift: expected {self.PINNED[2]!r}, got {title!r}"
        )

    def test_source_key_added(self, tmp_path):
        fixture = _load_fixture("europepmc", self.FIXTURE)
        with patch("hed_metadata_toolkit.clients.europepmc.cache_get_or_fetch", side_effect=_make_cache_stub(fixture)):
            from hed_metadata_toolkit.clients import europepmc
            result = europepmc.lookup_by_pmid("35722095", tmp_path)
        assert result["_source"] == "europepmc"
        assert "_doi" in result

    def test_empty_cache_returns_none(self, tmp_path):
        with patch("hed_metadata_toolkit.clients.europepmc.cache_get_or_fetch", side_effect=_make_cache_stub({})):
            from hed_metadata_toolkit.clients import europepmc
            result = europepmc.lookup_by_pmid("99999999", tmp_path)
        assert result is None

    def test_doi_populated(self, tmp_path):
        """_doi convenience key is populated from the EuropePMC 'doi' field."""
        fixture = _load_fixture("europepmc", self.FIXTURE)
        with patch("hed_metadata_toolkit.clients.europepmc.cache_get_or_fetch", side_effect=_make_cache_stub(fixture)):
            from hed_metadata_toolkit.clients import europepmc
            result = europepmc.lookup_by_pmid("35722095", tmp_path)
        assert result["_doi"] == "10.1016/j.neuron.2022.05.021"
