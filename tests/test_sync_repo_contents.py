"""Tests for github.sync_repo_contents module."""

from hed_metadata_toolkit.github.sync_repo_contents import (
    _build_graphql_query,
    _parse_graphql_response,
)


class TestBuildGraphqlQuery:
    """Tests for _build_graphql_query()."""

    def test_single_repo(self):
        """Test building query for a single repository."""
        query = _build_graphql_query(["ds000001"], "OpenNeuroDatasets")

        assert 'repository(owner: "OpenNeuroDatasets", name: "ds000001")' in query
        assert "r0:" in query  # alias for first repo
        assert "entries" in query
        assert "type" in query

    def test_multiple_repos(self):
        """Test building query for multiple repositories."""
        repos = ["ds000001", "ds000002", "ds000003"]
        query = _build_graphql_query(repos, "OpenNeuroDatasets")

        for i, repo in enumerate(repos):
            assert (
                f'r{i}: repository(owner: "OpenNeuroDatasets", name: "{repo}")' in query
            )

    def test_query_structure(self):
        """Test that query has valid GraphQL structure."""
        query = _build_graphql_query(["test"], "TestOrg")

        # Should start with { and end with }
        assert query.startswith("{")
        assert query.endswith("}")
        # Should contain required fields
        assert "nameWithOwner" in query
        assert 'object(expression: "HEAD:")' in query
        assert "Tree" in query
        assert "Blob" in query
        assert "byteSize" in query
        assert "oid" in query


class TestParseGraphqlResponse:
    """Tests for _parse_graphql_response()."""

    def test_parse_successful_response(self):
        """Test parsing a successful GraphQL response."""
        payload = {
            "data": {
                "r0": {
                    "nameWithOwner": "OpenNeuroDatasets/ds000001",
                    "object": {
                        "entries": [
                            {
                                "name": "README",
                                "type": "blob",
                                "object": {"byteSize": 1024, "oid": "abc123"},
                            },
                            {
                                "name": "sub-01",
                                "type": "tree",
                            },
                            {
                                "name": ".gitignore",
                                "type": "blob",
                                "object": {"byteSize": 256, "oid": "def456"},
                            },
                        ]
                    },
                }
            }
        }

        result = _parse_graphql_response(payload, ["ds000001"])

        assert "ds000001" in result
        entries = result["ds000001"]
        assert len(entries) == 2  # .gitignore should be skipped

        # Check README
        readme = next(e for e in entries if e["name"] == "README")
        assert readme["type"] == "blob"
        assert readme["size"] == 1024
        assert readme["sha"] == "abc123"

        # Check directory
        subdir = next(e for e in entries if e["name"] == "sub-01")
        assert subdir["type"] == "tree"
        assert "size" not in subdir  # trees don't have size

    def test_parse_multiple_repos(self):
        """Test parsing response for multiple repositories."""
        payload = {
            "data": {
                "r0": {
                    "nameWithOwner": "OpenNeuroDatasets/ds000001",
                    "object": {
                        "entries": [
                            {
                                "name": "README",
                                "type": "blob",
                                "object": {"byteSize": 1024, "oid": "abc123"},
                            }
                        ]
                    },
                },
                "r1": {
                    "nameWithOwner": "OpenNeuroDatasets/ds000002",
                    "object": {
                        "entries": [
                            {
                                "name": "data.txt",
                                "type": "blob",
                                "object": {"byteSize": 2048, "oid": "def456"},
                            }
                        ]
                    },
                },
            }
        }

        result = _parse_graphql_response(payload, ["ds000001", "ds000002"])

        assert len(result) == 2
        assert len(result["ds000001"]) == 1
        assert len(result["ds000002"]) == 1
        assert result["ds000001"][0]["name"] == "README"
        assert result["ds000002"][0]["name"] == "data.txt"

    def test_parse_empty_response(self):
        """Test parsing response with no entries."""
        payload = {
            "data": {
                "r0": {
                    "nameWithOwner": "OpenNeuroDatasets/ds000001",
                    "object": None,  # No tree at HEAD
                }
            }
        }

        result = _parse_graphql_response(payload, ["ds000001"])

        assert "ds000001" in result
        assert result["ds000001"] == []

    def test_parse_missing_repo(self):
        """Test parsing when repo data is missing."""
        payload = {
            "data": {
                # r0 is missing
            }
        }

        result = _parse_graphql_response(payload, ["ds000001"])

        assert "ds000001" in result
        assert result["ds000001"] == []

    def test_skip_hidden_files(self):
        """Test that hidden files (starting with .) are skipped."""
        payload = {
            "data": {
                "r0": {
                    "nameWithOwner": "OpenNeuroDatasets/ds000001",
                    "object": {
                        "entries": [
                            {
                                "name": "README",
                                "type": "blob",
                                "object": {"byteSize": 1024, "oid": "abc123"},
                            },
                            {
                                "name": ".DS_Store",
                                "type": "blob",
                                "object": {"byteSize": 512, "oid": "def456"},
                            },
                            {
                                "name": ".github",
                                "type": "tree",
                            },
                        ]
                    },
                }
            }
        }

        result = _parse_graphql_response(payload, ["ds000001"])

        entries = result["ds000001"]
        assert len(entries) == 1
        assert entries[0]["name"] == "README"


class TestFailureTracking:
    """Tests for failure tracking with repo_contents_failures.json."""

    def test_failure_dict_schema(self, tmp_path):
        """Test that failure dict follows expected schema."""
        # This test documents the expected failure dict structure
        failure_dict = {
            "ds000001": {
                "reason": "empty_entries",
                "failed_at": "2026-04-14T17:41:55Z",
            },
            "ds000002": {
                "reason": "empty_entries",
                "failed_at": "2026-04-14T17:41:55Z",
                "skip": True,
            },
        }

        # Verify structure
        assert all("reason" in v and "failed_at" in v for v in failure_dict.values())
        assert failure_dict["ds000002"]["skip"] is True
        assert failure_dict["ds000001"].get("skip") is None
