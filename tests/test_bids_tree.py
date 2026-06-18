"""test_bids_tree.py — pure BIDS-tree derivation helpers.

No network, no IO. Exercises derive_repo_metadata + is_event_file against a
synthetic recursive git-tree.

Run:
    pytest tests/test_bids_tree.py -v
"""

from __future__ import annotations

from hed_metadata_toolkit.github import bids_tree as bt


def _blob(path, size=1, sha="s"):
    return {"path": path, "type": "blob", "size": size, "sha": sha}


def _tree(path):
    return {"path": path, "type": "tree"}


# ---------------------------------------------------------------------------
# is_event_file
# ---------------------------------------------------------------------------


def test_is_event_file_root_and_sub_only():
    keep = [
        "task-foo_events.json",  # root
        "task-foo_events.tsv",  # root
        "sub-001/sub-001_events.tsv",  # directly in sub
        "sub-001/eeg/sub-001_task-foo_events.tsv",  # nested under sub
        "sub-001/ses-01/eeg/sub-001_ses-01_task-foo_events.json",  # deep
    ]
    drop = [
        "derivatives/sub-001/sub-001_task-foo_events.tsv",  # other top dir
        "sourcedata/x_events.json",
        "code/x_events.json",
        "stimuli/sub-001_events.tsv",
        "participants.tsv",  # not an events file
        "sub-001/eeg/sub-001_task-foo_eeg.json",  # not an events file
    ]
    for p in keep:
        assert bt.is_event_file(p) is True, p
    for p in drop:
        assert bt.is_event_file(p) is False, p


# ---------------------------------------------------------------------------
# derive_repo_metadata
# ---------------------------------------------------------------------------


def _sample_tree():
    return [
        # root files
        _blob("dataset_description.json", size=120, sha="dd"),
        _blob("participants.tsv", size=50, sha="pt"),
        _blob("README", size=10, sha="rd"),
        _blob(".bidsignore", size=5, sha="bi"),  # hidden root -> excluded
        # included subdir (.nemar)
        _tree(".nemar"),
        _blob(".nemar/metadata.json", size=200, sha="nm"),
        # subjects / datatypes / events
        _tree("sub-001"),
        _tree("sub-001/eeg"),
        _blob("sub-001/eeg/sub-001_task-foo_events.tsv", size=300, sha="e1"),
        _blob("sub-001/eeg/sub-001_task-foo_eeg.json", size=80, sha="x1"),
        _tree("sub-002"),
        _tree("sub-002/ses-01"),
        _tree("sub-002/ses-01/emg"),
        _blob("sub-002/ses-01/emg/sub-002_ses-01_task-bar_events.json", sha="e2"),
        # derivatives must be ignored entirely
        _tree("derivatives"),
        _tree("derivatives/sub-001"),
        _blob("derivatives/sub-001/sub-001_task-foo_events.tsv", sha="d1"),
        # a phenotype dir at root must NOT become a datatype
        _tree("phenotype"),
        _blob("phenotype/age.tsv", sha="ph"),
    ]


def test_derive_top_level_files_includes_root_and_subdir():
    out = bt.derive_repo_metadata(_sample_tree(), include_subdirs=[".nemar"])
    paths = [b["path"] for b in out["top_level_files"]]
    assert paths == [
        ".nemar/metadata.json",
        "README",
        "dataset_description.json",
        "participants.tsv",
    ]
    # hidden root entry excluded; phenotype/age.tsv is not top-level
    assert ".bidsignore" not in paths
    assert "phenotype/age.tsv" not in paths
    # blob shape preserved
    dd = next(b for b in out["top_level_files"] if b["path"] == "dataset_description.json")
    assert dd == {"path": "dataset_description.json", "size": 120, "sha": "dd"}


def test_derive_subjects_and_datatypes():
    out = bt.derive_repo_metadata(_sample_tree(), include_subdirs=[".nemar"])
    assert out["subjects"] == ["sub-001", "sub-002"]
    # eeg + emg from under sub-*; derivatives ignored; phenotype excluded; ses- ignored
    assert out["datatypes"] == ["eeg", "emg"]


def test_derive_event_files_root_and_sub_only():
    out = bt.derive_repo_metadata(_sample_tree(), include_subdirs=[".nemar"])
    paths = [b["path"] for b in out["event_files"]]
    assert paths == [
        "sub-001/eeg/sub-001_task-foo_events.tsv",
        "sub-002/ses-01/emg/sub-002_ses-01_task-bar_events.json",
    ]
    # the derivatives events file is excluded
    assert all("derivatives" not in p for p in paths)


def test_no_include_subdirs_excludes_nemar():
    out = bt.derive_repo_metadata(_sample_tree())
    paths = [b["path"] for b in out["top_level_files"]]
    assert ".nemar/metadata.json" not in paths
    assert "README" in paths


def test_datatype_token_in_filename_does_not_false_match():
    tree = [
        _tree("sub-01"),
        _tree("sub-01/anat"),
        _blob("sub-01/anat/sub-01_T1w.nii.gz", sha="a"),
        # 'eeg' only appears as a filename token, never as a directory segment
        _blob("sub-01/anat/sub-01_acq-eeg_T1w.json", sha="b"),
    ]
    out = bt.derive_repo_metadata(tree)
    assert out["datatypes"] == ["anat"]
