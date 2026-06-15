"""test_sync_repo_contents_subdir.py — prefix + include-subdir behavior.

Covers the NEMAR-driven additions to sync_repo_contents:
  - configurable repo-name prefix (``ds`` for OpenNeuro, ``nm`` for NEMAR)
  - ``include_subdirs`` (e.g. ``.nemar``): the GraphQL query fetches the
    subdirectory tree, and its blobs are returned as ``<subdir>/<file>`` entries
    (hidden names inside an included subdir are kept; hidden *root* entries are
    still skipped).
  - backward compatibility: with no subdirs and the default prefix, behavior is
    unchanged.

No network: GraphQL responses are synthesized; the one sync_repo_contents test
monkeypatches fetch_batch and the rate-limit check.

Run:
    pytest tests/test_sync_repo_contents_subdir.py -v
"""

from __future__ import annotations

from hed_metadata_toolkit.github import sync_repo_contents as src


# ---------------------------------------------------------------------------
# GraphQL query builder
# ---------------------------------------------------------------------------


def test_query_without_subdirs_has_no_subdir_alias():
    q = src._build_graphql_query(["nm000103"], "nemarDatasets")
    assert 'object(expression: "HEAD:")' in q
    assert "s0:" not in q
    assert "HEAD:.nemar" not in q


def test_query_with_subdir_includes_head_subdir():
    q = src._build_graphql_query(["nm000103"], "nemarDatasets", [".nemar"])
    assert 's0: object(expression: "HEAD:.nemar")' in q


# ---------------------------------------------------------------------------
# GraphQL response parser
# ---------------------------------------------------------------------------


def _fake_payload() -> dict:
    return {
        "data": {
            "r0": {
                "nameWithOwner": "nemarDatasets/nm000103",
                "object": {
                    "entries": [
                        {
                            "name": "participants.tsv",
                            "type": "blob",
                            "object": {"byteSize": 10, "oid": "aaa"},
                        },
                        {"name": ".github", "type": "tree"},  # hidden root -> skip
                    ]
                },
                "s0": {
                    "entries": [
                        {
                            "name": "meta.json",
                            "type": "blob",
                            "object": {"byteSize": 5, "oid": "bbb"},
                        },
                        {
                            "name": ".keep",  # hidden inside subdir -> kept
                            "type": "blob",
                            "object": {"byteSize": 0, "oid": "ccc"},
                        },
                    ]
                },
            }
        }
    }


def test_parse_includes_subdir_and_skips_hidden_root():
    res = src._parse_graphql_response(_fake_payload(), ["nm000103"], [".nemar"])
    names = {e["name"] for e in res["nm000103"]}
    assert "participants.tsv" in names
    assert ".github" not in names  # hidden root entry skipped
    assert ".nemar/meta.json" in names  # subdir contents included, path-qualified
    assert ".nemar/.keep" in names  # hidden-inside-subdir kept
    meta = next(e for e in res["nm000103"] if e["name"] == ".nemar/meta.json")
    assert meta["type"] == "blob"
    assert meta["sha"] == "bbb"
    assert meta["size"] == 5


def test_parse_without_subdirs_is_unchanged():
    res = src._parse_graphql_response(_fake_payload(), ["nm000103"])
    names = [e["name"] for e in res["nm000103"]]
    # Only the non-hidden root blob; .github skipped; the s0 subdir is ignored.
    assert names == ["participants.tsv"]


# ---------------------------------------------------------------------------
# sync_repo_contents: prefix filter + include_subdirs threading
# ---------------------------------------------------------------------------


def test_prefix_filters_repos_and_threads_subdirs(tmp_path, monkeypatch):
    tsv = tmp_path / "datasets.tsv"
    tsv.write_text(
        "name\tupdated_at\n"
        "nm000103\t2026-01-01T00:00:00Z\n"
        "ds000001\t2026-01-01T00:00:00Z\n"
        ".github\t2026-01-01T00:00:00Z\n",
        encoding="utf-8",
    )
    out = tmp_path / "repo_contents.json"

    captured = {}

    def fake_fetch_batch(batch, org, headers, include_subdirs=None):
        captured["batch"] = list(batch)
        captured["org"] = org
        captured["include_subdirs"] = include_subdirs
        return {n: [{"name": "x", "type": "blob", "size": 1, "sha": "s"}] for n in batch}

    monkeypatch.setattr(src, "fetch_batch", fake_fetch_batch)
    monkeypatch.setattr(src, "_check_rate_limit", lambda headers: None)

    src.sync_repo_contents(
        tsv_path=str(tsv),
        out_path=str(out),
        token=None,
        organization="nemarDatasets",
        prefix="nm",
        include_subdirs=[".nemar"],
    )

    assert captured["batch"] == ["nm000103"]  # ds* and .github filtered out
    assert captured["org"] == "nemarDatasets"
    assert captured["include_subdirs"] == [".nemar"]
    assert out.exists()
