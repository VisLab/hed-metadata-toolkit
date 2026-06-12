#!/usr/bin/env python3
"""
Build a README summary corpus from a directory of dataset folders.

For each **immediate** subdirectory of ``--repos-dir`` that contains a
``README.md``, ``README``, or ``README.txt`` (checked in that order, top level
only — no deeper recursion), extract a lightweight summary (lead text, section
headers, a few key bullet points, content length) and write the collection as a
JSON list to ``--output``.

Shared toolkit tool (moved here from openneuro-metadata). Like the other
``hed-*`` commands it resolves paths relative to the current working directory,
so run it from the consumer repo's root. Every path is overridable.

Usage:
    hed-extract-readme-summaries [--repos-dir DIR] [--output FILE] [--dirprefix PREFIX]
    python -m hed_metadata_toolkit.dataset_summary.extract_readme_summaries ...
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

DEFAULT_REPOS_DIR = "datasets/dataset_repos"
DEFAULT_OUTPUT = "datasets/dataset_summaries/readme_summaries.json"
README_CANDIDATES = ("README.md", "README", "README.txt")


def extract_readme_info(filepath: Path) -> dict:
    """Parse a single README into a lightweight summary dict.

    Extract structured metadata from a README file including section headers,
    summary text, key bullet points, and full content. Returns a dictionary
    with parsed information for further processing or storage.

    Parameters:
        filepath: Path to the README file to parse.

    Returns:
        Dictionary with keys: summary (str), sections (list[str]),
        key_information (list[str]), content_length (int), and
        content (str) containing the full file contents.
    """
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    lines = content.split("\n")

    sections: list[str] = []
    summary_lines: list[str] = []
    key_information: list[str] = []

    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue

        # Is the next line an underline (==== or ----)?
        is_underlined_header = False
        if i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            if len(next_line) >= 3 and set(next_line).issubset({"=", "-", "*"}):
                if line and not set(line).issubset({"=", "-", "*"}):
                    is_underlined_header = True

        if (
            re.match(r"^(#+)\s", line)
            or re.match(r"^[A-Z0-9\s]+:$", line)
            or re.match(r"^[A-Z0-9\s_]{3,}$", line)
            or is_underlined_header
        ):
            header_text = line.lstrip("# \t").strip(":")
            if header_text and not set(header_text).issubset({"=", "-", "*"}):
                if header_text not in sections:
                    sections.append(header_text)
        elif line.startswith("- ") or line.startswith("* "):
            if len(key_information) < 5:  # up to 5 list items as key info
                key_information.append(line.lstrip("- *"))
        elif len(summary_lines) < 3 and not line.startswith("#"):
            summary_lines.append(line)

    summary = " ".join(summary_lines[:3]) if summary_lines else ""

    return {
        "summary": summary,
        "sections": sections,
        "key_information": key_information,
        "content_length": len(content),
        "content": content,
    }


def find_readme(folder: Path) -> "Path | None":
    """Return the first matching top-level README in *folder*, else None.

    Search for README files in order of preference: README.md, README, README.txt.
    Only checks the top level of the folder; does not recurse into subdirectories.

    Parameters:
        folder: Path to the directory to search for README files.

    Returns:
        Path object for the first matching README file found, or None if no
        README file exists at the top level of the folder.
    """
    for candidate in README_CANDIDATES:
        target = folder / candidate
        if target.is_file():
            return target
    return None


def build_corpus(repos_dir: Path, dirprefix: str = "") -> list[dict]:
    """Summarize every immediate subdirectory of *repos_dir* that has a README.

    Iterate through subdirectories of repos_dir, find READMEs, extract summaries,
    and compile them into a list. Each entry includes the summary, dataset name,
    and README filename.

    Parameters:
        repos_dir: Path to the directory containing dataset subdirectories.
        dirprefix: Optional prefix filter; only process subdirectories whose
                   name starts with this prefix (default: empty string, all
                   subdirectories processed).

    Returns:
        List of dictionaries, each containing README summary data and metadata
        for a single dataset.
    """
    results: list[dict] = []
    for folder in sorted(p for p in repos_dir.iterdir() if p.is_dir()):
        if dirprefix and not folder.name.startswith(dirprefix):
            continue
        readme_path = find_readme(folder)
        if readme_path is None:
            continue
        info = extract_readme_info(readme_path)
        info["dataset"] = folder.name
        info["file_name"] = readme_path.name
        results.append(info)
    return results


def parse_args(argv: "list[str] | None" = None) -> argparse.Namespace:
    """Parse command-line arguments for the README extraction tool.

    Parameters:
        argv: List of command-line arguments to parse. If None, uses sys.argv.

    Returns:
        Namespace object containing parsed arguments: repos_dir, output, and
        dirprefix.
    """
    parser = argparse.ArgumentParser(
        description="Summarize the top-level README of each subdirectory of a "
        "datasets directory into a JSON corpus.",
    )
    parser.add_argument(
        "--repos-dir",
        default=DEFAULT_REPOS_DIR,
        help=f"Directory whose immediate subdirectories are scanned "
        f"(default: {DEFAULT_REPOS_DIR}).",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Output JSON file (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--dirprefix",
        default="",
        help="Optional: only process subdirectories whose name starts with this "
        "prefix (e.g. 'ds'). Default: process all subdirectories.",
    )
    return parser.parse_args(argv)


def main(argv: "list[str] | None" = None) -> int:
    """Main entry point for the README extraction tool.

    Parse arguments, scan the repos directory for READMEs, extract summaries,
    and write the corpus to a JSON file.

    Parameters:
        argv: List of command-line arguments. If None, uses sys.argv.

    Returns:
        Exit code: 0 on success, 1 if repos directory not found.
    """
    args = parse_args(argv)

    repos_dir = Path(args.repos_dir).resolve()
    output = Path(args.output).resolve()

    if not repos_dir.is_dir():
        print(f"ERROR: repos directory not found: {repos_dir}")
        return 1

    results = build_corpus(repos_dir, dirprefix=args.dirprefix)

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"Processed {len(results)} README files and wrote to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
