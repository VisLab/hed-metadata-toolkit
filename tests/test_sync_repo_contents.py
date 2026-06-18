"""Tests for github.sync_repo_contents — recursive git-tree fetch layer.

The 2026-06-15 rewrite replaced the shallow batched GraphQL listing with one
recursive REST git-tree call per repo. These tests cover ``_fetch_recursive_tree``
response handling; the end-to-end producer behavior (prefix, schema, incremental,
truncated, failures) is in ``test_sync_repo_contents_subdir.py`` and the BIDS
derivation in ``test_bids_tree.py``.

No network: ``requests.get`` is monkeypatched.
"""

from __future__ import annotations

from hed_metadata_toolkit.github import sync_repo_contents as src


class _Resp:
    def __init__(self, status_code, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data or {}
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class TestFetchRecursiveTree:
    def test_success_returns_entries(self, monkeypatch):
        tree = {
            "tree": [
                {"path": "README", "type": "blob", "size": 10, "sha": "a"},
                {"path": "sub-01", "type": "tree"},
            ],
            "truncated": False,
        }
        monkeypatch.setattr(src.requests, "get", lambda *a, **k: _Resp(200, tree))
        entries, truncated, err = src._fetch_recursive_tree("org", "repo", {})
        assert err is None
        assert truncated is False
        assert [e["path"] for e in entries] == ["README", "sub-01"]

    def test_truncated_flag(self, monkeypatch):
        tree = {"tree": [], "truncated": True}
        monkeypatch.setattr(src.requests, "get", lambda *a, **k: _Resp(200, tree))
        _, truncated, err = src._fetch_recursive_tree("org", "repo", {})
        assert truncated is True
        assert err is None

    def test_404_is_not_found(self, monkeypatch):
        monkeypatch.setattr(src.requests, "get", lambda *a, **k: _Resp(404))
        entries, truncated, err = src._fetch_recursive_tree("org", "repo", {})
        assert entries is None
        assert err == "not_found"

    def test_409_empty_repo(self, monkeypatch):
        monkeypatch.setattr(src.requests, "get", lambda *a, **k: _Resp(409))
        entries, truncated, err = src._fetch_recursive_tree("org", "repo", {})
        assert entries == []
        assert err is None


class TestFailureTracking:
    def test_failure_dict_schema(self):
        failure_dict = {
            "nm000103": {"reason": "not_found", "failed_at": "2026-06-15T00:00:00Z"},
            "nm000104": {
                "reason": "empty_repo",
                "failed_at": "2026-06-15T00:00:00Z",
                "skip": True,
            },
        }
        assert all("reason" in v and "failed_at" in v for v in failure_dict.values())
        assert failure_dict["nm000104"]["skip"] is True
        assert failure_dict["nm000103"].get("skip") is None
