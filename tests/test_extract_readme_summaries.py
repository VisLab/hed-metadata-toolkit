"""
test_extract_readme_summaries.py — Tests for extract_readme_summaries.py.

Covers:
  - extract_readme_info: parsing README content into summary, sections, key info
  - find_readme: locating README files in priority order (README.md, README, README.txt)
  - build_corpus: aggregating README summaries from dataset subdirectories
  - parse_args: command-line argument parsing
  - main: full workflow with output JSON generation
  - Returned JSON structure with name, dataset, file_name, summary, sections,
    key_information, content_length, and content fields

Run:
    pytest tests/test_extract_readme_summaries.py -v

All tests use tmp_path; no mocks or network calls.
"""

from __future__ import annotations

import json
from pathlib import Path


from hed_metadata_toolkit.dataset_summary.extract_readme_summaries import (
    build_corpus,
    extract_readme_info,
    find_readme,
    main,
    parse_args,
)


# ---------------------------------------------------------------------------
# extract_readme_info tests
# ---------------------------------------------------------------------------


def test_extract_readme_info_with_markdown_headers(tmp_path: Path) -> None:
    """Extract structured data from README with markdown headers."""
    readme_file = tmp_path / "README.md"
    readme_file.write_text(
        """# My Dataset

This is a summary of the dataset.

## Section One
- Item A
- Item B

## Section Two
Some text here.
- Item C
- Item D
- Item E
- Item F
- Item G
- Item H
"""
    )

    info = extract_readme_info(readme_file)

    assert "summary" in info
    assert "sections" in info
    assert "key_information" in info
    assert "content_length" in info
    assert "content" in info
    assert info["summary"] != ""
    assert "My Dataset" in info["sections"] or "Section One" in info["sections"]
    assert len(info["key_information"]) > 0
    assert info["content_length"] > 0
    assert "My Dataset" in info["content"]


def test_extract_readme_info_with_underline_headers(tmp_path: Path) -> None:
    """Extract data from README with underlined headers (===== or -----)."""
    readme_file = tmp_path / "README"
    readme_file.write_text(
        """Main Title
==========

This is the introduction.

Subsection
----------
Some content here.
- Point 1
- Point 2
"""
    )

    info = extract_readme_info(readme_file)

    assert "Main Title" in info["sections"] or "Subsection" in info["sections"]
    assert len(info["key_information"]) > 0
    assert "Main Title" in info["content"]


def test_extract_readme_info_with_uppercase_headers(tmp_path: Path) -> None:
    """Extract data from README with uppercase headers (implicit section markers)."""
    readme_file = tmp_path / "README.txt"
    readme_file.write_text(
        """OVERVIEW
This is an overview.

DATA FORMATS
The data comes in CSV format.
- Format 1
- Format 2

LICENSE
MIT License
"""
    )

    info = extract_readme_info(readme_file)

    assert "OVERVIEW" in info["sections"] or "DATA FORMATS" in info["sections"]
    assert len(info["key_information"]) > 0


def test_extract_readme_info_captures_full_content(tmp_path: Path) -> None:
    """Verify that content field contains the complete file."""
    readme_file = tmp_path / "README.md"
    original_content = (
        "# Test\n\nFull test content with all lines preserved.\n\n- Item 1\n- Item 2"
    )
    readme_file.write_text(original_content)

    info = extract_readme_info(readme_file)

    assert info["content"] == original_content
    assert info["content_length"] == len(original_content)


def test_extract_readme_info_with_empty_file(tmp_path: Path) -> None:
    """Extract data from empty README."""
    readme_file = tmp_path / "README.md"
    readme_file.write_text("")

    info = extract_readme_info(readme_file)

    assert info["summary"] == ""
    assert info["sections"] == []
    assert info["key_information"] == []
    assert info["content_length"] == 0
    assert info["content"] == ""


def test_extract_readme_info_limits_key_information(tmp_path: Path) -> None:
    """Verify that only up to 5 bullet points are captured as key info."""
    readme_file = tmp_path / "README.md"
    readme_file.write_text(
        """# README

- Point 1
- Point 2
- Point 3
- Point 4
- Point 5
- Point 6
- Point 7
"""
    )

    info = extract_readme_info(readme_file)

    assert len(info["key_information"]) == 5


# ---------------------------------------------------------------------------
# find_readme tests
# ---------------------------------------------------------------------------


def test_find_readme_prefers_readme_md(tmp_path: Path) -> None:
    """find_readme returns README.md if all three exist."""
    (tmp_path / "README.md").write_text("Markdown")
    (tmp_path / "README").write_text("Plain")
    (tmp_path / "README.txt").write_text("Text")

    result = find_readme(tmp_path)

    assert result is not None
    assert result.name == "README.md"


