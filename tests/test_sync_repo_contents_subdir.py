"""test_sync_repo_contents_subdir.py — recursive-tree producer behavior.

Covers the 2026-06-15 rewrite of sync_repo_contents (GraphQL shallow listing ->
one recursive git-tree per repo, new per-repo schema):
  - prefix filter (``ds`` for OpenNeuro, ``nm`` for NEMAR)
  - new schema fields: top_level_files / subjects / datatypes / event_files,
    plus synced_at / updated_at / truncated
  - include_subdirs (e.g. ``.nemar``): blobs under it are kept as top-level files
  - incremental skip (synced_at >= updated_at) and ``force``
  - truncated flag recorded

No network: ``_fetch_recursive_tree`` is monkeypatched.

Run:
    pytest tests/test_sync_repo_contents_subdir.py -v
"""

from __future__ import annotations

import json

from hed_metadata_toolkit.github import sync_repo_contents as src


def _blob(path, size=1, sha="s"):
    return {"path": path, "type": "blob", "size": size, "sha": sha}


def _tree(path):
    return {"path": path, "type": "tree"}


def _sample_tree():
    return [
        _blob("dataset_description.json", size=12, sha="dd"),
        _blob("participants.tsv", size=8, sha="pt"),
        _blob(".bidsignore", size=2, sha="bi"),  # hidden root -> excluded
        _tree(".nemar"),
        _blob(".nemar/metadata.json", size=20, sha="nm"),
        _tree("sub-01"),
        _tree("sub-01/eeg"),
        _blob("sub-01/eeg/sub-01_task-foo_events.tsv", size=30, sha="e1"),
        _blob("sub-01/eeg/sub-01_task-foo_eeg.json", size=5, sha="x1"),
        _tree("derivatives"),
        _blob("derivatives/sub-01/sub-01_task-foo_events.tsv", sha="d1"),  # ignored
    ]


def _write_tsv(tmp_path):
    tsv = tmp_path / "datasets.tsv"
    tsv.write_text(
        "name\tupdated_at\n"
        "nm000103\t2026-01-01T00:00:00Z\n"
        "ds000001\t2026-01-01T00:00:00Z\n"
        ".github\t2026-01-01T00:00:00Z\n",
        encoding="utf-8",
    )
    return tsv


def test_prefix_filter_and_new_schema(tmp_path, monkeypatch):
    tsv = _write_tsv(tmp_path)
    out = tmp_path / "repo_contents.json"

    fetched = []

    def fake_fetch(org, repo, headers):
        fetched.append((org, repo))
        return _sample_tree(), False, None

    monkeypatch.setattr(src, "_fetch_recursive_tree", fake_fetch)

    src.sync_repo_contents(
        tsv_path=str(tsv),
        out_path=str(out),
        token=None,
        organization="nemarDatasets",
        prefix="nm",
        include_subdirs=[".nemar"],
    )

    # ds* and .github filtered out by prefix nm
    assert fetched == [("nemarDatasets", "nm000103")]
    data = json.loads(out.read_text(encoding="utf-8"))
    rec = data["nm000103"]
    assert rec["subjects"] == ["sub-01"]
    assert rec["datatypes"] == ["eeg"]
    assert [b["path"] for b in rec["event_files"]] == [
        "sub-01/eeg/sub-01_task-foo_events.tsv"
    ]
    tlf = [b["path"] for b in rec["top_level_files"]]
    assert tlf == [".nemar/metadata.json", "dataset_description.json", "participants.tsv"]
    assert rec["truncated"] is False
    assert rec["synced_at"] and rec["updated_at"] == "2026-01-01T00:00:00Z"


def test_include_subdir_omitted_drops_nemar(tmp_path, monkeypatch):
    tsv = _write_tsv(tmp_path)
    out = tmp_path / "repo_contents.json"
    monkeypatch.setattr(
        src, "_fetch_recursive_tree", lambda o, r, h: (_sample_tree(), False, None)
    )
    src.sync_repo_contents(
        tsv_path=str(tsv),
        out_path=str(out),
        token=None,
        organization="nemarDatasets",
        prefix="nm",
        include_subdirs=None,
    )
    rec = json.loads(out.read_text(encoding="utf-8"))["nm000103"]
    assert ".nemar/metadata.json" not in [b["path"] for b in rec["top_level_files"]]


def test_incremental_skip(tmp_path, monkeypatch):
    tsv = _write_tsv(tmp_path)
    out = tmp_path / "repo_contents.json"
    # Pre-seed: nm000103 already synced after its updated_at -> should be skipped.
    out.write_text(
        json.dumps(
            {
                "nm000103": {
                    "synced_at": "2026-02-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "truncated": False,
                    "top_level_files": [],
                    "subjects": [],
                    "datatypes": [],
                    "event_files": [],
                }
            }
        ),
        encoding="utf-8",
    )

    fetched = []
    monkeypatch.setattr(
        src,
        "_fetch_recursive_tree",
        lambda o, r, h: (fetched.append(r), (_sample_tree(), False, None))[1],
    )
    src.sync_repo_contents(
        tsv_path=str(tsv),
        out_path=str(out),
        token=None,
        organization="nemarDatasets",
        prefix="nm",
    )
    assert fetched == []  # nothing re-fetched


def test_force_refetches(tmp_path, monkeypatch):
    tsv = _write_tsv(tmp_path)
    out = tmp_path / "repo_contents.json"
    out.write_text(
        json.dumps(
            {
                "nm000103": {
                    "synced_at": "2999-01-01T00:00:00Z",
                    "updated_at": "x",
                    "subjects": [],
                }
            }
        ),
        encoding="utf-8",
    )
    fetched = []
    monkeypatch.setattr(
        src,
        "_fetch_recursive_tree",
        lambda o, r, h: (fetched.append(r), (_sample_tree(), False, None))[1],
    )
    src.sync_repo_contents(
        tsv_path=str(tsv),
        out_path=str(out),
        token=None,
        organization="nemarDatasets",
        prefix="nm",
        force=True,
    )
    assert fetched == ["nm000103"]


def test_truncated_flag_recorded(tmp_path, monkeypatch):
    tsv = _write_tsv(tmp_path)
    out = tmp_path / "repo_contents.json"
    monkeypatch.setattr(
        src, "_fetch_recursive_tree", lambda o, r, h: (_sample_tree(), True, None)
    )
    src.sync_repo_contents(
        tsv_path=str(tsv),
        out_path=str(out),
        token=None,
        organization="nemarDatasets",
        prefix="nm",
    )
    rec = json.loads(out.read_text(encoding="utf-8"))["nm000103"]
    assert rec["truncated"] is True


def test_error_recorded_in_failures(tmp_path, monkeypatch):
    tsv = _write_tsv(tmp_path)
    out = tmp_path / "repo_contents.json"
    monkeypatch.setattr(
        src, "_fetch_recursive_tree", lambda o, r, h: (None, False, "not_found")
    )
    src.sync_repo_contents(
        tsv_path=str(tsv),
        out_path=str(out),
        token=None,
        organization="nemarDatasets",
        prefix="nm",
    )
    fail = json.loads(
        (tmp_path / "repo_contents_failures.json").read_text(encoding="utf-8")
    )
    assert fail["nm000103"]["reason"] == "not_found"
