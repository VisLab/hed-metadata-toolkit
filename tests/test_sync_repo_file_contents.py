"""Tests for github.sync_repo_file_contents module."""

from pathlib import Path
from unittest.mock import MagicMock


from hed_metadata_toolkit.github.sync_repo_file_contents import (
    _load_sha_cache,
    _save_sha_cache,
    _read_participant_ids,
    _find_participant_dir,
    _is_rate_limited,
)


class TestParticipantIdReading:
    """Tests for reading participant IDs from participants.tsv."""

    def test_read_participant_ids_valid_tsv(self, tmp_path):
        """Test reading participant IDs from valid TSV file."""
        tsv_file = tmp_path / "participants.tsv"
        tsv_file.write_text(
            "participant_id\tage\tsex\nsub-01\t25\tM\nsub-02\t30\tF\nsub-03\t28\tM\n"
        )

        result = _read_participant_ids(str(tsv_file))

        assert result == ["sub-01", "sub-02", "sub-03"]

    def test_read_participant_ids_missing_file(self, tmp_path):
        """Test handling of missing participants.tsv file."""
        nonexistent = tmp_path / "participants.tsv"

        result = _read_participant_ids(str(nonexistent))

        assert result == []

    def test_read_participant_ids_no_participant_column(self, tmp_path):
        """Test TSV without participant_id column."""
        tsv_file = tmp_path / "participants.tsv"
        tsv_file.write_text("id\tage\tsex\n001\t25\tM\n002\t30\tF\n")

        result = _read_participant_ids(str(tsv_file))

        assert result == []

    def test_read_participant_ids_handles_whitespace(self, tmp_path):
        """Test that trailing whitespace is stripped."""
        tsv_file = tmp_path / "participants.tsv"
        tsv_file.write_text("participant_id\tage\nsub-01  \t25\n sub-02 \t30\n")

        result = _read_participant_ids(str(tsv_file))

        assert result == ["sub-01", "sub-02"]

    def test_read_participant_ids_skips_empty(self, tmp_path):
        """Test that empty participant_id values are skipped."""
        tsv_file = tmp_path / "participants.tsv"
        tsv_file.write_text("participant_id\tage\nsub-01\t25\n\t30\nsub-02\t28\n")

        result = _read_participant_ids(str(tsv_file))

        assert result == ["sub-01", "sub-02"]

    def test_read_participant_ids_participant_column_anywhere(self, tmp_path):
        """Test that participant_id column can be in any position."""
        tsv_file = tmp_path / "participants.tsv"
        tsv_file.write_text("age\tparticipant_id\tsex\n25\tsub-01\tM\n30\tsub-02\tF\n")

        result = _read_participant_ids(str(tsv_file))

        assert result == ["sub-01", "sub-02"]


class TestFindParticipantDir:
    """Tests for finding participant directory in repo entries."""

    def test_find_first_matching_participant(self):
        """Test finding first participant with matching tree entry."""
        participant_ids = ["sub-01", "sub-02", "sub-03"]
        entries = [
            {"name": "README", "type": "blob"},
            {"name": "sub-02", "type": "tree"},  # Match
            {"name": "sub-03", "type": "tree"},  # Also matches but not first
        ]

        result = _find_participant_dir(participant_ids, entries)

        assert result == "sub-02"

    def test_find_participant_no_match(self):
        """Test when no participant has matching directory."""
        participant_ids = ["sub-01", "sub-02", "sub-03"]
        entries = [
            {"name": "README", "type": "blob"},
            {"name": "derivatives", "type": "tree"},
        ]

        result = _find_participant_dir(participant_ids, entries)

        assert result is None

    def test_find_participant_returns_first_in_order(self):
        """Test that first participant with match is returned."""
        participant_ids = ["sub-01", "sub-02", "sub-03"]
        entries = [
            {"name": "sub-01", "type": "tree"},
            {"name": "sub-02", "type": "tree"},
            {"name": "sub-03", "type": "tree"},
        ]

        result = _find_participant_dir(participant_ids, entries)

        assert result == "sub-01"

    def test_find_participant_ignores_blobs(self):
        """Test that blob entries are ignored."""
        participant_ids = ["sub-01"]
        entries = [
            {"name": "sub-01", "type": "blob"},  # Blob, not tree
            {"name": "sub-01", "type": "tree"},  # Tree
        ]

        result = _find_participant_dir(participant_ids, entries)

        assert result == "sub-01"