def test_find_readme_falls_back_to_readme(tmp_path: Path) -> None:
    """find_readme returns README if README.md doesn't exist."""
    (tmp_path / "README").write_text("Plain")
    (tmp_path / "README.txt").write_text("Text")

    result = find_readme(tmp_path)

    assert result is not None
    assert result.name == "README"


def test_find_readme_falls_back_to_readme_txt(tmp_path: Path) -> None:
    """find_readme returns README.txt if only it exists."""
    (tmp_path / "README.txt").write_text("Text")

    result = find_readme(tmp_path)

    assert result is not None
    assert result.name == "README.txt"


def test_find_readme_returns_none_when_no_readme(tmp_path: Path) -> None:
    """find_readme returns None if no README exists."""
    (tmp_path / "other.md").write_text("Other")

    result = find_readme(tmp_path)

    assert result is None


def test_find_readme_ignores_nested_readmes(tmp_path: Path) -> None:
    """find_readme only checks top level, not subdirectories."""
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "README.md").write_text("Nested")

    result = find_readme(tmp_path)

    assert result is None


# ---------------------------------------------------------------------------
# build_corpus tests
# ---------------------------------------------------------------------------


def test_build_corpus_processes_multiple_datasets(tmp_path: Path) -> None:
    """build_corpus processes all subdirectories with READMEs."""
    # Create dataset1 with README.md
    ds1 = tmp_path / "dataset1"
    ds1.mkdir()
    (ds1 / "README.md").write_text("# Dataset 1\n\nContent 1.")

    # Create dataset2 with README
    ds2 = tmp_path / "dataset2"
    ds2.mkdir()
    (ds2 / "README").write_text("Dataset 2\n\nContent 2.")

    # Create dataset3 without README (should be skipped)
    ds3 = tmp_path / "dataset3"
    ds3.mkdir()
    (ds3 / "other.txt").write_text("No readme here.")

    corpus = build_corpus(tmp_path)

    assert len(corpus) == 2
    dataset_names = {entry["dataset"] for entry in corpus}
    assert dataset_names == {"dataset1", "dataset2"}


def test_build_corpus_respects_dirprefix(tmp_path: Path) -> None:
    """build_corpus only processes subdirectories matching dirprefix."""
    (tmp_path / "ds_001").mkdir()
    (tmp_path / "ds_001" / "README.md").write_text("DS 001")

    (tmp_path / "ds_002").mkdir()
    (tmp_path / "ds_002" / "README.md").write_text("DS 002")

    (tmp_path / "other_001").mkdir()
    (tmp_path / "other_001" / "README.md").write_text("Other 001")

    corpus = build_corpus(tmp_path, dirprefix="ds_")

    assert len(corpus) == 2
    dataset_names = {entry["dataset"] for entry in corpus}
    assert dataset_names == {"ds_001", "ds_002"}


def test_build_corpus_sorted_output(tmp_path: Path) -> None:
    """build_corpus processes subdirectories in sorted order."""
    (tmp_path / "zebra").mkdir()
    (tmp_path / "zebra" / "README.md").write_text("Z")

    (tmp_path / "apple").mkdir()
    (tmp_path / "apple" / "README.md").write_text("A")

    (tmp_path / "middle").mkdir()
    (tmp_path / "middle" / "README.md").write_text("M")

    corpus = build_corpus(tmp_path)

    names = [entry["dataset"] for entry in corpus]
    assert names == ["apple", "middle", "zebra"]


def test_build_corpus_includes_all_fields(tmp_path: Path) -> None:
    """Each corpus entry includes all required fields."""
    ds = tmp_path / "dataset"
    ds.mkdir()
    (ds / "README.md").write_text("# Test\n\nContent.\n- Item 1\n- Item 2")

    corpus = build_corpus(tmp_path)

    assert len(corpus) == 1
    entry = corpus[0]
    assert "dataset" in entry
    assert "file_name" in entry
    assert "summary" in entry
    assert "sections" in entry
    assert "key_information" in entry
    assert "content_length" in entry
    assert "content" in entry
    assert entry["dataset"] == "dataset"
    assert entry["file_name"] == "README.md"


# ---------------------------------------------------------------------------
# parse_args tests
# ---------------------------------------------------------------------------


def test_parse_args_defaults(tmp_path: Path) -> None:
    """parse_args returns defaults when no arguments provided."""
    args = parse_args([])

    assert args.repos_dir == "datasets/dataset_repos"
    assert args.output == "datasets/dataset_summaries/readme_summaries.json"
    assert args.dirprefix == ""


