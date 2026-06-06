"""
test_osf_client.py — Tests for src/clients/osf.py.

All tests run offline against pinned fixture dicts (no HTTP).
cache_get_or_fetch is stubbed so no disk I/O occurs either.

Five fixture cases:
  1. nodes type with description containing a DOI (bxvhr shape).
  2. nodes type with no description-DOI (8pg7x shape).
  3. preprints type with a populated attrs.doi.
  4. files type pointing at a parent node.
  5. 401 Unauthorized / private project (cache returns {}).

Plus dedicated is_osf_project_doi tests pinning the safety-critical rule
from .status/phase2_5_thinking_2026-05-06.md §2.1.

Run:
    pytest tests/test_osf_client.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


_ROOT = Path(__file__).resolve().parent.parent

_FIXTURES = _ROOT / "tests" / "fixtures" / "osf"


def _load(filename: str) -> dict:
    return json.loads((_FIXTURES / filename).read_text(encoding="utf-8"))


def _stub(fixture: dict):
    def _fn(*args, **kwargs):
        return fixture
    return _fn


# ---------------------------------------------------------------------------
# is_osf_project_doi — safety-critical filter rule
# ---------------------------------------------------------------------------

class TestIsOsfProjectDoi:
    """Pins the OSF project DOI detection rule from thinking doc §2.1."""

    def setup_method(self):
        from hed_metadata_toolkit.clients.osf import is_osf_project_doi
        self.fn = is_osf_project_doi

    def test_uppercase_prefix_true(self):
        assert self.fn("10.17605/OSF.IO/YCQGD") is True

    def test_lowercase_prefix_true(self):
        assert self.fn("10.17605/osf.io/ycqgd") is True

    def test_mixed_case_true(self):
        assert self.fn("10.17605/Osf.Io/AbCdE") is True

    def test_psyarxiv_false(self):
        assert self.fn("10.31234/osf.io/dzrkq") is False

    def test_osf_preprints_false(self):
        assert self.fn("10.31219/osf.io/j5v9b") is False

    def test_nature_doi_false(self):
        assert self.fn("10.1038/s41597-023-02664-4") is False

    def test_empty_string_false(self):
        assert self.fn("") is False

    def test_plain_text_false(self):
        assert self.fn("not-a-doi") is False


# ---------------------------------------------------------------------------
# lookup_guid
# ---------------------------------------------------------------------------

class TestLookupGuid:

    def test_returns_fixture_dict(self, tmp_path):
        guid_response = {
            "data": {
                "relationships": {
                    "referent": {
                        "data": {"id": "bxvhr", "type": "nodes"},
                        "links": {"related": {"href": "https://api.osf.io/v2/nodes/bxvhr/"}}
                    }
                },
                "attributes": {},
                "type": "guids",
                "id": "bxvhr"
            }
        }
        with patch("hed_metadata_toolkit.clients.osf.cache_get_or_fetch", side_effect=_stub(guid_response)):
            from hed_metadata_toolkit.clients.osf import lookup_guid
            result = lookup_guid("bxvhr", cache_dir=tmp_path)
        assert result["data"]["relationships"]["referent"]["data"]["type"] == "nodes"
        assert result["data"]["relationships"]["referent"]["data"]["id"] == "bxvhr"

    def test_private_project_returns_empty(self, tmp_path):
        """401 private project → cache returns {} → lookup_guid returns {}."""
        with patch("hed_metadata_toolkit.clients.osf.cache_get_or_fetch", side_effect=_stub({})):
            from hed_metadata_toolkit.clients.osf import lookup_guid
            result = lookup_guid("er5u7", cache_dir=tmp_path)
        assert result == {}

    def test_not_found_returns_empty(self, tmp_path):
        with patch("hed_metadata_toolkit.clients.osf.cache_get_or_fetch", side_effect=_stub({})):
            from hed_metadata_toolkit.clients.osf import lookup_guid
            result = lookup_guid("zzzzz", cache_dir=tmp_path)
        assert result == {}


# ---------------------------------------------------------------------------
# extract_publication_metadata — fixture case 1: nodes with description DOI
# ---------------------------------------------------------------------------

class TestExtractMetadataNodesWithDoi:
    """bxvhr: nodes type, description contains a real DOI and an OSF project DOI."""

    def setup_method(self):
        from hed_metadata_toolkit.clients.osf import extract_publication_metadata
        self._fn = extract_publication_metadata
        self._fixture = _load("nodes_bxvhr.json")

    def test_type_is_nodes(self):
        meta = self._fn(self._fixture)
        assert meta["type"] == "nodes"

    def test_not_a_publication(self):
        meta = self._fn(self._fixture)
        assert meta["is_publication"] is False

    def test_publication_doi_is_none(self):
        meta = self._fn(self._fixture)
        assert meta["publication_doi"] is None

    def test_title_extracted(self):
        meta = self._fn(self._fixture)
        assert meta["title"] == "Network Neuroscience dataset - Huskey et al."

    def test_description_doi_candidates_filtered(self):
        """OSF project DOI filtered out; journal DOI retained."""
        meta = self._fn(self._fixture)
        candidates = meta["description_doi_candidates"]
        assert "10.1093/joc/jqac025" in candidates
        # OSF project DOI must be absent
        for doi in candidates:
            from hed_metadata_toolkit.clients.osf import is_osf_project_doi
            assert not is_osf_project_doi(doi), f"Project DOI leaked: {doi}"

    def test_description_excerpt_populated(self):
        meta = self._fn(self._fixture)
        assert meta["description_excerpt"] is not None
        assert len(meta["description_excerpt"]) <= 600


# ---------------------------------------------------------------------------
# extract_publication_metadata — fixture case 2: nodes without description DOI
# ---------------------------------------------------------------------------

class TestExtractMetadataNodesNoDoi:
    """8pg7x: nodes type, description has no DOIs."""

    def setup_method(self):
        from hed_metadata_toolkit.clients.osf import extract_publication_metadata
        self._fn = extract_publication_metadata
        self._fixture = _load("nodes_8pg7x.json")

    def test_type_is_nodes(self):
        meta = self._fn(self._fixture)
        assert meta["type"] == "nodes"

    def test_not_a_publication(self):
        meta = self._fn(self._fixture)
        assert meta["is_publication"] is False

    def test_no_description_doi_candidates(self):
        meta = self._fn(self._fixture)
        assert meta["description_doi_candidates"] == []

    def test_title_extracted(self):
        meta = self._fn(self._fixture)
        assert meta["title"] == "Resting state fMRI dataset"


# ---------------------------------------------------------------------------
# extract_publication_metadata — fixture case 3: preprints with attrs.doi
# ---------------------------------------------------------------------------

class TestExtractMetadataPreprint:
    """j5v9b: preprints type with a populated doi attribute."""

    def setup_method(self):
        from hed_metadata_toolkit.clients.osf import extract_publication_metadata
        self._fn = extract_publication_metadata
        self._fixture = _load("preprints_j5v9b.json")

    def test_type_is_preprints(self):
        meta = self._fn(self._fixture)
        assert meta["type"] == "preprints"

    def test_is_publication_true(self):
        meta = self._fn(self._fixture)
        assert meta["is_publication"] is True

    def test_publication_doi_populated(self):
        meta = self._fn(self._fixture)
        assert meta["publication_doi"] == "10.31219/osf.io/j5v9b"

    def test_publication_doi_not_osf_project_doi(self):
        from hed_metadata_toolkit.clients.osf import is_osf_project_doi
        meta = self._fn(self._fixture)
        assert not is_osf_project_doi(meta["publication_doi"])


# ---------------------------------------------------------------------------
# extract_publication_metadata — fixture case 4: files type
# ---------------------------------------------------------------------------

class TestExtractMetadataFiles:
    """files type: not a publication, has no description-DOIs."""

    def setup_method(self):
        from hed_metadata_toolkit.clients.osf import extract_publication_metadata
        self._fn = extract_publication_metadata
        self._fixture = _load("files_abc123.json")

    def test_type_is_files(self):
        meta = self._fn(self._fixture)
        assert meta["type"] == "files"

    def test_not_a_publication(self):
        meta = self._fn(self._fixture)
        assert meta["is_publication"] is False

    def test_publication_doi_is_none(self):
        meta = self._fn(self._fixture)
        assert meta["publication_doi"] is None

    def test_files_has_relationships_to_parent(self):
        """Parent node id accessible in the fixture for resolver follow-up."""
        data = self._fixture["data"]
        parent_type = data["relationships"]["target"]["data"]["type"]
        parent_id = data["relationships"]["target"]["data"]["id"]
        assert parent_type == "nodes"
        assert parent_id == "bxvhr"


# ---------------------------------------------------------------------------
# extract_publication_metadata — fixture case 5: empty dict (private / 404)
# ---------------------------------------------------------------------------

class TestExtractMetadataEmpty:
    """Empty dict (private project or 404) → all-None/False/empty result."""

    def setup_method(self):
        from hed_metadata_toolkit.clients.osf import extract_publication_metadata
        self._fn = extract_publication_metadata

    def test_type_none(self):
        meta = self._fn({})
        assert meta["type"] is None

    def test_is_publication_false(self):
        meta = self._fn({})
        assert meta["is_publication"] is False

    def test_publication_doi_none(self):
        meta = self._fn({})
        assert meta["publication_doi"] is None

    def test_title_none(self):
        meta = self._fn({})
        assert meta["title"] is None

    def test_contributor_families_empty(self):
        meta = self._fn({})
        assert meta["contributor_families"] == []

    def test_description_excerpt_none(self):
        meta = self._fn({})
        assert meta["description_excerpt"] is None

    def test_description_doi_candidates_empty(self):
        meta = self._fn({})
        assert meta["description_doi_candidates"] == []