class TestRepoFileContentsSchema:
    """Tests for repo_file_contents.json schema."""

    def test_file_contents_output_schema(self):
        """Test that repo_file_contents output follows expected schema."""
        repo_file_contents = {
            "ds000001": {
                "sub-01": {
                    "synced_at": "2026-04-14T12:00:00Z",
                    "entries": [
                        {
                            "path": "func/sub-01_task-rest_bold.nii.gz",
                            "type": "blob",
                            "size": 1048576,
                            "sha": "abc123",
                        },
                        {
                            "path": "func/sub-01_task-rest_events.tsv",
                            "type": "blob",
                            "size": 512,
                            "sha": "def456",
                        },
                    ],
                }
            }
        }

        # Verify structure
        for _repo_name, repo_data in repo_file_contents.items():
            for _participant_id, participant_data in repo_data.items():
                assert "synced_at" in participant_data
                assert "entries" in participant_data
                for entry in participant_data["entries"]:
                    assert "path" in entry
                    assert "type" in entry
                    assert "size" in entry
                    assert "sha" in entry


class TestFileContentsFailuresSchema:
    """Tests for repo_file_contents_failures.json schema."""

    def test_file_contents_failures_schema(self):
        """Test that file failures dict follows expected schema."""
        failures = {
            "ds000001/sub-01/func/sub-01_task-rest_events.tsv": {
                "reason": "timeout",
                "failed_at": "2026-04-14T12:00:00Z",
            },
            "ds000001/sub-02/func/sub-02_bold.nii.gz": {
                "reason": "not_found",
                "failed_at": "2026-04-14T12:00:00Z",
                "skip": True,
            },
        }

        # Verify structure
        for key, failure in failures.items():
            assert isinstance(key, str)
            assert "/" in key  # repo/participant/path format
            assert "reason" in failure
            assert "failed_at" in failure


class TestEventFilesFiltering:
    """Tests for filtering event files (*_events.tsv and *_events.json)."""

    def test_event_file_suffixes(self):
        """Test that only event files are identified correctly."""
        EVENTS_SUFFIXES = ("_events.tsv", "_events.json")

        filenames = [
            ("sub-01_task-rest_events.tsv", True),
            ("sub-01_task-rest_events.json", True),
            ("sub-01_task-rest_bold.nii.gz", False),
            ("sub-01_bold.nii.gz", False),
            ("events.tsv", False),
            ("sub-01_task-rest_eventsrelation.json", False),
        ]

        for filename, should_match in filenames:
            is_event = any(filename.endswith(suffix) for suffix in EVENTS_SUFFIXES)
            assert is_event == should_match, f"Failed for {filename}"


class TestRateLimitDetection:
    """Tests for rate limit detection."""

    def test_is_rate_limited_429(self):
        """Test detection of 429 status."""
        response = MagicMock()
        response.status_code = 429

        assert _is_rate_limited(response) is True

    def test_is_rate_limited_403_exhausted(self):
        """Test detection of 403 with exhausted quota."""
        response = MagicMock()
        response.status_code = 403
        response.headers.get.return_value = "0"

        assert _is_rate_limited(response) is True

    def test_is_not_rate_limited_403_with_quota(self):
        """Test that 403 with remaining quota is not rate limited."""
        response = MagicMock()
        response.status_code = 403
        response.headers.get.return_value = "100"

        assert _is_rate_limited(response) is False


class TestShaCache:
    """Tests for SHA cache in sync_repo_file_contents."""

    def test_save_and_load_sha_cache(self, tmp_path):
        """Test SHA cache save/load cycle."""
        repo_dir = str(tmp_path / "ds000001" / "sub-01")
        Path(repo_dir).mkdir(parents=True)

        cache = {
            "func/sub-01_task-rest_events.tsv": "abc123",
            "func/sub-01_task-rest_events.json": "def456",
        }

        _save_sha_cache(repo_dir, cache)
        loaded = _load_sha_cache(repo_dir)

        assert loaded == cache

    def test_load_sha_cache_not_exists(self, tmp_path):
        """Test loading non-existent SHA cache."""
        repo_dir = str(tmp_path / "ds000001" / "sub-01")
        Path(repo_dir).mkdir(parents=True)

        result = _load_sha_cache(repo_dir)

        assert result == {}


class TestParticipantDirStructure:
    """Tests for participant directory structure."""

    def test_participant_dir_path_structure(self):
        """Test expected participant directory structure."""
        # Expected structure:
        # datasets/dataset_repos/
        #   ds000001/
        #     sub-01/
        #       func/
        #         sub-01_task-rest_events.tsv
        #       anat/
        #         sub-01_T1w.nii.gz

        expected_paths = [
            "func/sub-01_task-rest_events.tsv",
            "func/sub-01_task-rest_events.json",
            "anat/sub-01_T1w.nii.gz",
            "dwi/sub-01_dwi_events.tsv",
        ]

        # Verify event files
        event_suffixes = ("_events.tsv", "_events.json")
        event_files = [
            p for p in expected_paths if any(p.endswith(s) for s in event_suffixes)
        ]

        assert len(event_files) == 3
        assert "sub-01_task-rest_events.tsv" in event_files[0]
