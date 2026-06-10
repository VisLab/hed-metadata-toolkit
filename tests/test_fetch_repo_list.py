"""Tests for github.fetch_repo_list module."""

import os
from unittest.mock import MagicMock, patch

import requests

from hed_metadata_toolkit.github.fetch_repo_list import (
    get_github_organization_repositories,
    run_fetch,
)


class TestGetGithubOrganizationRepositories:
    """Tests for get_github_organization_repositories()."""

    @patch("hed_metadata_toolkit.github.fetch_repo_list.requests.get")
    def test_single_page_no_auth(self, mock_get):
        """Test fetching repos without authentication (single page)."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"name": "repo1", "updated_at": "2026-01-01T00:00:00Z"},
            {"name": "repo2", "updated_at": "2026-01-02T00:00:00Z"},
        ]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = get_github_organization_repositories("TestOrg")

        assert len(result) == 2
        assert result[0] == ("repo1", "2026-01-01T00:00:00Z")
        assert result[1] == ("repo2", "2026-01-02T00:00:00Z")
        mock_get.assert_called_once()

    @patch("hed_metadata_toolkit.github.fetch_repo_list.time.sleep")
    @patch("hed_metadata_toolkit.github.fetch_repo_list.requests.get")
    def test_pagination(self, mock_get, mock_sleep):
        """Test pagination with multiple pages."""
        page1_response = MagicMock()
        page1_response.json.return_value = [
            {"name": f"repo{i}", "updated_at": f"2026-01-{i:02d}T00:00:00Z"}
            for i in range(1, 101)  # Full page
        ]
        page1_response.raise_for_status.return_value = None

        page2_response = MagicMock()
        page2_response.json.return_value = [
            {"name": "repo101", "updated_at": "2026-02-01T00:00:00Z"},
            {"name": "repo102", "updated_at": "2026-02-02T00:00:00Z"},
        ]
        page2_response.raise_for_status.return_value = None

        mock_get.side_effect = [page1_response, page2_response]

        result = get_github_organization_repositories("TestOrg")

        assert len(result) == 102
        assert result[0][0] == "repo1"
        assert result[101][0] == "repo102"
        # Should have slept after first page
        mock_sleep.assert_called()

    @patch("hed_metadata_toolkit.github.fetch_repo_list.requests.get")
    def test_with_token(self, mock_get):
        """Test authentication with GitHub token."""
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        get_github_organization_repositories("TestOrg", token="test-token-123")

        # Check that Authorization header was set
        call_args = mock_get.call_args
        headers = call_args.kwargs["headers"]
        assert "Authorization" in headers
        assert headers["Authorization"] == "token test-token-123"

    @patch("hed_metadata_toolkit.github.fetch_repo_list.requests.get")
    def test_organization_not_found(self, mock_get):
        """Test handling of 404 response."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response
        )
        mock_get.return_value = mock_response

        result = get_github_organization_repositories("NonExistentOrg")

        assert result == []

    @patch("hed_metadata_toolkit.github.fetch_repo_list.requests.get")
    def test_network_error(self, mock_get):
        """Test handling of network errors."""
        mock_get.side_effect = requests.exceptions.RequestException("Connection failed")

        result = get_github_organization_repositories("TestOrg")

        assert result == []

    @patch("hed_metadata_toolkit.github.fetch_repo_list.requests.get")
    def test_empty_organization(self, mock_get):
        """Test organization with no repositories."""
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = get_github_organization_repositories("EmptyOrg")

        assert result == []


class TestRunFetch:
    """Tests for run_fetch() library entry point."""

    @patch.dict(os.environ, {}, clear=True)
    @patch("hed_metadata_toolkit.github.fetch_repo_list.get_github_organization_repositories")
    def test_run_fetch_success(self, mock_get_repos, tmp_path):
        """Test successful fetch and TSV writing."""
        mock_get_repos.return_value = [
            ("repo1", "2026-01-01T00:00:00Z"),
            ("repo2", "2026-01-02T00:00:00Z"),
        ]
        output_path = tmp_path / "output.tsv"

        result = run_fetch(org_name="TestOrg", output_path=output_path)

        assert result == 2
        assert output_path.exists()

        # Read TSV file without pandas
        with open(output_path, "r") as f:
            lines = f.readlines()

        assert len(lines) == 3  # header + 2 data rows
        header = lines[0].strip().split("\t")
        assert header == ["name", "updated_at"]

        row1 = lines[1].strip().split("\t")
        assert row1[0] == "repo1"
        assert row1[1] == "2026-01-01T00:00:00Z"

    @patch.dict(os.environ, {}, clear=True)
    @patch("hed_metadata_toolkit.github.fetch_repo_list.get_github_organization_repositories")
    def test_run_fetch_empty_result(self, mock_get_repos, tmp_path):
        """Test handling of empty organization."""
        mock_get_repos.return_value = []
        output_path = tmp_path / "output.tsv"

        result = run_fetch(org_name="EmptyOrg", output_path=output_path)

        assert result == 0
        assert not output_path.exists()

    @patch.dict(os.environ, {"GITHUB_TOKEN": "env-token"}, clear=True)
    @patch("hed_metadata_toolkit.github.fetch_repo_list.get_github_organization_repositories")
    def test_run_fetch_uses_env_token(self, mock_get_repos, tmp_path):
        """Test that token falls back to $GITHUB_TOKEN."""
        mock_get_repos.return_value = [
            ("repo1", "2026-01-01T00:00:00Z"),
        ]
        output_path = tmp_path / "output.tsv"

        run_fetch(org_name="TestOrg", output_path=output_path, token=None)

        # Verify env token was passed
        mock_get_repos.assert_called_once_with("TestOrg", token="env-token")

    @patch.dict(os.environ, {"GITHUB_TOKEN": "env-token"}, clear=True)
    @patch("hed_metadata_toolkit.github.fetch_repo_list.get_github_organization_repositories")
    def test_run_fetch_explicit_token_overrides_env(self, mock_get_repos, tmp_path):
        """Test that explicit token overrides $GITHUB_TOKEN."""
        mock_get_repos.return_value = [
            ("repo1", "2026-01-01T00:00:00Z"),
        ]
        output_path = tmp_path / "output.tsv"

        run_fetch(org_name="TestOrg", output_path=output_path, token="explicit-token")

        # Verify explicit token was used
        mock_get_repos.assert_called_once_with("TestOrg", token="explicit-token")

    @patch("hed_metadata_toolkit.github.fetch_repo_list.get_github_organization_repositories")
    def test_run_fetch_creates_parent_directory(self, mock_get_repos, tmp_path):
        """Test that parent directories are created."""
        mock_get_repos.return_value = [
            ("repo1", "2026-01-01T00:00:00Z"),
        ]
        output_path = tmp_path / "subdir" / "deeper" / "output.tsv"

        run_fetch(org_name="TestOrg", output_path=output_path)

        assert output_path.exists()
        assert output_path.parent.exists()
