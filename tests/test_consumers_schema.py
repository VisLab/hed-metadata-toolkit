"""test_consumers_schema.py — downloaders read both repo_contents.json schemas.

The 2026-06-15 producer rewrite changed repo_contents.json from a flat
``entries`` list to ``top_level_files`` / ``subjects`` / ``datatypes`` /
``event_files``. The downloaders must keep working with both shapes so openneuro
(not yet re-synced) is unaffected.

Run:
    pytest tests/test_consumers_schema.py -v
"""

from __future__ import annotations

from hed_metadata_toolkit.github import sync_local_files as slf
from hed_metadata_toolkit.github import sync_repo_file_contents as srfc


def test_blob_entries_from_new_schema():
    meta = {
        "top_level_files": [
            {"path": "dataset_description.json", "size": 5, "sha": "a"},
            {"path": ".nemar/metadata.json", "size": 9, "sha": "b"},
        ],
        "subjects": ["sub-01"],
    }
    out = slf._repo_blob_entries(meta)
    assert out == [
        {"name": "dataset_description.json", "type": "blob", "size": 5, "sha": "a"},
        {"name": ".nemar/metadata.json", "type": "blob", "size": 9, "sha": "b"},
    ]


def test_blob_entries_from_legacy_entries():
    meta = {
        "entries": [
            {"name": "README", "type": "blob", "size": 1, "sha": "x"},
            {"name": "sub-01", "type": "tree"},
        ]
    }
    out = slf._repo_blob_entries(meta)
    assert {e["name"] for e in out} == {"README", "sub-01"}


def test_tree_entries_from_new_schema():
    meta = {"subjects": ["sub-01", "sub-02"], "top_level_files": []}
    out = srfc._repo_tree_entries(meta)
    assert out == [
        {"name": "sub-01", "type": "tree"},
        {"name": "sub-02", "type": "tree"},
    ]
    # _find_participant_dir consumes exactly this shape
    assert srfc._find_participant_dir(["sub-02"], out) == "sub-02"


def test_tree_entries_from_legacy_entries():
    meta = {
        "entries": [
            {"name": "README", "type": "blob"},
            {"name": "sub-01", "type": "tree"},
        ]
    }
    out = srfc._repo_tree_entries(meta)
    assert srfc._find_participant_dir(["sub-01"], out) == "sub-01"
