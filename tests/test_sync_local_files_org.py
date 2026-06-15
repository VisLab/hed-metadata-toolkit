"""test_sync_local_files_org.py — regression guard for org threading.

Bug (2026-06-15): `sync_all` accepted an `organization` argument but did not
pass it to the per-repo `sync_repo(...)` call, which then defaulted to
`OpenNeuroDatasets`. Every non-OpenNeuro download therefore hit the wrong org
and 404'd. This test asserts the organization reaches the downloader.

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
                    "entries": [
                        {"name": "README.md", "type": "blob", "size": 10, "sha": "abc"},
                        {
                            "name": ".nemar/metadata.json",
                            "type": "blob",
                            "size": 5,
                            "sha": "def",
                        },
                    ]
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
    assert set(seen_orgs) == {"nemarDatasets"}, (
        f"organization not threaded to downloader: saw {set(seen_orgs)}"
    )
