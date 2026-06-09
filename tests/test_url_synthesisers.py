"""test_url_synthesisers.py — Pure-Python regex tests for Path B URL synthesisers.

Tests the three new synthesis patterns added in Phase 2.5C:
  - PsyArXiv (.com and .org variants, view_only param, trailing slash)
  - bioRxiv / medRxiv (with v1/v2 version suffixes, query params stripped)
  - eLife (numeric article ID)

All tests are offline — no network, no fixtures, no mocking.

Run:
    pytest tests/test_url_synthesisers.py -v
"""

from __future__ import annotations


import pytest

from hed_metadata_toolkit.citations.enrich_pub_ids import (  # noqa: E402
    _synth_biorxiv_medrxiv,
    _synth_elife,
    _synth_psyarxiv,
    _try_synth,
)


# ---------------------------------------------------------------------------
# PsyArXiv synthesiser
# ---------------------------------------------------------------------------


class TestPsyArXivSynth:
    @pytest.mark.parametrize(
        "url, expected_doi",
        [
            ("https://psyarxiv.com/3x2qh", "10.31234/osf.io/3x2qh"),
            ("https://psyarxiv.org/3x2qh", "10.31234/osf.io/3x2qh"),
            ("http://psyarxiv.com/3x2qh", "10.31234/osf.io/3x2qh"),
            ("https://www.psyarxiv.com/3x2qh", "10.31234/osf.io/3x2qh"),
            ("https://psyarxiv.com/abc123", "10.31234/osf.io/abc123"),
            ("HTTPS://PSYARXIV.COM/3x2qh", "10.31234/osf.io/3x2qh"),
        ],
    )
    def test_positive_cases(self, url: str, expected_doi: str):
        result = _synth_psyarxiv(url)
        assert result == expected_doi, f"URL: {url!r}"

    def test_query_param_view_only(self):
        """view_only= query param: psyarxiv.com/guid?view_only=... captures guid."""
        url = "https://psyarxiv.com/3x2qh?view_only=abc"
        # The psyarxiv regex anchors on the guid group; query params are not in the
        # [a-z0-9]+ group so the guid stops at the '?' boundary.
        result = _synth_psyarxiv(url)
        assert result == "10.31234/osf.io/3x2qh"

    @pytest.mark.parametrize(
        "url",
        [
            "https://arxiv.org/abs/2101.12345",
            "https://biorxiv.org/content/10.1101/123456",
            "https://osf.io/preprints/psyarxiv/3x2qh",
            "https://psyarxiv.com/",  # no guid
            "https://example.com/3x2qh",
        ],
    )
    def test_non_psyarxiv_returns_none(self, url: str):
        assert _synth_psyarxiv(url) is None, f"URL: {url!r}"


# ---------------------------------------------------------------------------
# bioRxiv / medRxiv synthesiser
# ---------------------------------------------------------------------------


