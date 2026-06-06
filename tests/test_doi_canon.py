"""test_doi_canon.py — Tests for the DOI pre-canonicalization step (Phase 2.5C-bis).

Tests _canonicalise_doi for:
  - bioRxiv version suffixes (v1, v2, v1.abstract, v1.full.pdf, v1.full)
  - URL-decoration suffixes (/full, /meta, /abstract)
  - Scientific Data DOI correction (10.1038/sdataYYYYNNN → 10.1038/sdata.YYYY.NNN)
  - No-op pass-through for already-canonical DOIs

All tests are pure-Python — no network, no fixtures.

Run:
    pytest tests/test_doi_canon.py -v
"""

from __future__ import annotations


import pytest

from hed_metadata_toolkit.citations.enrich_pub_ids import _canonicalise_doi  # noqa: E402


class TestCanonicaliseDOI:
    """Required cases from Phase 2.5C-bis spec."""

    @pytest.mark.parametrize("doi, expected", [
        # bioRxiv version suffix
        ("10.1101/2021.07.11.451985v1",          "10.1101/2021.07.11.451985"),
        # bioRxiv .full.pdf: strip .full.pdf first, then v1
        ("10.1101/2021.05.16.444253v1.full.pdf", "10.1101/2021.05.16.444253"),
        # Frontiers /full
        ("10.3389/fnhum.2020.00246/full",         "10.3389/fnhum.2020.00246"),
        # IOP /meta
        ("10.1088/1741-2552/ac69bc/meta",         "10.1088/1741-2552/ac69bc"),
        # Sci Data — year 2016, article 110
        ("10.1038/sdata2016110",                  "10.1038/sdata.2016.110"),
        # Sci Data — year 2014, article 3
        ("10.1038/sdata20143",                    "10.1038/sdata.2014.3"),
        # No-op: already-canonical DOI passes through unchanged
        ("10.1073/pnas.1711571115",               "10.1073/pnas.1711571115"),
    ])
    def test_required_cases(self, doi: str, expected: str):
        assert _canonicalise_doi(doi) == expected, f"Input: {doi!r}"


class TestVersionSuffixes:
    """bioRxiv version variants."""

    @pytest.mark.parametrize("doi, expected", [
        ("10.1101/2022.09.22.509104v1.abstract",  "10.1101/2022.09.22.509104"),
        ("10.1101/2021.01.12.426428v3",            "10.1101/2021.01.12.426428"),
        ("10.1101/2024.07.09.602700v1.full",       "10.1101/2024.07.09.602700"),
        ("10.1101/254961v2",                       "10.1101/254961"),
        ("10.1101/862797v3",                       "10.1101/862797"),
        # Uppercase input → lowercased and stripped
        ("10.1101/283234V1",                       "10.1101/283234"),
    ])
    def test_version_stripped(self, doi: str, expected: str):
        assert _canonicalise_doi(doi) == expected, f"Input: {doi!r}"


class TestDecorationSuffixes:
    """URL-decoration suffix variants."""

    @pytest.mark.parametrize("doi, expected", [
        ("10.3389/fradi.2021.789632/full",   "10.3389/fradi.2021.789632"),
        ("10.3389/fnhum.2020.578119/full",   "10.3389/fnhum.2020.578119"),
        ("10.1234/somejournal/abstract",     "10.1234/somejournal"),
    ])
    def test_suffix_stripped(self, doi: str, expected: str):
        assert _canonicalise_doi(doi) == expected, f"Input: {doi!r}"


class TestSciData:
    """Scientific Data DOI correction."""

    @pytest.mark.parametrize("doi, expected", [
        ("10.1038/sdata2016110",   "10.1038/sdata.2016.110"),
        ("10.1038/sdata20143",     "10.1038/sdata.2014.3"),
        ("10.1038/sdata20151",     "10.1038/sdata.2015.1"),
        # Already-correct Sci Data DOI must not be double-dotted
        ("10.1038/sdata.2016.110", "10.1038/sdata.2016.110"),
    ])
    def test_sdata_correction(self, doi: str, expected: str):
        assert _canonicalise_doi(doi) == expected, f"Input: {doi!r}"


class TestNoOp:
    """Already-canonical DOIs must pass through unchanged."""

    @pytest.mark.parametrize("doi", [
        "10.1073/pnas.1711571115",
        "10.12688/f1000research.12142.2",
        "10.1038/s41598-018-24312-0",
        "10.12688/f1000research.6911.1",
        "10.1016/j.neuroimage.2021.118500",
        "10.1101/2021.07.11.451985",  # already stripped
    ])
    def test_canonical_unchanged(self, doi: str):
        assert _canonicalise_doi(doi) == doi, f"Input: {doi!r}"

    def test_uppercase_lowercased(self):
        # Lowercasing is part of canonicalization but otherwise no change
        assert _canonicalise_doi("10.1073/PNAS.1711571115") == "10.1073/pnas.1711571115"
