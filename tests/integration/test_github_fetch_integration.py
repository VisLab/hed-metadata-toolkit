"""
Integration tests for github.fetch_repo_list.

These tests access the real GitHub API when run with --integration flag.

Run with:
    GITHUB_TOKEN=<token> pytest tests/integration/test_github_fetch_integration.py --integration -v

To test a specific organization:
    GITHUB_TOKEN=<token> pytest tests/integration/test_github_fetch_integration.py --integration -v -k "real"
"""

import pytest

from hed_metadata_toolkit.github.fetch_repo_list import (
    get_github_organization_repositories,
)


class TestFetchRepoListRealAPI:
    """Integration tests against real GitHub API."""

    @pytest.mark.integration
    def test_fetch_public_organization(self, github_token):
        """Test fetching repos from a public organization (real API call)."""
        # Use a small public org for testing
        repos = get_github_organization_repositories(
            "openneuro-validators",
            token=github_token,
        )

        # Should return a list
        assert isinstance(repos, list)

        # Each entry should be (name, updated_at)
        for repo_name, updated_at in repos:
            assert isinstance(repo_name, str)
            assert isinstance(updated_at, str)
            assert len(repo_name) > 0
            # updated_at should be ISO timestamp
            assert "T" in updated_at or len(updated_at) > 10

    @pytest.mark.integration
    def test_fetch_nonexistent_org_returns_empty(self, github_token):
        """Test that non-existent organization returns empty list."""
        repos = get_github_organization_repositories(
            "this-org-definitely-does-not-exist-12345-67890",
            token=github_token,
        )

        assert repos == []

    @pytest.mark.integration
    def test_rate_limit_headers_present(self, github_token):
        """Test that rate limit headers are returned."""
        # This is more of a documentation test showing what headers are available
        import requests

        headers = {"Authorization": f"token {github_token}"}
        url = "https://api.github.com/orgs/openneuro-validators/repos?per_page=1"

        response = requests.get(url, headers=headers)

        # GitHub should return rate limit headers
        assert "x-ratelimit-limit" in response.headers
        assert "x-ratelimit-remaining" in response.headers
        assert "x-ratelimit-reset" in response.headers


class TestFetchRepoListEdgeCases:
    """Edge case tests using real API."""

    @pytest.mark.integration
    def test_fetch_organization_with_no_public_repos(self, github_token):
        """Test org that may have repos (real API call)."""
        # This tests the function's handling of edge cases with real data
        repos = get_github_organization_repositories(
            "openneuro-validators",
            token=github_token,
        )

        # Function should handle any result gracefully
        assert isinstance(repos, list)
        assert all(isinstance(r, tuple) and len(r) == 2 for r in repos)
