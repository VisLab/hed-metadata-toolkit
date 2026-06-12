"""test_collect_citations.py — Tests for the citation-link collector.

Covers the previously-untested ``collect_citations`` module:
  - link extraction / cleaning helpers
  - HowToAcknowledge ("unlinked ack") detection
  - run_collection end-to-end (dry-run vs write-back, skip-list filtering,
    unlinked-ack rows, missing dataset directories)
  - that the CLI default paths are anchored to the current working directory,
    NOT to the installed package location (regression test for the
    __file__-relative path bug).

No network.  All I/O through tmp_path fixtures.

Run:
    pytest tests/test_collect_citations.py -v
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from hed_metadata_toolkit.citations import collect_citations as cc
from hed_metadata_toolkit.citations.collect_citations import (
    MAPPING_COLUMNS,
    _check_unlinked_ack,
    _clean_link,
    _extract_links_from_text,
    run_collection,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path, datasets: dict, skip_patterns=()) -> tuple:
    """Build a minimal consumer-repo layout under tmp_path.

    datasets maps dsid -> {"desc": <dict or None>, "readme": <str or None>}.
    A dsid whose dir should be *missing* can be passed with value {} and then
    removed by the caller, but the simplest "missing" case lists the id in the
    TSV without creating its directory (see make_missing).
    """
    repos = tmp_path / "datasets" / "dataset_repos"
    summ = tmp_path / "datasets" / "dataset_summaries"
    cfg = tmp_path / "config"
    repos.mkdir(parents=True)
    summ.mkdir(parents=True)
    cfg.mkdir(parents=True)

    tsv = summ / "datasets_ordered.tsv"
    tsv.write_text("name\n" + "\n".join(datasets.keys()) + "\n", encoding="utf-8")

    for dsid, spec in datasets.items():
        if spec is None:  # listed in TSV but directory intentionally absent
            continue
        d = repos / dsid
        d.mkdir()
        if spec.get("desc") is not None:
            (d / "dataset_description.json").write_text(
                json.dumps(spec["desc"]), encoding="utf-8"
            )
        if spec.get("readme") is not None:
            (d / "README.md").write_text(spec["readme"], encoding="utf-8")

    skip = cfg / "citation_skip_list.txt"
    skip.write_text("# skip list\n" + "\n".join(skip_patterns) + "\n", encoding="utf-8")

    out = summ / "dataset_citations.tsv"
    return tsv, repos, skip, out


# ---------------------------------------------------------------------------
# Link helpers
# ---------------------------------------------------------------------------


def test_clean_link_truncates_at_paren():
    assert _clean_link("https://example.com/a)") == "https://example.com/a"


def test_clean_link_strips_trailing_period():
    assert _clean_link("https://example.com/a.") == "https://example.com/a"


def test_clean_link_leaves_clean_link_untouched():
    assert _clean_link("https://example.com/a") == "https://example.com/a"


def test_extract_links_finds_all_url_flavors():
    text = (
        "doi https://doi.org/10.1/x then http://plain.org and www.host.org "
        "plus doi:10.2/zzz end"
    )
    links = _extract_links_from_text(text)
    assert "https://doi.org/10.1/x" in links
    assert "http://plain.org" in links
    assert any(lk.startswith("www.host.org") for lk in links)
    assert any(lk.startswith("doi:10.2/zzz") for lk in links)


# ---------------------------------------------------------------------------
# Unlinked-acknowledgment detection
# ---------------------------------------------------------------------------


def test_unlinked_ack_yes_when_text_without_links(tmp_path: Path):
    p = tmp_path / "dataset_description.json"
    p.write_text(json.dumps({"HowToAcknowledge": "Please cite the Smith Lab."}))
    assert _check_unlinked_ack(p) == "yes"


def test_unlinked_ack_no_when_ack_contains_link(tmp_path: Path):
    p = tmp_path / "dataset_description.json"
    p.write_text(json.dumps({"HowToAcknowledge": "Cite https://doi.org/10.1/x"}))
    assert _check_unlinked_ack(p) == "no"


def test_unlinked_ack_no_when_empty_or_missing(tmp_path: Path):
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"HowToAcknowledge": "   "}))
    assert _check_unlinked_ack(empty) == "no"

    missing = tmp_path / "missing.json"
    missing.write_text(json.dumps({"Name": "ds000001"}))
    assert _check_unlinked_ack(missing) == "no"


# ---------------------------------------------------------------------------
# run_collection
# ---------------------------------------------------------------------------


def test_run_collection_dry_run_does_not_write(tmp_path: Path):
    tsv, repos, skip, out = _make_repo(
        tmp_path,
        {"ds000001": {"desc": {"Name": "x"}, "readme": "see https://doi.org/10.1/x"}},
    )
    res = run_collection(
        datasets_tsv=tsv,
        datasets_dir=repos,
        skip_list_path=skip,
        output_path=out,
        write_back=False,
    )
    assert res.written is False
    assert not out.exists()
    assert res.with_links == 1
    assert any(r["raw_link"] == "https://doi.org/10.1/x" for r in res.rows)


def test_run_collection_write_back_creates_tsv(tmp_path: Path):
    tsv, repos, skip, out = _make_repo(
        tmp_path,
        {"ds000001": {"desc": {"Name": "x"}, "readme": "see https://doi.org/10.1/x"}},
    )
    res = run_collection(
        datasets_tsv=tsv,
        datasets_dir=repos,
        skip_list_path=skip,
        output_path=out,
        write_back=True,
    )
    assert res.written is True
    assert out.exists()
    with out.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        header = next(reader)
        data_rows = list(reader)
    assert header == MAPPING_COLUMNS
    assert len(data_rows) == len(res.rows) == 1


def test_run_collection_skip_list_filters_links(tmp_path: Path):
    tsv, repos, skip, out = _make_repo(
        tmp_path,
        {
            "ds000001": {
                "desc": {"Name": "x"},
                "readme": "code https://github.com/foo/bar paper https://doi.org/10.1/x",
            }
        },
        skip_patterns=["github.com"],
    )
    res = run_collection(
        datasets_tsv=tsv,
        datasets_dir=repos,
        skip_list_path=skip,
        output_path=out,
        write_back=False,
    )
    raw_links = {r["raw_link"] for r in res.rows}
    assert "https://doi.org/10.1/x" in raw_links
    assert not any("github.com" in lk for lk in raw_links)
    assert res.skip_pattern_count == 1


def test_run_collection_emits_unlinked_ack_row(tmp_path: Path):
    tsv, repos, skip, out = _make_repo(
        tmp_path,
        {"ds000001": {"desc": {"HowToAcknowledge": "Please cite the Smith Lab."}}},
    )
    res = run_collection(
        datasets_tsv=tsv,
        datasets_dir=repos,
        skip_list_path=skip,
        output_path=out,
        write_back=False,
    )
    assert res.with_links == 0
    assert len(res.rows) == 1
    row = res.rows[0]
    assert row["dataset_id"] == "ds000001"
    assert row["raw_link"] == ""
    assert row["UnlinkedAck"] == "yes"


def test_run_collection_skips_missing_dataset_dir(tmp_path: Path):
    # ds000002 is listed in the TSV but no directory is created for it.
    tsv, repos, skip, out = _make_repo(
        tmp_path,
        {
            "ds000001": {"desc": {"Name": "x"}, "readme": "https://doi.org/10.1/x"},
            "ds000002": None,
        },
    )
    res = run_collection(
        datasets_tsv=tsv,
        datasets_dir=repos,
        skip_list_path=skip,
        output_path=out,
        write_back=False,
    )
    # No crash; only the existing dataset contributes rows.
    assert {r["dataset_id"] for r in res.rows} == {"ds000001"}


# ---------------------------------------------------------------------------
# Regression: CLI default paths must be cwd-relative, not package-relative
# ---------------------------------------------------------------------------


def test_default_paths_are_not_inside_the_installed_package(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["hed-collect-citations"])
    args = cc.parse_args()
    pkg_dir = Path(cc.__file__).resolve().parent.parent  # .../hed_metadata_toolkit
    for p in (args.datasets_tsv, args.datasets_dir, args.output, args.skip_list):
        assert not Path(p).resolve().is_relative_to(pkg_dir), (
            f"default {p} resolves inside the package; it must be relative to "
            "the current working directory"
        )
