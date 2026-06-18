"""test_extract_summary_nemar.py — .nemar enrichment of the dataset summary.

When a dataset has a local ``.nemar/metadata.json``, extract_summary_info fills
the ``title``, ``modalities`` (comma-joined), and ``links``
(``related_identifiers`` joined by ``; ``) columns from it. Datasets without
that file (e.g. OpenNeuro) are unchanged, and passing no ``datasets_dir`` keeps
the original name-only behavior.

No network; all I/O via tmp_path.

Run:
    pytest tests/test_extract_summary_nemar.py -v
"""

from __future__ import annotations

import json

from hed_metadata_toolkit.dataset_summary import extract_summary_info as esi


def _setup(tmp_path, with_nemar: bool):
    contents = tmp_path / "repo_contents.json"
    contents.write_text(
        json.dumps(
            {
                "nm000105": {
                    "synced_at": "2026-06-15T00:00:00Z",
                    "entries": [
                        {"name": "participants.tsv", "type": "blob"},
                        {"name": "sub-001", "type": "tree"},
                        {"name": "README.md", "type": "blob"},
                        {"name": ".nemar/metadata.json", "type": "blob"},
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    repos = tmp_path / "dataset_repos"
    if with_nemar:
        nd = repos / "nm000105" / ".nemar"
        nd.mkdir(parents=True)
        (nd / "metadata.json").write_text(
            json.dumps(
                {
                    "title": "FRL Discrete Gestures",
                    "modalities": ["emg", "eeg"],
                    "related_identifiers": [
                        {
                            "identifier": "10.1038/s41586-025-09255-w",
                            "identifier_type": "DOI",
                        },
                        {"identifier": "https://nemar.org/x", "identifier_type": "URL"},
                    ],
                }
            ),
            encoding="utf-8",
        )
    else:
        (repos / "nm000105").mkdir(parents=True)
    return contents, repos


def test_nemar_fills_title_modalities_links(tmp_path):
    contents, repos = _setup(tmp_path, with_nemar=True)
    rows = esi.extract_dataset_info(str(contents), datasets_dir=str(repos))
    row = rows[0]
    assert row["title"] == "FRL Discrete Gestures"
    assert row["modalities"] == "emg,eeg"
    assert row["links"] == "10.1038/s41586-025-09255-w; https://nemar.org/x"
    # name-derived fields still computed normally
    assert row["subjs"] == 1
    assert row["readme"] == "yes"


def test_dataset_without_nemar_leaves_columns_blank(tmp_path):
    contents, repos = _setup(tmp_path, with_nemar=False)
    row = esi.extract_dataset_info(str(contents), datasets_dir=str(repos))[0]
    assert row["title"] == ""
    assert row["modalities"] == ""
    assert row["links"] == ""


def test_no_datasets_dir_is_backward_compatible(tmp_path):
    # Even though the .nemar file exists, passing no datasets_dir must not read it.
    contents, repos = _setup(tmp_path, with_nemar=True)
    row = esi.extract_dataset_info(str(contents))[0]
    assert row["title"] == ""
    assert row["modalities"] == ""
    assert row["links"] == ""
