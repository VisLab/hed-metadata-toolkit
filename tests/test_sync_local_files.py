"""Tests for github.sync_local_files module."""

import base64
from pathlib import Path
from unittest.mock import MagicMock


from hed_metadata_toolkit.github.sync_local_files import (
    _load_sha_cache,
    _save_sha_cache,
    _is_rate_limited,
)


class TestShaCache:
    """Tests for SHA cache functions."""

    def test_load_sha_cache_not_exists(self, tmp_path):
        """Test loading SHA cache when file doesn't exist."""
        repo_dir = str(tmp_path / "repo")
        Path(repo_dir).mkdir()

        result = _load_sha_cache(repo_dir)

        assert result == {}

    def test_save_and_load_sha_cache(self, tmp_path):
        """Test saving and loading SHA cache."""
        repo_dir = str(tmp_path / "repo")
        Path(repo_dir).mkdir()

        cache = {
            "README.md": "abc123def456",
            "data.txt": "xyz789uvw012",
        }

        _save_sha_cache(repo_dir, cache)
        loaded = _load_sha_cache(repo_dir)

        assert loaded == cache

    def test_load_sha_cache_corrupt_file(self, tmp_path):
        """Test loading SHA cache with corrupt JSON file."""
        repo_dir = str(tmp_path / "repo")
        Path(repo_dir).mkdir()

        # Write invalid JSON
        cache_file = Path(repo_dir) / ".sha_cache.json"
        cache_file.write_text("{ invalid json")

        result = _load_sha_cache(repo_dir)

        assert result == {}

    def test_sha_cache_persistence(self, tmp_path):
        """Test that SHA cache persists across multiple saves."""
        repo_dir = str(tmp_path / "repo")
        Path(repo_dir).mkdir()

        cache1 = {"file1.txt": "sha1"}
        _save_sha_cache(repo_dir, cache1)

        loaded1 = _load_sha_cache(repo_dir)
        assert loaded1 == cache1

        # Update cache
        cache2 = {"file1.txt": "sha1", "file2.txt": "sha2"}
        _save_sha_cache(repo_dir, cache2)

        loaded2 = _load_sha_cache(repo_dir)
        assert loaded2 == cache2


class TestRateLimitDetection:
    """Tests for rate limit detection."""

    def test_is_rate_limited_429(self):
        """Test detection of 429 rate limit status."""
        response = MagicMock()
        response.status_code = 429

        assert _is_rate_limited(response) is True

    def test_is_rate_limited_403_with_zero_remaining(self):
        """Test detection of 403 with exhausted rate limit."""
        response = MagicMock()
        response.status_code = 403
        response.headers.get.return_value = "0"

        assert _is_rate_limited(response) is True

    def test_is_rate_limited_403_with_remaining(self):
        """Test that 403 with remaining quota is not rate limited."""
        response = MagicMock()
        response.status_code = 403
        response.headers.get.return_value = "50"

        assert _is_rate_limited(response) is False

    def test_is_not_rate_limited_200(self):
        """Test that 200 response is not rate limited."""
        response = MagicMock()
        response.status_code = 200

        assert _is_rate_limited(response) is False

    def test_is_not_rate_limited_404(self):
        """Test that 404 response is not rate limited."""
        response = MagicMock()
        response.status_code = 404

        assert _is_rate_limited(response) is False


class TestRepoContentsSchema:
    """Tests for repo_contents.json schema validation."""

    def test_repo_contents_output_schema(self):
        """Test that repo_contents output follows expected schema."""
        # This documents the expected output schema
        repo_contents = {
            "ds000001": {
                "synced_at": "2026-04-14T12:00:00Z",
                "entries": [
                    {
                        "name": "README",
                        "type": "blob",
                        "size": 2048,
                        "sha": "abc123",
                    },
                    {
                        "name": "dataset_description.json",
                        "type": "blob",
                        "size": 512,
                        "sha": "def456",
                    },
                    {
                        "name": "sub-01",
                        "type": "tree",
                    },
                ],
            }
        }

        # Verify structure
        for _repo_name, repo_data in repo_contents.items():
            assert "synced_at" in repo_data
            assert "entries" in repo_data
            for entry in repo_data["entries"]:
                assert "name" in entry
                assert "type" in entry
                # Blobs should have size and sha
                if entry["type"] == "blob":
                    assert "size" in entry
                    assert "sha" in entry


