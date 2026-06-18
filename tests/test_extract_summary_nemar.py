"""test_extract_summary_nemar.py — dataset summary against the new schema.

extract_summary_info now derives the ``datatypes`` column from the repository
tree (repo_contents.json ``datatypes`` field), NOT from NEMAR. A dataset's local
``.nemar/metadata.json`` still fills ``title`` and ``links`` (its ``modalities``
field is ignored). Datasets without that file (e.g. OpenNeuro) keep title/links
blank, and passing no ``datasets_dir`` skips the .nemar read entirely.

Also checks legacy ``entries`` schema still parses (datatypes blank there).

No network; all IO via tmp_path.

Run:
    pytest tests/test_extract_summary_nemar.py -v
"""

from __future__ import annotations

import json

from hed_metadata_toolkit.dataset_summary import extract_summary_info as esi


def _new_schema_contents(tmp_path):
    contents = tmp_path / "repo_contents.json"
    contents.write_text(
        json.dumps(
            {
                "nm000105": {
                    "synced_at": "2026-06-15T00:00:00Z",
                    "updated_at": "2026-06-14T00:00:00Z",
                    "truncated": False,
                    "top_level_files": [
                        {"path": "participants.tsv", "size": 8, "sha": "p"},
                        {"path": "README.md", "size": 10, "sha": "r"},
                        {"path": ".nemar/metadata.json", "size": 20, "sha": "n"},
                    ],
                    "subjects": ["sub-001", "sub-002"],
                    "datatypes": ["eeg", "emg"],
                    "event_files": [
                        {
                            "path": "sub-001/eeg/sub-001_task-rest_events.tsv",
                            "size": 30,
                            "sha": "e",
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    return contents


def _make_nemar(tmp_path):
    repos = tmp_path / "dataset_repos"
    nd = repos / "nm000105" / ".nemar"
    nd.mkdir(parents=True)
    (nd / "metadata.json").write_text(
        json.dumps(
            {
                "title": "FRL Discrete Gestures",
                "modalities": ["emg", "eeg"],  # must be ignored
                "related_identifiers": [
                    {"identifier": "10.1038/s41586-025-09255-w", "identifier_type": "DOI"},
                    {"identifier": "https://nemar.org/x", "identifier_type": "URL"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return repos


def test_new_schema_datatypes_from_tree_title_links_from_nemar(tmp_path):
    contents = _new_schema_contents(tmp_path)
    repos = _make_nemar(tmp_path)
    row = esi.extract_dataset_info(str(contents), datasets_dir=str(repos))[0]
    # datatypes come from the tree, not from .nemar's modalities
    assert row["datatypes"] == "eeg,emg"
    assert "modalities" not in row
    # subjects/events/tasks/readme derived from the new schema
    assert row["subjs"] == 2
    assert row["events"] == "yes"
    assert row["readme"] == "yes"
    assert row["tasks"] == "rest"
    # title + links still come from .nemar
    assert row["title"] == "FRL Discrete Gestures"
    assert row["links"] == "10.1038/s41586-025-09255-w; https://nemar.org/x"


def test_new_schema_without_nemar_leaves_title_links_blank(tmp_path):
    contents = _new_schema_contents(tmp_path)
    repos = tmp_path / "dataset_repos"
    (repos / "nm000105").mkdir(parents=True)
    row = esi.extract_dataset_info(str(contents), datasets_dir=str(repos))[0]
    assert row["title"] == ""
    assert row["links"] == ""
    assert row["datatypes"] == "eeg,emg"  # still from the tree


def test_no_datasets_dir_skips_nemar(tmp_path):
    contents = _new_schema_contents(tmp_path)
    _make_nemar(tmp_path)  # present, but must not be read
    row = esi.extract_dataset_info(str(contents))[0]
    assert row["title"] == ""
    assert row["links"] == ""
    assert row["datatypes"] == "eeg,emg"


def test_legacy_entries_schema_still_parses(tmp_path):
    contents = tmp_path / "repo_contents.json"
    contents.write_text(
        json.dumps(
            {
                "ds000001": {
                    "synced_at": "2026-06-01T00:00:00Z",
                    "entries": [
                        {"name": "participants.tsv", "type": "blob"},
                        {"name": "sub-01", "type": "tree"},
                        {"name": "README", "type": "blob"},
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    row = esi.extract_dataset_info(str(contents))[0]
    assert row["subjs"] == 1
    assert row["readme"] == "yes"
    assert row["datatypes"] == ""  # legacy schema has no derived datatypes