def test_parse_args_custom_repos_dir(tmp_path: Path) -> None:
    """parse_args accepts custom --repos-dir."""
    args = parse_args(["--repos-dir", str(tmp_path)])

    assert args.repos_dir == str(tmp_path)


def test_parse_args_custom_output(tmp_path: Path) -> None:
    """parse_args accepts custom --output."""
    output_file = tmp_path / "custom.json"
    args = parse_args(["--output", str(output_file)])

    assert args.output == str(output_file)


def test_parse_args_custom_dirprefix(tmp_path: Path) -> None:
    """parse_args accepts custom --dirprefix."""
    args = parse_args(["--dirprefix", "ds_"])

    assert args.dirprefix == "ds_"


def test_parse_args_all_custom(tmp_path: Path) -> None:
    """parse_args accepts all custom arguments."""
    output_file = tmp_path / "out.json"
    args = parse_args(
        [
            "--repos-dir",
            str(tmp_path),
            "--output",
            str(output_file),
            "--dirprefix",
            "test_",
        ]
    )

    assert args.repos_dir == str(tmp_path)
    assert args.output == str(output_file)
    assert args.dirprefix == "test_"


# ---------------------------------------------------------------------------
# main tests
# ---------------------------------------------------------------------------


def test_main_creates_output_json(tmp_path: Path) -> None:
    """main writes corpus to JSON output file."""
    repos_dir = tmp_path / "repos"
    repos_dir.mkdir()
    output_file = tmp_path / "output.json"

    ds = repos_dir / "dataset1"
    ds.mkdir()
    (ds / "README.md").write_text("# Dataset 1\n\nDescription.")

    result = main(
        [
            "--repos-dir",
            str(repos_dir),
            "--output",
            str(output_file),
        ]
    )

    assert result == 0
    assert output_file.exists()
    with open(output_file) as f:
        data = json.load(f)
    assert len(data) == 1
    assert data[0]["dataset"] == "dataset1"


def test_main_returns_1_on_missing_repos_dir(tmp_path: Path) -> None:
    """main returns 1 if repos directory doesn't exist."""
    nonexistent = tmp_path / "nonexistent"
    output_file = tmp_path / "output.json"

    result = main(
        [
            "--repos-dir",
            str(nonexistent),
            "--output",
            str(output_file),
        ]
    )

    assert result == 1


def test_main_creates_output_parent_dirs(tmp_path: Path) -> None:
    """main creates parent directories for output if they don't exist."""
    repos_dir = tmp_path / "repos"
    repos_dir.mkdir()
    output_file = tmp_path / "deeply" / "nested" / "output.json"

    ds = repos_dir / "dataset1"
    ds.mkdir()
    (ds / "README.md").write_text("# Test")

    result = main(
        [
            "--repos-dir",
            str(repos_dir),
            "--output",
            str(output_file),
        ]
    )

    assert result == 0
    assert output_file.exists()


def test_main_output_json_validity(tmp_path: Path) -> None:
    """main produces valid, properly formatted JSON."""
    repos_dir = tmp_path / "repos"
    repos_dir.mkdir()
    output_file = tmp_path / "output.json"

    ds1 = repos_dir / "ds_one"
    ds1.mkdir()
    (ds1 / "README.md").write_text(
        "# Dataset One\n\nDescription.\n- Point A\n- Point B"
    )

    ds2 = repos_dir / "ds_two"
    ds2.mkdir()
    (ds2 / "README").write_text("Dataset Two\n\nMore info.")

    result = main(
        [
            "--repos-dir",
            str(repos_dir),
            "--output",
            str(output_file),
        ]
    )

    assert result == 0

    with open(output_file) as f:
        data = json.load(f)

    assert len(data) == 2
    for entry in data:
        assert "dataset" in entry
        assert "file_name" in entry
        assert "summary" in entry
        assert "sections" in entry
        assert "key_information" in entry
        assert "content_length" in entry
        assert "content" in entry


def test_main_processes_dirprefix(tmp_path: Path) -> None:
    """main respects --dirprefix filter."""
    repos_dir = tmp_path / "repos"
    repos_dir.mkdir()
    output_file = tmp_path / "output.json"

    for prefix in ["ds_", "other_"]:
        for i in range(1, 3):
            ds = repos_dir / f"{prefix}{i:03d}"
            ds.mkdir()
            (ds / "README.md").write_text(f"# {prefix}{i}")

    result = main(
        [
            "--repos-dir",
            str(repos_dir),
            "--output",
            str(output_file),
            "--dirprefix",
            "ds_",
        ]
    )

    assert result == 0

    with open(output_file) as f:
        data = json.load(f)

    assert len(data) == 2
    datasets = {entry["dataset"] for entry in data}
    assert datasets == {"ds_001", "ds_002"}