class TestDownloadFailuresSchema:
    """Tests for download_failures.json schema."""

    def test_download_failures_schema(self):
        """Test that download_failures follows expected schema."""
        download_failures = {
            "ds000001_README.md": {
                "reason": "not_found",
                "failed_at": "2026-04-14T12:00:00Z",
            },
            "ds000001_data.bin": {
                "reason": "timeout",
                "failed_at": "2026-04-14T12:00:00Z",
                "skip": True,
            },
        }

        # Verify structure
        for key, failure in download_failures.items():
            assert isinstance(key, str)
            assert "reason" in failure
            assert "failed_at" in failure


class TestFileDownloadLogic:
    """Tests for file download behavior (conceptual)."""

    def test_incremental_skip_logic(self):
        """Test SHA-based incremental skip logic."""
        # Local file info from sha_cache.json
        sha_cache = {
            "README.md": "abc123",
            "data.txt": "def456",
        }

        # Remote file info from repo_contents.json
        remote_entries = [
            {"name": "README.md", "sha": "abc123"},  # Match: skip
            {"name": "data.txt", "sha": "def456"},  # Match: skip
            {"name": "new_file.txt", "sha": "xyz789"},  # New: download
        ]

        # Determine which files need download
        to_download = []
        for entry in remote_entries:
            if sha_cache.get(entry["name"]) != entry["sha"]:
                to_download.append(entry["name"])

        assert to_download == ["new_file.txt"]

    def test_force_flag_overrides_skip(self):
        """Test that force flag causes re-download."""
        sha_cache = {"README.md": "abc123"}
        remote_entries = [{"name": "README.md", "sha": "abc123"}]
        force = True

        # With force=True, skip SHA comparison and download anyway
        to_download = []
        for entry in remote_entries:
            if force or sha_cache.get(entry["name"]) != entry["sha"]:
                to_download.append(entry["name"])

        assert to_download == ["README.md"]

    def test_max_size_filtering(self):
        """Test that files exceeding max_size are filtered."""
        max_size = 512 * 1024  # 512 KB

        entries = [
            {"name": "small.txt", "size": 1024},
            {"name": "medium.bin", "size": 100 * 1024},
            {"name": "large.iso", "size": 1000 * 1024},  # Exceeds max
        ]

        downloadable = [e for e in entries if e["size"] <= max_size]

        assert len(downloadable) == 2
        assert all(e["name"] != "large.iso" for e in downloadable)


class TestBase64Decoding:
    """Tests for GitHub REST API base64 encoding."""

    def test_base64_content_decoding(self):
        """Test decoding base64-encoded file content from GitHub API."""
        original_content = "Hello, World!"
        encoded = base64.b64encode(original_content.encode("utf-8")).decode("utf-8")

        # Simulate API response
        api_response = {
            "encoding": "base64",
            "content": encoded,
            "sha": "abc123",
        }

        # Decode
        if api_response["encoding"] == "base64":
            decoded_bytes = base64.b64decode(api_response["content"])
            decoded_text = decoded_bytes.decode("utf-8")

        assert decoded_text == original_content

    def test_utf8_content_no_encoding(self):
        """Test handling content without explicit base64 encoding."""
        api_response = {
            "encoding": "utf-8",
            "content": "Plain text content",
            "sha": "def456",
        }

        # Should handle as UTF-8
        if api_response["encoding"] == "base64":
            content_bytes = base64.b64decode(api_response["content"])
        else:
            content_bytes = api_response["content"].encode("utf-8")

        assert content_bytes == b"Plain text content"
