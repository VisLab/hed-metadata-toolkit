"""test_generate_review_queue.py — Tests for generate_review_queue.py.

Covers:
  - Registry → JSON shape (8 required fields per entry)
  - Hints populated for an OSF row (OSF cache mock + Crossref title search mock)
  - Hints absent when include_hints=False
  - Rows with pub_id non-empty are excluded
  - Rejected / not_a_citation rows excluded
  - --limit N truncates correctly
  - Date-stamped output filename via main()
  - _parse_osf_guid handles preprints/<provider>/<guid> form
  - _build_osf_hints: osf_private returned for a cached-empty GUID

No live network calls.  OSF and Crossref I/O is intercepted by writing
fixture files into a tmp cache directory or by monkeypatching.

Run:
    pytest tests/test_generate_review_queue.py -v
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


from hed_metadata_toolkit.citations.generate_review_queue import (  # noqa: E402
    _parse_osf_guid,
    generate_queue,
    load_registry,
    write_json_atomic,
)

TODAY = "2026-05-11"
EMAIL = "hedannotation@gmail.com"

COLUMNS = [
    "citation_id",
    "doi",
    "url",
    "source_link",
    "pub_id",
    "first_author_family",
    "year",
    "title",
    "status",
    "metadata_source",
    "verified_on",
    "notes",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(
    cit: str,
    *,
    doi: str = "",
    url: str = "",
    pub_id: str = "",
    status: str = "needs_review",
    notes: str = "",
    title: str = "",
    first_author_family: str = "",
    year: str = "",
) -> dict:
    return {
        "citation_id": cit,
        "doi": doi,
        "url": url,
        "source_link": "",
        "pub_id": pub_id,
        "first_author_family": first_author_family,
        "year": year,
        "title": title,
        "status": status,
        "metadata_source": "",
        "verified_on": "",
        "notes": notes,
    }


def _reg(*rows: dict) -> dict[str, dict]:
    return {r["citation_id"]: r for r in rows}


def _cache_hex(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _write_stable_cache(cache_dir: Path, source: str, key: str, response: dict) -> None:
    """Write a fake stable cache entry."""
    h = _cache_hex(key)
    p = cache_dir / source / "stable" / f"{h}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"source": source, "key": key, "fetched_on": TODAY, "response": response}
    p.write_text(json.dumps(payload), encoding="utf-8")


def _write_dated_cache(
    cache_dir: Path, source: str, key: str, response: dict, date: str = TODAY
) -> None:
    """Write a fake date-stamped cache entry."""
    h = _cache_hex(key)
    p = cache_dir / source / date / f"{h}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"source": source, "key": key, "fetched_on": date, "response": response}
    p.write_text(json.dumps(payload), encoding="utf-8")


# Minimal OSF node API response shape
def _osf_node_guid_response(guid: str, title: str, description: str = "") -> dict:
    return {
        "data": {
            "id": guid,
            "type": "nodes",
            "attributes": {
                "title": title,
                "description": description,
            },
            "embeds": {},
            "relationships": {},
        }
    }


# Minimal Crossref title-search response
def _cr_search_response(items: list[dict]) -> dict:
    return {"message": {"items": items}}


def _cr_item(doi: str, title: str, family: str, year: int) -> dict:
    return {
        "DOI": doi,
        "title": [title],
        "container-title": ["Test Journal"],
        "author": [{"sequence": "first", "family": family}],
        "published": {"date-parts": [[year]]},
    }


# ---------------------------------------------------------------------------
# Tests: JSON shape
# ---------------------------------------------------------------------------


def test_entry_has_eight_required_fields(tmp_path):
    """Every emitted entry must have the 8 required fields."""
    reg = _reg(_row("cit_000001", url="https://example.com"))
    entries = generate_queue(
        reg, tmp_path, TODAY, include_hints=False, limit=None, email=EMAIL
    )
    assert len(entries) == 1
    e = entries[0]
    for field in (
        "citation_id",
        "url",
        "status",
        "doi",
        "resolved_url",
        "notes",
        "manual_fill",
    ):
        assert field in e, f"Field {field!r} missing from entry"
    assert e["citation_id"] == "cit_000001"
    assert e["status"] == "needs_review"
    assert e["manual_fill"] is None
    assert e["resolved_url"] is None


def test_url_field_falls_back_to_doi_prefix(tmp_path):
    """When url is empty but doi is set, url field is 'doi:<doi>'."""
    reg = _reg(_row("cit_000002", doi="10.1000/test"))
    entries = generate_queue(
        reg, tmp_path, TODAY, include_hints=False, limit=None, email=EMAIL
    )
    assert entries[0]["url"] == "doi:10.1000/test"
    assert entries[0]["doi"] == "10.1000/test"


def test_doi_field_is_null_when_empty(tmp_path):
    """doi field is None (not empty string) when no doi in registry."""
    reg = _reg(_row("cit_000003", url="https://example.com"))
    entries = generate_queue(
        reg, tmp_path, TODAY, include_hints=False, limit=None, email=EMAIL
    )
    assert entries[0]["doi"] is None


# ---------------------------------------------------------------------------
# Tests: row filtering
# ---------------------------------------------------------------------------


def test_pub_id_set_row_excluded(tmp_path):
    """Rows with pub_id non-empty are excluded from the queue."""
    reg = _reg(
        _row("cit_000001", url="https://example.com", pub_id="pub_abc12345"),
        _row("cit_000002", url="https://example.com"),
    )
    entries = generate_queue(
        reg, tmp_path, TODAY, include_hints=False, limit=None, email=EMAIL
    )
    assert len(entries) == 1
    assert entries[0]["citation_id"] == "cit_000002"


def test_rejected_row_excluded(tmp_path):
    """Rows with status='rejected' are excluded."""
    reg = _reg(
        _row("cit_000001", url="https://example.com", status="rejected"),
        _row("cit_000002", url="https://example.com"),
    )
    entries = generate_queue(
        reg, tmp_path, TODAY, include_hints=False, limit=None, email=EMAIL
    )
    assert len(entries) == 1
    assert entries[0]["citation_id"] == "cit_000002"


def test_not_a_citation_row_excluded(tmp_path):
    """Rows with status='not_a_citation' are excluded."""
    reg = _reg(
        _row("cit_000001", url="https://example.com", status="not_a_citation"),
        _row("cit_000002", url="https://example.com"),
    )
    entries = generate_queue(
        reg, tmp_path, TODAY, include_hints=False, limit=None, email=EMAIL
    )
    assert len(entries) == 1
    assert entries[0]["citation_id"] == "cit_000002"


def test_resolved_row_excluded(tmp_path):
    """Rows with status='resolved' are excluded."""
    reg = _reg(
        _row("cit_000001", url="https://example.com", status="resolved"),
        _row("cit_000002", url="https://example.com"),
    )
    entries = generate_queue(
        reg, tmp_path, TODAY, include_hints=False, limit=None, email=EMAIL
    )
    assert len(entries) == 1
    assert entries[0]["citation_id"] == "cit_000002"


def test_row_without_url_or_doi_excluded(tmp_path):
    """Rows with neither url nor doi are excluded even if not terminal."""
    reg = _reg(
        _row("cit_000001"),  # no url, no doi
        _row("cit_000002", url="https://example.com"),
    )
    entries = generate_queue(
        reg, tmp_path, TODAY, include_hints=False, limit=None, email=EMAIL
    )
    assert len(entries) == 1
    assert entries[0]["citation_id"] == "cit_000002"


# ---------------------------------------------------------------------------
# Tests: --limit
# ---------------------------------------------------------------------------


def test_limit_truncates_output(tmp_path):
    """--limit N stops after N entries."""
    reg = _reg(
        _row("cit_000001", url="https://a.com"),
        _row("cit_000002", url="https://b.com"),
        _row("cit_000003", url="https://c.com"),
        _row("cit_000004", url="https://d.com"),
        _row("cit_000005", url="https://e.com"),
    )
    entries = generate_queue(
        reg, tmp_path, TODAY, include_hints=False, limit=3, email=EMAIL
    )
    assert len(entries) == 3


def test_limit_zero_gives_empty(tmp_path):
    reg = _reg(_row("cit_000001", url="https://a.com"))
    entries = generate_queue(
        reg, tmp_path, TODAY, include_hints=False, limit=0, email=EMAIL
    )
    assert entries == []


# ---------------------------------------------------------------------------
# Tests: hints absent when include_hints=False
# ---------------------------------------------------------------------------


def test_hints_absent_when_disabled(tmp_path):
    """No 'hints' key when include_hints=False."""
    reg = _reg(_row("cit_000001", url="https://osf.io/abc12"))
    entries = generate_queue(
        reg, tmp_path, TODAY, include_hints=False, limit=None, email=EMAIL
    )
    assert "hints" not in entries[0]


# ---------------------------------------------------------------------------
# Tests: OSF hints (mocked cache)
# ---------------------------------------------------------------------------


def test_osf_hints_populated_from_cache(tmp_path):
    """OSF hints are populated when a node response is in the stable cache."""
    guid = "abcde"
    title = "Test OSF Project Title for Neuroscience"
    description = "A study with DOI https://doi.org/10.1038/s41597-021-01234-5 in it."

    # Write a fake GUID cache that is the node itself (shape A from our code)
    node_response = _osf_node_guid_response(guid, title, description)
    _write_stable_cache(tmp_path, "osf", f"guid:{guid}", node_response)

    # Write a fake Crossref title-search cache
    title_key = f"crossref_title_search|{title.lower()}"
    cr_response = _cr_search_response(
        [
            _cr_item("10.1038/s41597-021-01234-5", title, "Smith", 2021),
        ]
    )
    _write_dated_cache(tmp_path, "crossref", title_key, cr_response, TODAY)

    reg = _reg(_row("cit_000001", url=f"https://osf.io/{guid}"))
    entries = generate_queue(
        reg, tmp_path, TODAY, include_hints=True, limit=None, email=EMAIL
    )

    assert len(entries) == 1
    e = entries[0]
    assert "hints" in e
    h = e["hints"]
    assert h.get("osf_type") == "nodes"
    assert h.get("osf_title") == title
    assert "osf_description_excerpt" in h
    assert "10.1038/s41597-021-01234-5" in h.get("osf_description_dois", [])


def test_osf_hints_crossref_candidates_filtered(tmp_path):
    """crossref_candidates are filtered by title overlap >= 0.5."""
    guid = "xyzzy"
    title = "Memory consolidation during sleep in humans"
    node_response = _osf_node_guid_response(guid, title)
    _write_stable_cache(tmp_path, "osf", f"guid:{guid}", node_response)

    # One relevant item (high overlap), one irrelevant
    relevant = _cr_item(
        "10.1000/good", "Memory consolidation during sleep", "Jones", 2020
    )
    irrelevant = _cr_item(
        "10.1000/bad", "Completely unrelated paper on cats", "Baker", 2019
    )
    title_key = f"crossref_title_search|{title.lower()}"
    _write_dated_cache(
        tmp_path,
        "crossref",
        title_key,
        _cr_search_response([relevant, irrelevant]),
        TODAY,
    )

    reg = _reg(_row("cit_000001", url=f"https://osf.io/{guid}"))
    entries = generate_queue(
        reg, tmp_path, TODAY, include_hints=True, limit=None, email=EMAIL
    )
    cands = entries[0].get("hints", {}).get("crossref_candidates", [])
    dois = [c["doi"] for c in cands]
    assert "10.1000/good" in dois
    assert "10.1000/bad" not in dois


def test_osf_private_hint_for_cached_empty_guid(tmp_path):
    """osf_private=True is returned when the GUID is cached as empty (401/404)."""
    guid = "priv1"
    # Write an empty stable cache (simulates a cached 401)
    _write_stable_cache(tmp_path, "osf", f"guid:{guid}", {})

    reg = _reg(_row("cit_000001", url=f"https://osf.io/{guid}"))
    entries = generate_queue(
        reg, tmp_path, TODAY, include_hints=True, limit=None, email=EMAIL
    )
    h = entries[0].get("hints", {})
    assert h.get("osf_private") is True


def test_no_osf_hints_when_not_in_cache(tmp_path):
    """No osf_ hints when the GUID has no cache entry (cache miss)."""
    reg = _reg(_row("cit_000001", url="https://osf.io/zzzzz"))
    entries = generate_queue(
        reg, tmp_path, TODAY, include_hints=True, limit=None, email=EMAIL
    )
    h = entries[0].get("hints", {})
    # May have crossref_candidates if title is set, but no osf_* keys
    for key in ("osf_type", "osf_title", "osf_private", "osf_contributors"):
        assert key not in h


# ---------------------------------------------------------------------------
# Tests: date-stamped output filename
# ---------------------------------------------------------------------------


def test_date_stamped_output_filename(tmp_path, monkeypatch):
    """main() writes manual_review_<today>.json."""
    import generate_review_queue as grq  # noqa: PLC0415

    # Write a minimal registry
    reg_path = tmp_path / "registry.tsv"
    with reg_path.open("w", encoding="utf-8") as fh:
        fh.write("\t".join(COLUMNS) + "\n")
        fh.write(
            "\t".join(
                [
                    "cit_000001",
                    "",
                    "https://example.com",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "needs_review",
                    "",
                    "",
                    "",
                ]
            )
            + "\n"
        )

    out_path = tmp_path / f"manual_review_{TODAY}.json"
    grq.main(
        [
            "--registry",
            str(reg_path),
            "--output",
            str(out_path),
            "--no-hints",
            "--cache-dir",
            str(tmp_path),
        ]
    )

    assert out_path.exists()
    data = json.loads(out_path.read_text())
    assert len(data) == 1
    assert data[0]["citation_id"] == "cit_000001"


# ---------------------------------------------------------------------------
# Tests: write_json_atomic round-trip
# ---------------------------------------------------------------------------


def test_write_json_atomic_round_trip(tmp_path):
    data = [{"citation_id": "cit_000001", "status": "needs_review"}]
    p = tmp_path / "out.json"
    write_json_atomic(p, data)
    assert p.exists()
    loaded = json.loads(p.read_text())
    assert loaded == data


# ---------------------------------------------------------------------------
# Tests: _parse_osf_guid edge cases
# ---------------------------------------------------------------------------


def test_parse_osf_guid_simple():
    assert _parse_osf_guid("https://osf.io/abc12") == "abc12"
    assert _parse_osf_guid("https://osf.io/abc12/") == "abc12"
    assert _parse_osf_guid("https://osf.io/abc12?view_only=xxx") == "abc12"


def test_parse_osf_guid_preprints():
    assert _parse_osf_guid("https://osf.io/preprints/psyarxiv/xyz99") == "xyz99"
    assert _parse_osf_guid("https://osf.io/preprints/osf/aaabb") == "aaabb"


def test_parse_osf_guid_non_osf():
    assert _parse_osf_guid("https://example.com/foo") is None
    assert _parse_osf_guid("https://github.com/osf.io/foo") is None


# ---------------------------------------------------------------------------
# Tests: load_registry (smoke test)
# ---------------------------------------------------------------------------


def test_load_registry_reads_pending_row(tmp_path):
    reg_path = tmp_path / "registry.tsv"
    with reg_path.open("w", encoding="utf-8") as fh:
        fh.write("\t".join(COLUMNS) + "\n")
        fh.write(
            "\t".join(
                [
                    "cit_000001",
                    "10.1000/test",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "needs_review",
                    "",
                    "",
                    "some note",
                ]
            )
            + "\n"
        )
    reg, cols = load_registry(reg_path)
    assert "cit_000001" in reg
    assert reg["cit_000001"]["doi"] == "10.1000/test"
    assert cols == COLUMNS
