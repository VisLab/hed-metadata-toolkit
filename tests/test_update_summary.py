"""test_update_summary.py - hed-update-summary with and without a citations file.

A consumer with no citation registry (nemar-metadata) has no
dataset_citations.tsv. The update must still fill ``title`` and ``HED`` from
each dataset's dataset_description.json and leave ``links`` exactly as the
summary had it. When a citations file is given, ``links`` becomes a count.
An explicitly named citations file that is missing is still an error.

No network; all IO via tmp_path.

Run:
    pytest tests/test_update_summary.py -v
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from hed_metadata_toolkit.dataset_summary import sort_datasets as sd
from hed_metadata_toolkit.dataset_summary import update_summary as us

SUMMARY_COLUMNS = ["name", "subjs", "links", "readme", "events", "title", "tasks", "datatypes", "contact", "notes"]


def _write_consumer(root):
    """Lay out a consumer repo with two datasets, one HED-annotated."""
    summaries = root / "datasets" / "dataset_summaries"
    summaries.mkdir(parents=True)
    rows = [
        ["nm000001", 2, "10.1/abc; https://nemar.org/dataset/nm000001", "README", 1, "", "rest", "eeg", "", ""],
        ["nm000002", 3, "", "README", 0, "Old title", "", "meg", "", ""],
    ]
    pd.DataFrame(rows, columns=SUMMARY_COLUMNS).to_csv(summaries / "dataset_summary.tsv", sep="\t", index=False)

    repos = root / "datasets" / "dataset_repos"
    for name, desc in [
        ("nm000001", {"Name": "Annotated\ndataset", "HEDVersion": "8.4.0"}),
        ("nm000002", {"Name": "Plain dataset"}),
    ]:
        (repos / name).mkdir(parents=True)
        (repos / name / "dataset_description.json").write_text(json.dumps(desc), encoding="utf-8")
    return summaries


def _read(path):
    return pd.read_csv(path, sep="\t", keep_default_na=False).set_index("name")


def test_run_update_without_citations_fills_title_and_hed(tmp_path):
    summaries = _write_consumer(tmp_path)
    out = summaries / "dataset_summary_updated.tsv"

    us.run_update(
        summary_path=summaries / "dataset_summary.tsv",
        citations_path=None,
        datasets_dir=tmp_path / "datasets" / "dataset_repos",
        output_path=out,
    )

    df = _read(out)
    assert df.loc["nm000001", "HED"] == "8.4.0"
    assert df.loc["nm000002", "HED"] == ""
    assert df.loc["nm000001", "title"] == "Annotated dataset"
    assert df.loc["nm000002", "title"] == "Plain dataset"
    # links untouched: still the identifier string, not a count
    assert df.loc["nm000001", "links"] == "10.1/abc; https://nemar.org/dataset/nm000001"
    assert df.loc["nm000002", "links"] == ""


def test_run_update_writes_hed_version_list_as_list_string(tmp_path):
    # A list (standard schema plus libraries) is written as the string form of
    # a Python list, the same convention ndx-hed uses; readers parse it back.
    summaries = _write_consumer(tmp_path)
    desc = tmp_path / "datasets" / "dataset_repos" / "nm000002" / "dataset_description.json"
    desc.write_text(
        json.dumps({"Name": "Library dataset", "HEDVersion": ["8.3.0", "sc:score_1.1.0"]}), encoding="utf-8"
    )
    out = summaries / "dataset_summary_updated.tsv"

    us.run_update(
        summary_path=summaries / "dataset_summary.tsv",
        citations_path=None,
        datasets_dir=tmp_path / "datasets" / "dataset_repos",
        output_path=out,
    )

    assert _read(out).loc["nm000002", "HED"] == "['8.3.0', 'sc:score_1.1.0']"


def test_run_update_with_citations_counts_links(tmp_path):
    summaries = _write_consumer(tmp_path)
    # A consumer with a citation registry keeps links blank or numeric; the
    # merge casts it to int, so clear the .nemar identifier strings first.
    summary = pd.read_csv(summaries / "dataset_summary.tsv", sep="\t")
    summary["links"] = None
    summary.to_csv(summaries / "dataset_summary.tsv", sep="\t", index=False)
    citations = summaries / "dataset_citations.tsv"
    pd.DataFrame(
        [["nm000001", "cit_000001", "10.1/abc", ""], ["nm000001", "cit_000002", "10.1/def", ""]],
        columns=["dataset_id", "citation_id", "raw_link", "UnlinkedAck"],
    ).to_csv(citations, sep="\t", index=False)
    out = summaries / "dataset_summary_updated.tsv"

    us.run_update(
        summary_path=summaries / "dataset_summary.tsv",
        citations_path=citations,
        datasets_dir=tmp_path / "datasets" / "dataset_repos",
        output_path=out,
    )

    df = _read(out)
    assert df.loc["nm000001", "links"] == 2
    assert df.loc["nm000002", "links"] == 0
    assert df.loc["nm000001", "HED"] == "8.4.0"


def test_run_update_explicit_missing_citations_raises(tmp_path):
    summaries = _write_consumer(tmp_path)
    with pytest.raises(FileNotFoundError):
        us.run_update(
            summary_path=summaries / "dataset_summary.tsv",
            citations_path=summaries / "absent.tsv",
            datasets_dir=tmp_path / "datasets" / "dataset_repos",
            output_path=summaries / "out.tsv",
        )


def test_cli_without_citations_file_succeeds(tmp_path, monkeypatch, capsys):
    summaries = _write_consumer(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert us.main([]) == 0

    assert "citation merge skipped" in capsys.readouterr().out
    assert _read(summaries / "dataset_summary_updated.tsv").loc["nm000001", "HED"] == "8.4.0"


def test_cli_explicit_missing_citations_fails(tmp_path, monkeypatch):
    _write_consumer(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert us.main(["--citations", "absent.tsv"]) == 1


@pytest.mark.parametrize(
    ("run_update_first", "expect_hed_column"),
    [(True, True), (False, False)],
)
def test_sort_default_input_prefers_updated_summary(tmp_path, monkeypatch, run_update_first, expect_hed_column):
    summaries = _write_consumer(tmp_path)
    monkeypatch.chdir(tmp_path)
    if run_update_first:
        assert us.main([]) == 0

    assert sd.main([]) == 0

    sorted_df = pd.read_csv(summaries / "dataset_summary_sorted.tsv", sep="\t", keep_default_na=False)
    assert ("HED" in sorted_df.columns) is expect_hed_column
    if expect_hed_column:
        assert sorted_df.loc[0, "name"] == "nm000001"


def test_update_writes_provenance_of_the_raw_summary(tmp_path, monkeypatch):
    summaries = _write_consumer(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert us.main([]) == 0

    sidecar = summaries / "dataset_summary_updated.provenance.json"
    record = json.loads(sidecar.read_text(encoding="utf-8"))
    assert record["source"] == "dataset_summary.tsv"
    assert record["source_sha256"] == us.file_sha256(summaries / "dataset_summary.tsv")


def test_sort_default_input_skips_stale_updated_summary(tmp_path, monkeypatch, capsys):
    """A raw summary that changed after the update means extraction was rerun: sort the raw file and warn."""
    summaries = _write_consumer(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert us.main([]) == 0
    raw = summaries / "dataset_summary.tsv"

    # Rerun extraction: a new dataset row lands in the raw summary. No timestamps are
    # involved; the provenance fingerprint no longer matches.
    with raw.open("a", encoding="utf-8") as f:
        f.write("nm000003\t3\t\tREADME\t1\t\t\t\t\t\n")

    assert sd.main([]) == 0
    out = capsys.readouterr().out
    assert "has changed since" in out and "rerun hed-update-summary" in out

    sorted_df = pd.read_csv(summaries / "dataset_summary_sorted.tsv", sep="\t", keep_default_na=False)
    assert "HED" not in sorted_df.columns
    assert "nm000003" in set(sorted_df["name"])
    assert len(sorted_df) == 3


def test_sort_default_input_skips_updated_summary_without_provenance(tmp_path, monkeypatch, capsys):
    """An updated summary with no sidecar (written before provenance existed, or restored alone) is not trusted."""
    summaries = _write_consumer(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert us.main([]) == 0
    (summaries / "dataset_summary_updated.provenance.json").unlink()

    assert sd.main([]) == 0
    out = capsys.readouterr().out
    assert "is missing" in out and "rerun hed-update-summary" in out

    sorted_df = pd.read_csv(summaries / "dataset_summary_sorted.tsv", sep="\t", keep_default_na=False)
    assert "HED" not in sorted_df.columns
    assert len(sorted_df) == 2


@pytest.mark.parametrize("sidecar_text", ["null", "[]", '"abc"', '{"source_sha256": 7}', "{not json"])
def test_sort_default_input_rejects_malformed_provenance(tmp_path, monkeypatch, capsys, sidecar_text):
    """A sidecar that is not a provenance record is untrusted, not a crash: sort the raw file and warn."""
    summaries = _write_consumer(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert us.main([]) == 0
    (summaries / "dataset_summary_updated.provenance.json").write_text(sidecar_text, encoding="utf-8")

    assert sd.main([]) == 0
    out = capsys.readouterr().out
    assert "rerun hed-update-summary" in out

    sorted_df = pd.read_csv(summaries / "dataset_summary_sorted.tsv", sep="\t", keep_default_na=False)
    assert "HED" not in sorted_df.columns
    assert len(sorted_df) == 2


def test_update_failed_save_writes_no_provenance(tmp_path, monkeypatch):
    """If the updated TSV cannot be written, the command fails and no sidecar certifies the old file."""
    summaries = _write_consumer(tmp_path)
    monkeypatch.chdir(tmp_path)
    blocked = summaries / "dataset_summary_updated.tsv"
    blocked.mkdir()  # a directory in the output's place makes the write fail

    assert us.main([]) == 1
    assert not (summaries / "dataset_summary_updated.provenance.json").exists()