class TestBioRxivMedRxivSynth:
    @pytest.mark.parametrize(
        "url, expected_doi",
        [
            # bioRxiv, no version suffix
            ("https://biorxiv.org/content/10.1101/283234", "10.1101/283234"),
            # bioRxiv with v1 suffix
            ("https://biorxiv.org/content/10.1101/283234v1", "10.1101/283234"),
            # bioRxiv with v2 suffix
            (
                "https://www.biorxiv.org/content/10.1101/2021.01.01.123456v2",
                "10.1101/2021.01.01.123456",
            ),
            # medRxiv
            (
                "https://medrxiv.org/content/10.1101/2021.06.15.21259000",
                "10.1101/2021.06.15.21259000",
            ),
            # medRxiv with v3 suffix
            (
                "https://www.medrxiv.org/content/10.1101/2021.06.15.21259000v3",
                "10.1101/2021.06.15.21259000",
            ),
            # http scheme
            ("http://biorxiv.org/content/10.1101/170720", "10.1101/170720"),
            # Uppercase URL (regex is case-insensitive)
            ("HTTPS://BIORXIV.ORG/CONTENT/10.1101/283234", "10.1101/283234"),
        ],
    )
    def test_positive_cases(self, url: str, expected_doi: str):
        result = _synth_biorxiv_medrxiv(url)
        assert result == expected_doi, f"URL: {url!r}"

    def test_query_params_stripped(self):
        """Content DOI is captured up to the query string delimiter."""
        url = "https://biorxiv.org/content/10.1101/283234?utm_source=rss"
        result = _synth_biorxiv_medrxiv(url)
        assert result == "10.1101/283234"

    @pytest.mark.parametrize(
        "url",
        [
            "https://psyarxiv.com/3x2qh",
            "https://arxiv.org/abs/2101.12345",
            "https://biorxiv.org/",  # no DOI path
            "https://biorxiv.org/search/content",  # not a content URL
            "https://nature.com/articles/s41598-021-00001-1",
        ],
    )
    def test_non_matching_returns_none(self, url: str):
        assert _synth_biorxiv_medrxiv(url) is None, f"URL: {url!r}"


# ---------------------------------------------------------------------------
# eLife synthesiser
# ---------------------------------------------------------------------------


class TestELifeSynth:
    @pytest.mark.parametrize(
        "url, expected_doi",
        [
            ("https://elifesciences.org/articles/12345", "10.7554/eLife.12345"),
            ("https://www.elifesciences.org/articles/67890", "10.7554/eLife.67890"),
            ("http://elifesciences.org/articles/99999", "10.7554/eLife.99999"),
            ("HTTPS://ELIFESCIENCES.ORG/ARTICLES/11111", "10.7554/eLife.11111"),
        ],
    )
    def test_positive_cases(self, url: str, expected_doi: str):
        result = _synth_elife(url)
        assert result == expected_doi, f"URL: {url!r}"

    @pytest.mark.parametrize(
        "url",
        [
            "https://psyarxiv.com/3x2qh",
            "https://biorxiv.org/content/10.1101/283234",
            "https://elifesciences.org/",  # no article id
            "https://elifesciences.org/podcasts/12345",  # not /articles/ path
            "https://example.com/articles/12345",
        ],
    )
    def test_non_matching_returns_none(self, url: str):
        assert _synth_elife(url) is None, f"URL: {url!r}"


# ---------------------------------------------------------------------------
# Integration: _try_synth dispatches correctly across all patterns
# ---------------------------------------------------------------------------


class TestTrySynth:
    def test_psyarxiv_com_dispatched(self):
        assert _try_synth("https://psyarxiv.com/3x2qh") == "10.31234/osf.io/3x2qh"

    def test_psyarxiv_org_dispatched(self):
        assert _try_synth("https://psyarxiv.org/3x2qh") == "10.31234/osf.io/3x2qh"

    def test_biorxiv_v1_dispatched(self):
        result = _try_synth("https://biorxiv.org/content/10.1101/283234v1")
        assert result == "10.1101/283234"

    def test_medrxiv_v3_dispatched(self):
        result = _try_synth("https://medrxiv.org/content/10.1101/2021.06.15.21259000v3")
        assert result == "10.1101/2021.06.15.21259000"

    def test_elife_dispatched(self):
        # _try_synth canonicalises to lowercase; eLife DOIs are case-insensitive
        assert (
            _try_synth("https://elifesciences.org/articles/12345")
            == "10.7554/elife.12345"
        )

    def test_existing_nature_synth_still_works(self):
        """Existing citation_normalize patterns must not be broken."""
        result = _try_synth("https://www.nature.com/articles/s41597-022-01407-x")
        assert result == "10.1038/s41597-022-01407-x"

    def test_non_matching_returns_none(self):
        assert _try_synth("https://arxiv.org/abs/2101.12345") is None
        assert _try_synth("https://osf.io/bxvhr") is None  # OSF handled by Path D
        assert _try_synth("https://example.com/paper") is None
