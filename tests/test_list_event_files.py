"""test_list_event_files.py — event-file listing + filter + incremental skip.

No network: `_fetch_recursive_tree` is monkeypatched.

Run:
    pytest tests/test_list_event_files.py -v
"""

from __future__ import annotations

import json

from hed_metadata_toolkit.github import list_event_files as lef


# ---------------------------------------------------------------------------
# Path filter
# ---------------------------------------------------------------------------


def test_is_event_file_root_and_sub_only():
    keep = [
        "task-foo_events.json",  # root
        "task-foo_events.tsv",  # root
        "sub-001/sub-001_events.tsv",  # directly in sub
        "sub-001/eeg/sub-001_task-foo_events.tsv",  # nested under sub
        "sub-001/ses-01/eeg/sub-001_ses-01_task-foo_events.json",  # deep under sub
    ]
    drop = [
        "derivatives/sub-001/sub-001_task-foo_events.tsv",  # other top dir
        "sourcedata/x_events.json",  # other top dir
        "code/x_events.json",  # other top dir
        "stimuli/sub-001_events.tsv",  # other top dir
        "participants.tsv",  # not an events file
        "sub-001/eeg/sub-001_task-foo_eeg.json",  # not an events file
    ]
    for p in keep:
        assert lef.is_event_file(p) is True, p
    for p in drop:
        assert lef.is_event_file(p) is False, p


# ---------------------------------------------------------------------------
# Manifest build + incremental skip
# ---------------------------------------------------------------------------


def _tree(*paths):
    return [{"path": p, "type": "blob", "sha": "x", "size": 1} for p in paths]


def test_incremental_skip_and_filtering(tmp_path, monkeypatch):
    tsv = tmp_path / "datasets.tsv"
    tsv.write_text(
        "name\tupdated_at\n"
        "nm000103\t2026-01-02T00:00:00Z\n"  # has newer manifest -> skip
        "nm000104\t2026-01-02T00:00:00Z\n"  # not in manifest -> fetch
        "ds000001\t2026-01-02T00:00:00Z\n"  # filtered out by prefix nm
        ".github\t2026-01-02T00:00:00Z\n",
        encoding="utf-8",
    )
    out = tmp_path / "event_files.json"
    tsv_out = tmp_path / "event_files.tsv"

    # Pre-seed manifest: nm000103 already synced *after* its updated_at.
    seeded = {"path": "task-old_events.json", "size": 7, "sha": "old"}
    out.write_text(
        json.dumps(
            {
                "nm000103": {
                    "synced_at": "2026-01-03T00:00:00Z",
                    "updated_at": "2026-01-02T00:00:00Z",
                    "event_files": [seeded],
                }
            }
        ),
        encoding="utf-8",
    )

    fetched = []

    def fake_fetch(org, repo, headers):
        fetched.append(repo)
        return _tree(
            "task-foo_events.json",
            "sub-01/eeg/sub-01_task-foo_events.tsv",
            "derivatives/sub-01/sub-01_task-foo_events.tsv",  # must be excluded
            "participants.tsv",
        ), False, None

    monkeypatch.setattr(lef, "_fetch_recursive_tree", fake_fetch)

    manifest = lef.list_event_files(
        tsv_path=str(tsv),
        out_path=str(out),
        token=None,
        organization="nemarDatasets",
        prefix="nm",
        tsv_out_path=str(tsv_out),
    )

    # Only nm000104 was fetched; nm000103 skipped (unchanged), ds*/.github filtered.
    assert fetched == ["nm000104"]
    assert manifest["nm000103"]["event_files"] == [seeded]  # untouched
    assert manifest["nm000104"]["event_files"] == [
        {"path": "sub-01/eeg/sub-01_task-foo_events.tsv", "size": 1, "sha": "x"},
        {"path": "task-foo_events.json", "size": 1, "sha": "x"},
    ]
    # flat TSV written with both repos' files, now incl. size + sha
    rows = tsv_out.read_text(encoding="utf-8").splitlines()
    assert rows[0] == "repo\tpath\tsize\tsha"
    assert "nm000104\tsub-01/eeg/sub-01_task-foo_events.tsv\t1\tx" in rows


def test_force_relists_everything(tmp_path, monkeypatch):
    tsv = tmp_path / "datasets.tsv"
    tsv.write_text(
        "name\tupdated_at\nnm000103\t2026-01-02T00:00:00Z\n", encoding="utf-8"
    )
    out = tmp_path / "event_files.json"
    out.write_text(
        json.dumps(
            {
                "nm000103": {
                    "synced_at": "2999-01-01T00:00:00Z",
                    "updated_at": "x",
                    "event_files": [],
                }
            }
        ),
        encoding="utf-8",
    )

    fetched = []

    def fake_fetch(org, repo, headers):
        fetched.append(repo)
        return _tree("sub-01/sub-01_task-x_events.json"), False, None

    monkeypatch.setattr(lef, "_fetch_recursive_tree", fake_fetch)

    lef.list_event_files(
        tsv_path=str(tsv),
        out_path=str(out),
        token=None,
        organization="nemarDatasets",
        prefix="nm",
        force=True,
        tsv_out_path=None,
    )
    assert fetched == ["nm000103"]  # re-listed despite future synced_at


def test_truncated_flag_recorded(tmp_path, monkeypatch):
    tsv = tmp_path / "datasets.tsv"
    tsv.write_text(
        "name\tupdated_at\nnm000200\t2026-01-02T00:00:00Z\n", encoding="utf-8"
    )
    out = tmp_path / "event_files.json"

    def fake_fetch(org, repo, headers):
        return _tree("sub-01/eeg/sub-01_task-x_events.tsv"), True, None  # truncated

    monkeypatch.setattr(lef, "_fetch_recursive_tree", fake_fetch)

    m = lef.list_event_files(
        tsv_path=str(tsv),
        out_path=str(out),
        token=None,
        organization="nemarDatasets",
        prefix="nm",
        tsv_out_path=None,
    )
    assert m["nm000200"]["truncated"] is True
