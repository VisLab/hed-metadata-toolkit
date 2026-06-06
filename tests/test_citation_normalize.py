"""
test_citation_normalize.py — Fixture-driven tests for citation_normalize.

Pinned cases cover:
  - DOI canonicalisation across http/https, www., doi: prefix,
    uppercase suffix, trailing punctuation, trailing whitespace,
    literal "\\n" (2-char) suffix that appears in production data
  - URL canonicalisation across schemes, fragments, utm_* params,
    literal "\\n" suffix
  - Publisher → DOI synthesis for every v2 §4.3 pattern
  - URL-only hosts (Cambridge, eLife, OSF, PubMed) deliberately
    return None from synthesis
  - Junk-link detection via skip-list and file-extension rules
  - load_skip_list parses comments and blank lines correctly

No network, no fixtures beyond pytest's tmp_path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hed_metadata_toolkit.citation_normalize import (  # noqa: E402
    canonicalize_doi,
    canonicalize_url,
    extract_doi,
    is_junk_link,
    load_skip_list,
    synthesise_doi_from_url,
)


# canonicalize_doi -----------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("10.1234/xyz", "10.1234/xyz"),
        ("doi:10.1234/xyz", "10.1234/xyz"),
        ("DOI:10.1234/xyz", "10.1234/xyz"),
        ("https://doi.org/10.1234/xyz", "10.1234/xyz"),
        ("http://doi.org/10.1234/xyz", "10.1234/xyz"),
        ("https://dx.doi.org/10.1234/xyz", "10.1234/xyz"),
        ("https://doi.org/10.1234/XYZ", "10.1234/xyz"),
        ("https://doi.org/10.1234/xyz.", "10.1234/xyz"),
        ("https://doi.org/10.1234/xyz)", "10.1234/xyz"),
        ("https://doi.org/10.1234/xyz   ", "10.1234/xyz"),
        ("https://doi.org/10.1234/xyz\n", "10.1234/xyz"),
        ("https://doi.org/10.1234/xyz\\n", "10.1234/xyz"),
        ("https://doi.org/10.1234/xyz\\n\\n", "10.1234/xyz"),
    ],
    ids=lambda v: v.replace("/", "_")[:30] if isinstance(v, str) else str(v),
)
def test_canonicalize_doi(raw, expected):
    assert canonicalize_doi(raw) == expected


# canonicalize_url -----------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("http://example.org/foo", "https://example.org/foo"),
        ("www.example.org/foo", "https://www.example.org/foo"),
        ("https://Example.ORG/Foo", "https://example.org/Foo"),
        ("https://example.org/foo/", "https://example.org/foo"),
        ("https://example.org/foo\n", "https://example.org/foo"),
        ("https://example.org/foo   ", "https://example.org/foo"),
        ("https://example.org/foo\\n", "https://example.org/foo"),
        ("https://example.org/foo#section", "https://example.org/foo"),
        (
            "https://example.org/foo?utm_source=x&utm_medium=y&id=42",
            "https://example.org/foo?id=42",
        ),
        (
            "HTTP://Example.ORG/Foo/?utm_source=x#bar\n",
            "https://example.org/Foo?",
        ),
    ],
    ids=lambda v: v[:40] if isinstance(v, str) else str(v),
)
def test_canonicalize_url(raw, expected):
    actual = canonicalize_url(raw)
    assert actual.rstrip("?") == expected.rstrip("?")


# synthesise_doi_from_url ----------------------------------------------------

@pytest.mark.parametrize(
    "url, expected_doi",
    [
        (
            "https://www.nature.com/articles/s41597-023-02396-5",
            "10.1038/s41597-023-02396-5",
        ),
        (
            "https://link.springer.com/article/10.1007/s00112-023-04456-7",
            "10.1007/s00112-023-04456-7",
        ),
        (
            "https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0001234",
            "10.1371/journal.pone.0001234",
        ),
        (
            "https://www.tandfonline.com/doi/full/10.1080/12345678.2024.0001",
            "10.1080/12345678.2024.0001",
        ),
        (
            "https://direct.mit.edu/nol/article/5/2/315/118227/10.1162/nol_a_00099",
            "10.1162/nol_a_00099",
        ),
        (
            "https://www.frontiersin.org/journals/human-neuroscience/articles/10.3389/fnhum.2024.1329086/full",
            "10.3389/fnhum.2024.1329086",
        ),
    ],
    ids=["nature", "springer", "plos", "tandfonline", "mit", "frontiers"],
)
def test_synthesise_doi_from_url_positive(url, expected_doi):
    assert synthesise_doi_from_url(url) == expected_doi


@pytest.mark.parametrize(
    "url",
    [
        "https://www.cambridge.org/core/journals/foo/article/bar/2BF58F5EC8",
        "https://elifesciences.org/articles/82580",
        "https://osf.io/abcde",
        "https://pubmed.ncbi.nlm.nih.gov/12345",
        "https://www.ncbi.nlm.nih.gov/pubmed/12345",
        "https://lab.example.org/data",
    ],
    ids=["cambridge", "elife", "osf", "pubmed", "ncbi-pubmed", "generic"],
)
def test_synthesise_doi_from_url_returns_none_for_url_only_hosts(url):
    assert synthesise_doi_from_url(url) is None


# extract_doi ----------------------------------------------------------------

@pytest.mark.parametrize(
    "link, expected",
    [
        ("doi:10.1234/xyz", "10.1234/xyz"),
        ("https://doi.org/10.1234/xyz", "10.1234/xyz"),
        (
            "https://www.frontiersin.org/journals/human-neuroscience/articles/10.3389/fnhum.2024.1329086/full",
            "10.3389/fnhum.2024.1329086",
        ),
        (
            "https://www.nature.com/articles/s41597-023-02396-5",
            "10.1038/s41597-023-02396-5",
        ),
        (
            "https://www.cambridge.org/core/journals/foo/article/bar/2BF58F5EC8",
            None,
        ),
        ("https://lab.example.org/page", None),
        ("https://doi.org/10.1234/xyz\n", "10.1234/xyz"),
    ],
    ids=[
        "doi-prefix",
        "doi-org",
        "frontiers-no-overcapture",
        "nature-synth",
        "cambridge-none",
        "generic-none",
        "trailing-newline",
    ],
)
def test_extract_doi(link, expected):
    assert extract_doi(link) == expected


# is_junk_link ---------------------------------------------------------------

_TEST_SKIP_LIST = [
    "openneuro.org",
    "github.com",
    "/licenses/",
]


@pytest.mark.parametrize(
    "link, expected",
    [
        ("https://openneuro.org/datasets/ds001", True),
        ("https://OpenNeuro.org/datasets/ds001", True),
        ("https://github.com/foo/bar", True),
        ("https://example.org/licenses/MIT.html", True),
        ("https://example.org/data.zip", True),
        ("https://example.org/install.exe", True),
        ("https://doi.org/10.1234/xyz", False),
        ("https://www.nature.com/articles/s41597-023-02396-5", False),
    ],
    ids=[
        "openneuro", "openneuro-uppercase", "github", "licenses",
        "zip-file", "exe-file", "real-doi", "real-nature",
    ],
)
def test_is_junk_link(link, expected):
    assert is_junk_link(link, _TEST_SKIP_LIST) is expected


# load_skip_list -------------------------------------------------------------

def test_load_skip_list(tmp_path):
    f = tmp_path / "skip.txt"
    f.write_text(
        "# Header comment\n"
        "\n"
        "openneuro.org\n"
        "  github.com  \n"
        "# section divider\n"
        "\n"
        "doi:10.18112/openneuro.\n",
        encoding="utf-8",
    )
    patterns = load_skip_list(f)
    assert patterns == ["openneuro.org", "github.com", "doi:10.18112/openneuro."]


def test_load_skip_list_real_file_is_nonempty():
    skip_list_path = (
        Path(__file__).resolve().parent.parent
        / "config" / "citation_skip_list.txt"
    )
    patterns = load_skip_list(skip_list_path)
    assert len(patterns) >= 30
    assert "openneuro.org" in patterns
    assert "github.com" in patterns


# Round-trip / collision detection -------------------------------------------

@pytest.mark.parametrize(
    "link_a, link_b",
    [
        (
            "https://www.nature.com/articles/s41597-023-02396-5",
            "www.nature.com/articles/s41597-023-02396-5",
        ),
        (
            "https://doi.org/10.1234/xyz",
            "http://doi.org/10.1234/xyz",
        ),
        (
            "doi:10.1234/xyz",
            "https://doi.org/10.1234/xyz",
        ),
        (
            "https://doi.org/10.1234/XYZ",
            "https://doi.org/10.1234/xyz",
        ),
        (
            "https://dx.doi.org/10.1234/xyz",
            "https://doi.org/10.1234/xyz",
        ),
    ],
    ids=[
        "nature-www-vs-https", "doi-http-vs-https", "doi-prefix-vs-url",
        "uppercase-vs-lower", "dx-vs-no-dx",
    ],
)
def test_canonicalisation_collapses_known_collisions(link_a, link_b):
    doi_a = extract_doi(link_a)
    doi_b = extract_doi(link_b)
    assert doi_a is not None
    assert doi_b is not None
    assert doi_a == doi_b
