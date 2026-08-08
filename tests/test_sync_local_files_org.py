"""test_sync_local_files_org.py — regression guard for org threading.

`sync_all` must pass its `organization` argument through to every per-repo
`sync_repo(...)` call. If it does not, requests go to the wrong organization,
which 404s per file and leaves empty dataset directories behind instead of
raising - a failure that looks like "the datasets are empty upstream".

No network: `_download_file` is monkeypatched to capture the org it's given.

Run:
    pytest tests/test_sync_local_files_org.py -v
"""

from __future__ import annotations

import json

from hed_metadata_toolkit.github import sync_local_files as slf


def test_sync_all_threads_organization_to_downloader(tmp_path, monkeypatch):
    contents = tmp_path / "repo_contents.json"
    contents.write_text(
        json.dumps(
            {
                "nm000105": {
                    "top_level_files": [
                        {"path": "README.md", "size": 10, "sha": "abc"},
                        {"path": ".nemar/metadata.json", "size": 5, "sha": "def"},
                    ],
                    "subjects": ["sub-01"],
                }
            }
        ),
        encoding="utf-8",
    )

    seen_orgs = []

    def fake_download(org, repo, filename, local_path, expected_sha, headers):
        seen_orgs.append(org)
        return True, expected_sha or "x", None

    monkeypatch.setattr(slf, "_download_file", fake_download)

    slf.sync_all(
        contents_path=str(contents),
        datasets_dir=str(tmp_path / "out"),
        token=None,
        organization="nemarDatasets",
        force=True,
        workers=1,
    )

    assert seen_orgs, "downloader was never called"
    assert set(seen_orgs) == {"nemarDatasets"}, f"organization not threaded to downloader: saw {set(seen_orgs)}"
