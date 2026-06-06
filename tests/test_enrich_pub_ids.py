"""test_enrich_pub_ids.py — Fixture-driven tests for enrich_pub_ids.

Covers all ~17+ cases specified in Session 2.5C:
  - Pass 1 (offline): manual metadata → pub_id computed, no HTTP calls
  - Path A: Crossref happy path, Crossref-miss→OpenAlex, is-preprint-of chase,
            has-version chase, sanity-check failure, OpenAlex fallback,
            preprint-only-no-chase
  - Path B: PsyArXiv, bioRxiv (with chase), eLife
  - Path C: PMID URL → Europe PMC → DOI → Path A
  - Path D: OSF preprint resolved, OSF node stays needs_review,
            OSF 401 graceful, OSF files→parent recursion
  - Terminal-status rows skipped at top
  - Idempotency: two runs → byte-identical registry
  - Truncation regression: 200+-row round-trip via write_registry

No network calls; all API clients are mocked.

Run:
    pytest tests/test_enrich_pub_ids.py -v
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


from hed_metadata_toolkit.citations.enrich_pub_ids import (  # noqa: E402
    _extract_journal_doi_from_openalex,
    _extract_relation_doi,
    _parse_osf_url,
    _process_pass1,
    _process_pass2,
    _sanity_check,
    _title_token_overlap,
    _try_synth,
    load_registry,
    write_registry,
)
from hed_metadata_toolkit.citation_identity import build_pub_id  # noqa: E402

TODAY = "2026-05-06"
CACHE = Path("/fake/cache")

COLUMNS = [
    "citation_id", "doi", "url", "source_link", "pub_id",
    "first_author_family", "year", "title",
    "status", "metadata_source", "verified_on", "notes",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(
    cit_id: str,
    doi: str = "",
    url: str = "",
    status: str = "auto",
    pub_id: str = "",
    family: str = "",
    year: str = "",
    title: str = "",
    metadata_source: str = "",
    notes: str = "",
    verified_on: str = "",
) -> dict:
    return {
        "citation_id": cit_id,
        "doi": doi,
        "url": url,
        "source_link": "",
        "pub_id": pub_id,
        "first_author_family": family,
        "year": year,
        "title": title,
        "status": status,
        "metadata_source": metadata_source,
        "verified_on": verified_on,
        "notes": notes,
    }


def _make_stats() -> dict[str, list]:
    return {
        "pass1_resolved": [],
        "path_a_direct": [],
        "path_a_is_preprint_of": [],
        "path_a_has_version": [],
        "path_a_openalex": [],
        "path_a_preprint_only": [],
        "path_b": [],
        "path_c": [],
        "path_d_preprint": [],
        "path_d_node": [],
        "skipped_already": [],
        "skipped_terminal": [],
        "still_pending": [],
    }


def _run_pass2(row: dict, paths: str = "ABCD", limit: int | None = None):
    registry = {row["citation_id"]: row}
    stats = _make_stats()
    warnings: list[str] = []
    _process_pass2(
        registry, TODAY, set(paths), CACHE, limit, warnings, stats
    )
    return registry, stats, warnings


# ---------------------------------------------------------------------------
# Crossref fixture data
# ---------------------------------------------------------------------------

def _cr_journal(
    doi: str = "10.1016/j.neuroimage.2022.100001",
    family: str = "Smith",
    year: int = 2022,
    title: str = "A neuroimaging study",
) -> dict:
    return {
        "title": [title],
        "author": [{"sequence": "first", "family": family, "given": "John"}],
        "published": {"date-parts": [[year, 3, 15]]},
        "relation": {},
        "DOI": doi,
        "_source": "crossref",
        "_doi": doi,
        "_fetched_on": "",
    }


def _cr_preprint_is_preprint_of(
    preprint_doi: str = "10.1101/2021.06.01.234567",
    journal_doi: str = "10.1016/j.neuro.2022.100001",
    family: str = "Jones",
    title: str = "Memory study preprint",
) -> dict:
    return {
        "title": [title],
        "author": [{"sequence": "first", "family": family, "given": "Alice"}],
        "published": {"date-parts": [[2021, 6, 1]]},
        "relation": {
            "is-preprint-of": [{"id-type": "doi", "id": journal_doi}]
        },
        "DOI": preprint_doi,
        "_source": "crossref",
        "_doi": preprint_doi,
        "_fetched_on": "",
    }


def _cr_preprint_has_version(
    preprint_doi: str = "10.31234/osf.io/3x2qh",
    journal_doi: str = "10.1016/j.neuroimage.2022.119567",
    family: str = "Lee",
    title: str = "Cognitive flexibility study",
) -> dict:
    return {
        "title": [title],
        "author": [{"sequence": "first", "family": family, "given": "Kevin"}],
        "published": {"date-parts": [[2020, 9, 15]]},
        "relation": {
            "has-version": [{"id-type": "doi", "id": journal_doi}]
        },
        "DOI": preprint_doi,
        "_source": "crossref",
        "_doi": preprint_doi,
        "_fetched_on": "",
    }


def _cr_preprint_no_relation(
    preprint_doi: str = "10.1101/2021.03.05.999999",
    family: str = "Chen",
    title: str = "Brain connectivity in aging",
) -> dict:
    return {
        "title": [title],
        "author": [{"sequence": "first", "family": family, "given": "Wei"}],
        "published": {"date-parts": [[2021, 3, 5]]},
        "relation": {},
        "DOI": preprint_doi,
        "_source": "crossref",
        "_doi": preprint_doi,
        "_fetched_on": "",
    }


# ---------------------------------------------------------------------------
# OpenAlex fixture data
# ---------------------------------------------------------------------------

def _oa_journal(
    doi: str = "10.1016/j.neuroimage.2022.100001",
    family_display: str = "John Smith",
    year: int = 2022,
    title: str = "A neuroimaging study",
) -> dict:
    return {
        "title": title,
        "authorships": [
            {"author_position": "first", "author": {"display_name": family_display}}
        ],
        "publication_year": year,
        "doi": f"https://doi.org/{doi}",
        "locations": [
            {
                "version": "publishedVersion",
                "source": {"type": "journal", "display_name": "NeuroImage"},
                "landing_page_url": f"https://doi.org/{doi}",
            }
        ],
        "_source": "openalex",
        "_doi": doi,
    }


def _oa_biorxiv_with_journal(
    preprint_doi: str = "10.1101/2021.03.05.999999",
    journal_doi: str = "10.1016/j.neuroimage.2022.119001",
    family_display: str = "Wei Chen",
    title: str = "Brain connectivity in aging",
) -> dict:
    return {
        "title": title,
        "authorships": [
            {"author_position": "first", "author": {"display_name": family_display}}
        ],
        "publication_year": 2021,
        "doi": f"https://doi.org/{preprint_doi}",
        "locations": [
            {
                "version": "submittedVersion",
                "source": {"type": "repository", "display_name": "bioRxiv"},
                "landing_page_url": f"https://doi.org/{preprint_doi}",
            },
            {
                "version": "publishedVersion",
                "source": {"type": "journal", "display_name": "NeuroImage"},
                "landing_page_url": f"https://doi.org/{journal_doi}",
            },
        ],
        "_source": "openalex",
        "_doi": preprint_doi,
    }


def _oa_preprint_no_journal(
    doi: str = "10.1101/2021.03.05.888888",
    family_display: str = "Wei Chen",
    title: str = "A preprint with no journal",
) -> dict:
    return {
        "title": title,
        "authorships": [
            {"author_position": "first", "author": {"display_name": family_display}}
        ],
        "publication_year": 2021,
        "doi": f"https://doi.org/{doi}",
        "locations": [
            {
                "version": "submittedVersion",
                "source": {"type": "repository"},
                "landing_page_url": f"https://doi.org/{doi}",
            }
        ],
        "_source": "openalex",
        "_doi": doi,
    }


# ---------------------------------------------------------------------------
# Europe PMC fixture
# ---------------------------------------------------------------------------

def _epmc_with_doi(
    pmid: str = "12345678",
    doi: str = "10.1016/j.neuroimage.2018.10.002",
    family: str = "Adams",
    title: str = "An fMRI paradigm study",
    year: int = 2019,
) -> dict:
    return {
        "pmid": pmid,
        "doi": doi,
        "title": title,
        "authorList": {"author": [{"lastName": family, "firstName": "R"}]},
        "pubYear": str(year),
        "_source": "europepmc",
        "_doi": doi,
    }


# ---------------------------------------------------------------------------
# OSF fixture data
# ---------------------------------------------------------------------------

def _osf_guid_resp(obj_type: str, obj_id: str) -> dict:
    return {
        "data": {
            "type": "guids",
            "id": obj_id,
            "relationships": {
                "referent": {"data": {"type": obj_type, "id": obj_id}}
            },
        }
    }


def _osf_preprint_resp(
    guid: str = "3x2qh",
    doi: str = "10.31234/osf.io/3x2qh",
    title: str = "A cognitive flexibility preprint",
) -> dict:
    return {
        "data": {
            "type": "preprints",
            "id": guid,
            "attributes": {"doi": doi, "title": title, "description": ""},
        }
    }


def _osf_node_resp(guid: str = "bxvhr", title: str = "Dataset node") -> dict:
    return {
        "data": {
            "type": "nodes",
            "id": guid,
            "attributes": {
                "title": title,
                "description": "",
                "tags": [],
            },
        }
    }


def _osf_file_resp(file_id: str = "fileabc", parent_node_id: str = "bxvhr") -> dict:
    return {
        "data": {
            "type": "files",
            "id": file_id,
            "attributes": {},
            "relationships": {
                "node": {"data": {"type": "nodes", "id": parent_node_id}}
            },
        }
    }


# ===========================================================================
# Tests — Pass 1 (offline)
# ===========================================================================

class TestPass1Offline:

    def test_computes_pub_id_for_row_with_full_metadata(self):
        row = _make_row("cit_p1", family="Smith", year="2019",
                        title="A brain imaging study")
        registry = {"cit_p1": row}
        stats = _make_stats()
        _process_pass1(registry, TODAY, stats)

        expected_pub_id = build_pub_id("Smith", 2019, "A brain imaging study")
        assert row["pub_id"] == expected_pub_id
        assert row["status"] == "resolved"
        assert row["metadata_source"] == "manual"
        assert "cit_p1" in stats["pass1_resolved"]

    def test_no_http_calls_in_pass1(self):
        """Pass 1 is offline: client functions must never be invoked."""
        row = _make_row("cit_nhttp", family="Jones", year="2020",
                        title="Memory study")
        registry = {"cit_nhttp": row}
        stats = _make_stats()

        error = AssertionError("Pass 1 made a network call!")
        with (
            patch("src.clients.crossref.lookup_by_doi", side_effect=error),
            patch("src.clients.openalex.lookup_by_doi", side_effect=error),
        ):
            _process_pass1(registry, TODAY, stats)

        assert "cit_nhttp" in stats["pass1_resolved"]

    def test_skips_row_missing_year(self):
        row = _make_row("cit_missy", family="Brown", title="No year row")
        registry = {"cit_missy": row}
        stats = _make_stats()
        _process_pass1(registry, TODAY, stats)
        assert row["pub_id"] == ""
        assert "cit_missy" not in stats["pass1_resolved"]

    def test_skips_terminal_status(self):
        row = _make_row("cit_rej", family="Chen", year="2021",
                        title="Rejected entry", status="rejected")
        registry = {"cit_rej": row}
        stats = _make_stats()
        _process_pass1(registry, TODAY, stats)
        assert row["pub_id"] == ""


# ===========================================================================
# Tests — Path A direct DOI resolution
# ===========================================================================

class TestPathADirect:

    def test_crossref_happy_path(self):
        """Path A: DOI in registry → Crossref returns metadata → pub_id set."""
        doi = "10.1016/j.neuroimage.2022.100001"
        row = _make_row("cit_cr", doi=doi, status="manual")
        cr_resp = _cr_journal(doi=doi, family="Smith", year=2022,
                              title="A neuroimaging study")

        with (
            patch("src.clients.crossref.lookup_by_doi", return_value=cr_resp),
            patch("src.clients.openalex.lookup_by_doi", return_value=None),
        ):
            registry, stats, warnings = _run_pass2(row, paths="A")

        assert registry["cit_cr"]["pub_id"] != ""
        assert registry["cit_cr"]["status"] == "resolved"
        assert registry["cit_cr"]["first_author_family"] == "Smith"
        assert "cit_cr" in stats["path_a_direct"]
        assert not warnings

    def test_crossref_miss_openalex_hit(self):
        """Path A: Crossref returns None → OpenAlex returns metadata → pub_id set."""
        doi = "10.1016/j.neuroimage.2021.118411"
        row = _make_row("cit_oa", doi=doi, status="manual")
        oa_resp = _oa_journal(doi=doi, family_display="Katja Whitaker",
                              year=2022, title="A unified framework study")

        with (
            patch("src.clients.crossref.lookup_by_doi", return_value=None),
            patch("src.clients.openalex.lookup_by_doi", return_value=oa_resp),
        ):
            registry, stats, warnings = _run_pass2(row, paths="A")

        assert registry["cit_oa"]["pub_id"] != ""
        assert registry["cit_oa"]["first_author_family"] == "Whitaker"
        assert "cit_oa" in stats["path_a_direct"]


# ===========================================================================
# Tests — Path A relation chase
# ===========================================================================

class TestPathARelationChase:

    def test_is_preprint_of_chase(self):
        """bioRxiv preprint with is-preprint-of → resolves to journal metadata."""
        preprint_doi = "10.1101/2021.06.01.234567"
        journal_doi = "10.1016/j.neuro.2022.100001"

        row = _make_row("cit_biochase", doi=preprint_doi, status="manual")
        cr_preprint = _cr_preprint_is_preprint_of(
            preprint_doi=preprint_doi, journal_doi=journal_doi,
            family="Jones", title="Memory study preprint"
        )
        cr_journal = _cr_journal(
            doi=journal_doi, family="Jones", year=2022,
            title="Memory study published"  # >50% token overlap
        )

        def cr_side_effect(doi, cache_dir):
            if doi == preprint_doi:
                return cr_preprint
            if doi == journal_doi:
                return cr_journal
            return None

        with patch("src.clients.crossref.lookup_by_doi", side_effect=cr_side_effect):
            registry, stats, warnings = _run_pass2(row, paths="A")

        r = registry["cit_biochase"]
        assert r["pub_id"] != ""
        assert r["first_author_family"] == "Jones"
        assert r["year"] == "2022"  # journal year, not preprint year
        assert "preprint-chained" in r["notes"]
        assert "crossref-is-preprint-of" in r["notes"]
        assert "cit_biochase" in stats["path_a_is_preprint_of"]

    def test_has_version_chase(self):
        """PsyArXiv preprint with has-version → resolves to journal metadata."""
        preprint_doi = "10.31234/osf.io/3x2qh"
        journal_doi = "10.1016/j.neuroimage.2022.119567"

        row = _make_row("cit_psychase", doi=preprint_doi, status="needs_review")
        cr_preprint = _cr_preprint_has_version(
            preprint_doi=preprint_doi, journal_doi=journal_doi,
            family="Lee", title="Cognitive flexibility study"
        )
        cr_journal = _cr_journal(
            doi=journal_doi, family="Lee", year=2022,
            title="Cognitive flexibility in healthy adults"  # high token overlap
        )

        def cr_side_effect(doi, cache_dir):
            if doi == preprint_doi:
                return cr_preprint
            if doi == journal_doi:
                return cr_journal
            return None

        with patch("src.clients.crossref.lookup_by_doi", side_effect=cr_side_effect):
            registry, stats, warnings = _run_pass2(row, paths="A")

        r = registry["cit_psychase"]
        assert r["pub_id"] != ""
        assert r["year"] == "2022"
        assert "preprint-chained" in r["notes"]
        assert "crossref-has-version" in r["notes"]
        assert "cit_psychase" in stats["path_a_has_version"]

    def test_sanity_check_failure_falls_back_to_preprint(self):
        """Chase target fails sanity check → fall back to preprint metadata."""
        preprint_doi = "10.31234/osf.io/abc99"
        journal_doi = "10.9999/unrelated.paper"
        row = _make_row("cit_sanity", doi=preprint_doi, status="needs_review")

        cr_preprint = _cr_preprint_has_version(
            preprint_doi=preprint_doi, journal_doi=journal_doi,
            family="Martinez", title="Working memory fMRI study"
        )
        # Different family AND very different title → sanity check fails
        cr_journal_wrong = _cr_journal(
            doi=journal_doi, family="Yamamoto", year=2022,
            title="Entirely unrelated paper on sleep"
        )

        def cr_side_effect(doi, cache_dir):
            if doi == preprint_doi:
                return cr_preprint
            if doi == journal_doi:
                return cr_journal_wrong
            return None

        with patch("src.clients.crossref.lookup_by_doi", side_effect=cr_side_effect):
            registry, stats, warnings = _run_pass2(row, paths="A")

        r = registry["cit_sanity"]
        # Should use preprint metadata (family=Martinez, year from preprint)
        assert r["pub_id"] != ""
        assert r["first_author_family"] == "Martinez"
        assert any("sanity check FAILED" in w for w in warnings)
        # preprint-only fallback
        assert "cit_sanity" in stats["path_a_preprint_only"]

    def test_openalex_locations_fallback(self):
        """bioRxiv DOI, no Crossref relation → OpenAlex locations provide journal DOI."""
        preprint_doi = "10.1101/2021.03.05.999999"
        journal_doi = "10.1016/j.neuroimage.2022.119001"

        row = _make_row("cit_oafb", doi=preprint_doi, status="auto")
        cr_preprint_no_rel = _cr_preprint_no_relation(
            preprint_doi=preprint_doi, family="Chen", title="Brain connectivity in aging"
        )
        oa_preprint_with_loc = _oa_biorxiv_with_journal(
            preprint_doi=preprint_doi, journal_doi=journal_doi,
            family_display="Wei Chen", title="Brain connectivity in aging"
        )
        cr_journal = _cr_journal(
            doi=journal_doi, family="Chen", year=2022,
            title="Brain connectivity changes in healthy aging"
        )

        def cr_side_effect(doi, cache_dir):
            if doi == preprint_doi:
                return cr_preprint_no_rel
            if doi == journal_doi:
                return cr_journal
            return None

        def oa_side_effect(doi, cache_dir):
            if doi == preprint_doi:
                return oa_preprint_with_loc
            return None

        with (
            patch("src.clients.crossref.lookup_by_doi", side_effect=cr_side_effect),
            patch("src.clients.openalex.lookup_by_doi", side_effect=oa_side_effect),
        ):
            registry, stats, warnings = _run_pass2(row, paths="A")

        r = registry["cit_oafb"]
        assert r["pub_id"] != ""
        assert r["year"] == "2022"
        assert "openalex-locations" in r["notes"]
        assert "cit_oafb" in stats["path_a_openalex"]

    def test_preprint_only_no_chase(self):
        """Preprint DOI, no relation in Crossref or OpenAlex → preprint-only resolved."""
        preprint_doi = "10.1101/2021.03.05.888888"
        row = _make_row("cit_preonly", doi=preprint_doi, status="auto")

        cr_no_rel = _cr_preprint_no_relation(
            preprint_doi=preprint_doi, family="Park", title="A preprint with no journal"
        )
        oa_no_loc = _oa_preprint_no_journal(
            doi=preprint_doi, family_display="Park", title="A preprint with no journal"
        )

        with (
            patch("src.clients.crossref.lookup_by_doi", return_value=cr_no_rel),
            patch("src.clients.openalex.lookup_by_doi", return_value=oa_no_loc),
        ):
            registry, stats, warnings = _run_pass2(row, paths="A")

        r = registry["cit_preonly"]
        assert r["pub_id"] != ""
        assert "preprint-only resolved" in r["notes"]
        assert "cit_preonly" in stats["path_a_preprint_only"]


# ===========================================================================
# Tests — Path B URL synthesis
# ===========================================================================

class TestPathB:

    def test_psyarxiv_synth(self):
        """psyarxiv.com/<guid> → 10.31234/osf.io/<guid> → Path A resolved."""
        url = "https://psyarxiv.com/3x2qh"
        row = _make_row("cit_psy", url=url, status="needs_review")
        cr_resp = _cr_journal(
            doi="10.31234/osf.io/3x2qh", family="Lee", year=2020,
            title="Cognitive flexibility study"
        )

        with (
            patch("src.clients.crossref.lookup_by_doi", return_value=cr_resp),
            patch("src.clients.openalex.lookup_by_doi", return_value=None),
        ):
            registry, stats, warnings = _run_pass2(row, paths="B")

        r = registry["cit_psy"]
        assert r["pub_id"] != ""
        assert r["doi"] == "10.31234/osf.io/3x2qh"
        assert "cit_psy" in stats["path_b"]

    def test_biorxiv_synth_with_chase(self):
        """biorxiv.org/content/<doi>v1 → strip v1 → Path A with is-preprint-of chase."""
        url = "https://biorxiv.org/content/10.1101/283234v1"
        preprint_doi = "10.1101/283234"
        journal_doi = "10.1016/j.neuro.2019.100001"
        row = _make_row("cit_bio", url=url, status="needs_review")

        cr_preprint = _cr_preprint_is_preprint_of(
            preprint_doi=preprint_doi, journal_doi=journal_doi,
            family="Kim", title="Neural synchrony study"
        )
        cr_journal = _cr_journal(
            doi=journal_doi, family="Kim", year=2019,
            title="Neural synchrony during encoding"
        )

        def cr_side_effect(doi, cache_dir):
            if doi == preprint_doi:
                return cr_preprint
            if doi == journal_doi:
                return cr_journal
            return None

        with (
            patch("src.clients.crossref.lookup_by_doi", side_effect=cr_side_effect),
            patch("src.clients.openalex.lookup_by_doi", return_value=None),
        ):
            registry, stats, warnings = _run_pass2(row, paths="B")

        r = registry["cit_bio"]
        assert r["pub_id"] != ""
        assert r["doi"] == preprint_doi
        assert r["year"] == "2019"
        assert "cit_bio" in stats["path_b"]

    def test_elife_synth(self):
        """elifesciences.org/articles/<id> → 10.7554/eLife.<id> → Path A."""
        url = "https://elifesciences.org/articles/12345"
        doi = "10.7554/elife.12345"  # _try_synth canonicalises to lowercase
        row = _make_row("cit_eli", url=url, status="needs_review")
        cr_resp = _cr_journal(doi=doi, family="Nguyen", year=2021,
                              title="Synaptic plasticity mechanisms")

        with (
            patch("src.clients.crossref.lookup_by_doi", return_value=cr_resp),
            patch("src.clients.openalex.lookup_by_doi", return_value=None),
        ):
            registry, stats, warnings = _run_pass2(row, paths="B")

        r = registry["cit_eli"]
        assert r["pub_id"] != ""
        assert r["doi"] == doi
        assert "cit_eli" in stats["path_b"]


# ===========================================================================
# Tests — Path C PMID URL → Europe PMC
# ===========================================================================

class TestPathC:

    def test_pmid_url_europepmc_doi_then_path_a(self):
        """pubmed URL → lookup_by_pmid → DOI → Path A resolved."""
        url = "https://pubmed.ncbi.nlm.nih.gov/12345678"
        epmc_doi = "10.1016/j.neuroimage.2018.10.002"
        row = _make_row("cit_pmid", url=url, status="needs_review")

        epmc_resp = _epmc_with_doi(pmid="12345678", doi=epmc_doi,
                                   family="Adams", title="fMRI paradigm", year=2019)
        cr_resp = _cr_journal(doi=epmc_doi, family="Adams", year=2019,
                              title="fMRI paradigm study")

        with (
            patch("src.clients.europepmc.lookup_by_pmid", return_value=epmc_resp),
            patch("src.clients.crossref.lookup_by_doi", return_value=cr_resp),
            patch("src.clients.openalex.lookup_by_doi", return_value=None),
        ):
            registry, stats, warnings = _run_pass2(row, paths="C")

        r = registry["cit_pmid"]
        assert r["pub_id"] != ""
        assert r["doi"] == epmc_doi
        assert "cit_pmid" in stats["path_c"]


# ===========================================================================
# Tests — Path D OSF
# ===========================================================================

class TestPathD:

    def test_osf_preprint_resolved(self):
        """OSF preprint GUID → attrs.doi → Path A → resolved."""
        url = "https://osf.io/3x2qh"
        preprint_doi = "10.31234/osf.io/3x2qh"
        row = _make_row("cit_osfp", url=url, status="needs_review")

        guid_resp = _osf_guid_resp("preprints", "3x2qh")
        typed_resp = _osf_preprint_resp("3x2qh", preprint_doi, "Cognitive flexibility preprint")
        cr_resp = _cr_journal(doi=preprint_doi, family="Lee", year=2020,
                              title="Cognitive flexibility study")

        with (
            patch("src.clients.osf.lookup_guid", return_value=guid_resp),
            patch("src.clients.osf.lookup_typed", return_value=typed_resp),
            patch("src.clients.crossref.lookup_by_doi", return_value=cr_resp),
            patch("src.clients.openalex.lookup_by_doi", return_value=None),
        ):
            registry, stats, warnings = _run_pass2(row, paths="D")

        r = registry["cit_osfp"]
        assert r["pub_id"] != ""
        assert r["doi"] == preprint_doi
        assert "cit_osfp" in stats["path_d_preprint"]

    def test_osf_node_stays_needs_review(self):
        """OSF node → cache written, status stays needs_review, no pub_id."""
        url = "https://osf.io/bxvhr"
        row = _make_row("cit_osfn", url=url, status="needs_review")

        guid_resp = _osf_guid_resp("nodes", "bxvhr")
        typed_resp = _osf_node_resp("bxvhr", "Dataset node")

        with (
            patch("src.clients.osf.lookup_guid", return_value=guid_resp),
            patch("src.clients.osf.lookup_typed", return_value=typed_resp),
        ):
            registry, stats, warnings = _run_pass2(row, paths="D")

        r = registry["cit_osfn"]
        assert r["pub_id"] == ""
        assert r["status"] == "needs_review"  # unchanged
        assert "cit_osfn" in stats["path_d_node"]
        assert "cit_osfn" not in stats["still_pending"]

    def test_osf_401_graceful(self):
        """OSF 401 (private project) → empty guid response → row unchanged."""
        url = "https://osf.io/er5u7"
        row = _make_row("cit_osfpriv", url=url, status="needs_review")

        with patch("src.clients.osf.lookup_guid", return_value={}):
            registry, stats, warnings = _run_pass2(row, paths="D")

        r = registry["cit_osfpriv"]
        assert r["pub_id"] == ""
        assert r["status"] == "needs_review"

    def test_osf_files_recurse_to_parent(self):
        """OSF files type → follow parent node → node cached, not promoted."""
        url = "https://osf.io/abc12"
        row = _make_row("cit_osffile", url=url, status="needs_review")

        # First call: files GUID → type=files, id=fileabc
        guid_file_resp = _osf_guid_resp("files", "fileabc")
        # Typed lookup for file → has parent node "bxvhr"
        typed_file_resp = _osf_file_resp("fileabc", "bxvhr")
        # Recursive: lookup_guid for parent "bxvhr" → type=nodes, id=bxvhr
        guid_node_resp = _osf_guid_resp("nodes", "bxvhr")
        typed_node_resp = _osf_node_resp("bxvhr", "Parent dataset node")

        def mock_lookup_guid(guid, **kwargs):
            if guid == "abc12":
                return guid_file_resp
            if guid == "bxvhr":
                return guid_node_resp
            return {}

        def mock_lookup_typed(obj_type, obj_id, **kwargs):
            if obj_type == "files" and obj_id == "fileabc":
                return typed_file_resp
            if obj_type == "nodes" and obj_id == "bxvhr":
                return typed_node_resp
            return {}

        with (
            patch("src.clients.osf.lookup_guid", side_effect=mock_lookup_guid),
            patch("src.clients.osf.lookup_typed", side_effect=mock_lookup_typed),
        ):
            registry, stats, warnings = _run_pass2(row, paths="D")

        r = registry["cit_osffile"]
        assert r["pub_id"] == ""
        assert r["status"] == "needs_review"
        assert "cit_osffile" in stats["path_d_node"]

    def test_osf_project_doi_filtered(self):
        """OSF preprint whose attrs.doi is a project DOI must NOT be promoted."""
        url = "https://osf.io/ycqgd"
        row = _make_row("cit_projdoi", url=url, status="needs_review")

        guid_resp = _osf_guid_resp("preprints", "ycqgd")
        # attrs.doi is a 10.17605 project DOI — must be filtered
        typed_resp = {
            "data": {
                "type": "preprints",
                "id": "ycqgd",
                "attributes": {"doi": "10.17605/OSF.IO/YCQGD", "title": "Test"},
            }
        }

        with (
            patch("src.clients.osf.lookup_guid", return_value=guid_resp),
            patch("src.clients.osf.lookup_typed", return_value=typed_resp),
        ):
            registry, stats, warnings = _run_pass2(row, paths="D")

        assert registry["cit_projdoi"]["pub_id"] == ""


# ===========================================================================
# Tests — edge cases and guards
# ===========================================================================

class TestEdgeCases:

    def test_terminal_status_rows_skipped(self):
        """Rows with rejected / not_a_citation status skipped entirely."""
        rows = [
            _make_row("cit_rej", doi="10.1234/foo", status="rejected"),
            _make_row("cit_nac", doi="10.1234/bar", status="not_a_citation"),
        ]
        for row in rows:
            registry = {row["citation_id"]: row}
            stats = _make_stats()
            warnings: list[str] = []
            _process_pass2(registry, TODAY, {"A"}, CACHE, None, warnings, stats)
            assert row["pub_id"] == ""
            assert row["citation_id"] in stats["skipped_terminal"]

    def test_idempotency_second_run_is_noop(self, tmp_path):
        """After first resolution, second run leaves registry byte-identical."""
        doi = "10.1016/j.neuroimage.2022.100001"
        row = _make_row("cit_idem", doi=doi, status="manual")
        cr_resp = _cr_journal(doi=doi, family="Wang", year=2022,
                              title="An idempotency test paper")

        with (
            patch("src.clients.crossref.lookup_by_doi", return_value=cr_resp),
            patch("src.clients.openalex.lookup_by_doi", return_value=None),
        ):
            # First run
            registry1 = {"cit_idem": dict(row)}
            stats1 = _make_stats()
            warnings1: list[str] = []
            _process_pass2(registry1, TODAY, {"A"}, CACHE, None, warnings1, stats1)

            assert registry1["cit_idem"]["pub_id"] != ""

            # Write and read back
            registry_path = tmp_path / "registry.tsv"
            write_registry(registry_path, registry1, COLUMNS)
            registry2, _ = load_registry(registry_path)

            # Second run — should skip the already-resolved row
            stats2 = _make_stats()
            warnings2: list[str] = []
            _process_pass2(registry2, TODAY, {"A"}, CACHE, None, warnings2, stats2)

            assert "cit_idem" in stats2["skipped_already"]
            assert stats2["path_a_direct"] == []

            # Write again and compare bytes
            registry_path2 = tmp_path / "registry2.tsv"
            write_registry(registry_path2, registry2, COLUMNS)
            assert registry_path.read_bytes() == registry_path2.read_bytes()


# ===========================================================================
# Tests — Registry write/read round-trip (truncation regression)
# ===========================================================================

class TestRegistryRoundTrip:

    def test_200_row_roundtrip_no_truncation(self, tmp_path):
        """write_registry + load_registry round-trip preserves all 200+ rows."""
        n_rows = 210
        rows: dict[str, dict] = {}
        for i in range(n_rows):
            cit_id = f"cit_{i:06d}"
            rows[cit_id] = {
                "citation_id": cit_id,
                "doi": f"10.1234/test.{i:04d}" if i % 3 != 0 else "",
                "url": f"https://example.com/paper/{i}" if i % 3 == 0 else "",
                "source_link": f"https://openneuro.org/ds{i:06d}",
                "pub_id": f"pub_abc{i:04x}" if i % 5 == 0 else "",
                "first_author_family": f"Author{i}",
                "year": str(2010 + i % 15),
                "title": f"Paper title number {i} with sufficient length",
                "status": ["auto", "manual", "needs_review", "rejected"][i % 4],
                "metadata_source": "crossref" if i % 2 == 0 else "",
                "verified_on": "2026-05-06" if i % 3 == 0 else "",
                "notes": f"note {i}" if i % 7 == 0 else "",
            }

        registry_path = tmp_path / "big_registry.tsv"
        write_registry(registry_path, rows, COLUMNS)

        loaded, loaded_cols = load_registry(registry_path)

        assert len(loaded) == n_rows
        assert loaded_cols == COLUMNS

        for cit_id, original_row in rows.items():
            loaded_row = loaded[cit_id]
            for col in COLUMNS:
                assert loaded_row[col] == original_row[col], (
                    f"Column {col!r} mismatch for {cit_id}: "
                    f"expected {original_row[col]!r}, got {loaded_row[col]!r}"
                )

    def test_write_is_atomic(self, tmp_path):
        """write_registry uses tmp file → rename so no partial writes on disk."""
        rows = {"cit_000001": _make_row("cit_000001", doi="10.1/test",
                                        family="Test", year="2020", title="T")}
        p = tmp_path / "reg.tsv"
        write_registry(p, rows, COLUMNS)

        # Verify no .tmp files left behind
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], f"Leftover tmp files: {tmp_files}"

        # Verify the written file has the correct row count (header + 1 data row)
        lines = p.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2  # header + 1 row


# ===========================================================================
# Tests — Utility / helper functions
# ===========================================================================

class TestHelpers:

    def test_title_token_overlap_identical(self):
        assert _title_token_overlap("a memory study", "a memory study") == 1.0

    def test_title_token_overlap_no_overlap(self):
        assert _title_token_overlap("memory fMRI study", "sleep apnea disorders") == 0.0

    def test_title_token_overlap_partial(self):
        score = _title_token_overlap(
            "Brain connectivity in aging",
            "Brain connectivity changes in healthy aging"
        )
        assert score >= 0.5

    def test_sanity_check_family_match_sufficient(self):
        assert _sanity_check(
            {"family": "Smith", "title": "Completely different title XYZ"},
            {"family": "Smith", "title": "Another very different title ABC"},
        )

    def test_sanity_check_title_overlap_sufficient(self):
        # "Working memory and attention" overlaps heavily despite different families
        # Jaccard: {"working","memory","and","attention"} / {"working","memory","and",
        #          "attention","fmri","in","adults"} = 4/7 ≥ 0.5
        assert _sanity_check(
            {"family": "Jones", "title": "Working memory and attention fMRI"},
            {"family": "Brown", "title": "Working memory and attention in adults"},
        )

    def test_sanity_check_fails_different_author_and_title(self):
        assert not _sanity_check(
            {"family": "Garcia", "title": "Cognitive flexibility paradigm"},
            {"family": "Yamamoto", "title": "Sleep deprivation effects"},
        )

    def test_parse_osf_url_simple_guid(self):
        assert _parse_osf_url("https://osf.io/bxvhr") == "bxvhr"

    def test_parse_osf_url_with_view_only(self):
        assert _parse_osf_url("https://osf.io/bxvhr/?view_only=abc") == "bxvhr"

    def test_parse_osf_url_preprints(self):
        assert _parse_osf_url("https://osf.io/preprints/psyarxiv/3x2qh") == "3x2qh"

    def test_parse_osf_url_non_osf(self):
        assert _parse_osf_url("https://example.com/paper") is None

    def test_try_synth_psyarxiv_com(self):
        assert _try_synth("https://psyarxiv.com/3x2qh") == "10.31234/osf.io/3x2qh"

    def test_try_synth_psyarxiv_org(self):
        assert _try_synth("https://psyarxiv.org/3x2qh") == "10.31234/osf.io/3x2qh"

    def test_try_synth_biorxiv_strips_version(self):
        assert _try_synth(
            "https://biorxiv.org/content/10.1101/283234v2"
        ) == "10.1101/283234"

    def test_try_synth_elife(self):
        # DOIs are canonical lowercase; eLife's camelcase is lowercased by _try_synth
        assert _try_synth("https://elifesciences.org/articles/67890") == "10.7554/elife.67890"

    def test_extract_relation_doi_is_preprint_of_wins(self):
        cr_data = {
            "relation": {
                "is-preprint-of": [{"id-type": "doi", "id": "10.1234/journal"}],
                "has-version":    [{"id-type": "doi", "id": "10.5678/other"}],
            }
        }
        doi, via = _extract_relation_doi(cr_data)
        assert doi == "10.1234/journal"
        assert via == "crossref-is-preprint-of"

    def test_extract_relation_doi_osf_project_doi_filtered(self):
        """10.17605/OSF.IO/* DOIs in relation fields are filtered out."""
        cr_data = {
            "relation": {
                "is-preprint-of": [
                    {"id-type": "doi", "id": "10.17605/OSF.IO/YCQGD"}
                ]
            }
        }
        doi, via = _extract_relation_doi(cr_data)
        assert doi is None

    def test_extract_journal_doi_from_openalex_published_version(self):
        oa_data = _oa_biorxiv_with_journal(
            journal_doi="10.1016/j.neuroimage.2022.119001"
        )
        result = _extract_journal_doi_from_openalex(oa_data)
        assert result == "10.1016/j.neuroimage.2022.119001"

    def test_extract_journal_doi_from_openalex_no_journal(self):
        oa_data = _oa_preprint_no_journal()
        result = _extract_journal_doi_from_openalex(oa_data)
        assert result is None
