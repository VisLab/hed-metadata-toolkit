"""collect_citations.py — Extract raw citation links from dataset files.

Scans each dataset's README and dataset_description.json for URLs and DOIs,
filters out known non-citation links using config/citation_skip_list.txt, and
writes raw links (no IDs) to datasets/dataset_summaries/dataset_citations.tsv.

ID assignment is handled by assign_citation_ids.py in a separate step.

Run from the repo root:
    python src/collect_citations.py [--dry-run]
    python src/collect_citations.py --write-back
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

from hed_metadata_toolkit.citation_normalize import is_junk_link, load_skip_list

# Default data/config paths live under the current working directory (run the
# command from the consumer repo root); every path is overridable via CLI flags.
_REPO_ROOT = Path.cwd()


MAPPING_COLUMNS = ["dataset_id", "citation_id", "raw_link", "UnlinkedAck"]

_LINK_PATTERNS = [
    re.compile(r'https://[^\s\'"]+', re.IGNORECASE),
    re.compile(r'http://[^\s\'"]+', re.IGNORECASE),
    re.compile(r'www\.[^\s\'"]+', re.IGNORECASE),
    re.compile(r'doi:[^\s\'"]+', re.IGNORECASE),
]


def _clean_link(link: str) -> str:
    if ")" in link:
        link = link[: link.index(")")]
    if link.endswith("."):
        link = link[:-1]
    return link


def _extract_links_from_text(text: str) -> list[str]:
    links = []
    for pat in _LINK_PATTERNS:
        for raw in pat.findall(text):
            links.append(_clean_link(raw))
    return links


def _check_unlinked_ack(json_path: Path) -> str:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        ack = data.get("HowToAcknowledge") or ""
        if not ack.strip():
            return "no"
        if _extract_links_from_text(ack):
            return "no"
        return "yes"
    except Exception:
        return "no"


def _extract_links_from_json(json_path: Path) -> list[str]:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        return _extract_links_from_text(json.dumps(data, ensure_ascii=False))
    except Exception:
        return []


def _extract_links_from_readme(readme_path: Path) -> list[str]:
    try:
        return _extract_links_from_text(readme_path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _find_readme_files(dataset_dir: Path) -> list[Path]:
    try:
        return [p for p in dataset_dir.iterdir() if p.name.lower().startswith("readme")]
    except Exception:
        return []


def collect_dataset_citations(
    datasets_tsv: Path,
    datasets_dir: Path,
    skip_patterns: list[str],
) -> list[dict]:
    """Return mapping-row dicts (dataset_id, citation_id, raw_link, UnlinkedAck).

    citation_id is always empty — assign_citation_ids.py fills it in.
    """
    with datasets_tsv.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        dataset_ids = [r["name"] for r in reader]

    print(f"Loaded {len(dataset_ids)} datasets from {datasets_tsv}")

    rows: list[dict] = []
    for i, dsid in enumerate(dataset_ids, 1):
        print(f"Processing {i}/{len(dataset_ids)}: {dsid}")
        ds_dir = datasets_dir / dsid

        if not ds_dir.is_dir():
            print(f"  Directory not found: {ds_dir}")
            continue

        all_links: list[str] = []
        unlinked_ack = "no"

        json_file = ds_dir / "dataset_description.json"
        if json_file.exists():
            all_links.extend(_extract_links_from_json(json_file))
            unlinked_ack = _check_unlinked_ack(json_file)
            if unlinked_ack == "yes":
                print("  Found unlinked acknowledgment text")

        for readme in _find_readme_files(ds_dir):
            all_links.extend(_extract_links_from_readme(readme))

        # Deduplicate preserving first-seen order, then filter junk
        seen: dict[str, None] = {}
        for lnk in all_links:
            seen.setdefault(lnk, None)
        filtered = [lnk for lnk in seen if not is_junk_link(lnk, skip_patterns)]

        if filtered:
            print(f"  Found {len(filtered)} citation link(s)")
            for lnk in filtered:
                rows.append(
                    {
                        "dataset_id": dsid,
                        "citation_id": "",
                        "raw_link": lnk,
                        "UnlinkedAck": unlinked_ack,
                    }
                )
        else:
            print("  No citation links found")
            if unlinked_ack == "yes":
                rows.append(
                    {
                        "dataset_id": dsid,
                        "citation_id": "",
                        "raw_link": "",
                        "UnlinkedAck": "yes",
                    }
                )

    return rows


def write_citations(rows: list[dict], output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=MAPPING_COLUMNS,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in MAPPING_COLUMNS})
    print(f"Wrote {len(rows)} rows to {output_path}")


# ---------------------------------------------------------------------------
# Library API
# ---------------------------------------------------------------------------


@dataclass
class CollectionResult:
    """Outcome of :func:`run_collection`."""

    rows: list[dict]
    skip_pattern_count: int
    with_links: int
    without_links: int
    output_path: Path | None
    written: bool


def run_collection(
    *,
    datasets_tsv: Path,
    datasets_dir: Path,
    skip_list_path: Path,
    output_path: Path,
    write_back: bool = False,
) -> CollectionResult:
    """Collect raw citation links from dataset files.

    Library entry point.  ``write_back=False`` (the default) leaves
    ``output_path`` untouched; the in-memory rows are still returned
    on the result so callers can inspect or process them further.

    Parameters
    ----------
    datasets_tsv
        Per-dataset TSV (typically ``datasets_ordered.tsv``) that
        lists which dataset directories to scan.
    datasets_dir
        Parent directory holding the ``dsXXXXXX/`` dataset
        sub-directories.
    skip_list_path
        Path to the skip-list file used by
        :func:`citation_normalize.is_junk_link`.
    output_path
        Destination for the citations TSV when ``write_back=True``.
    write_back
        If True, write the collected rows to ``output_path``.
    """
    skip_patterns = load_skip_list(skip_list_path)
    rows = collect_dataset_citations(datasets_tsv, datasets_dir, skip_patterns)
    with_links = sum(1 for r in rows if r["raw_link"])
    if write_back:
        write_citations(rows, output_path)
    return CollectionResult(
        rows=rows,
        skip_pattern_count=len(skip_patterns),
        with_links=with_links,
        without_links=len(rows) - with_links,
        output_path=output_path if write_back else None,
        written=write_back,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract raw citation links from dataset files.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Scan and report without writing the output TSV (default).",
    )
    group.add_argument(
        "--write-back",
        action="store_true",
        default=False,
        help="Write the output TSV after scanning.",
    )
    parser.add_argument(
        "--datasets-tsv",
        type=Path,
        default=(
            _REPO_ROOT / "datasets" / "dataset_summaries" / "datasets_ordered.tsv"
        ),
    )
    parser.add_argument(
        "--datasets-dir",
        type=Path,
        default=_REPO_ROOT / "datasets" / "dataset_repos",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            _REPO_ROOT / "datasets" / "dataset_summaries" / "dataset_citations.tsv"
        ),
    )
    parser.add_argument(
        "--skip-list",
        type=Path,
        default=_REPO_ROOT / "config" / "citation_skip_list.txt",
    )
    return parser.parse_args()


def main() -> int:
    """Argparse wrapper around :func:`run_collection`."""
    args = parse_args()

    print(f"Datasets TSV: {args.datasets_tsv}")
    print(f"Datasets dir: {args.datasets_dir}")
    print(f"Skip-list:    {args.skip_list}")
    print(f"Mode:         {'WRITE-BACK' if args.write_back else 'DRY-RUN'}")
    print()

    result = run_collection(
        datasets_tsv=args.datasets_tsv,
        datasets_dir=args.datasets_dir,
        skip_list_path=args.skip_list,
        output_path=args.output,
        write_back=args.write_back,
    )

    print(f"Loaded {result.skip_pattern_count} skip-list pattern(s).")
    print(f"\nTotal rows collected: {len(result.rows)}")
    print(f"  With links:         {result.with_links}")
    print(f"  Empty/UnlinkedAck:  {result.without_links}")

    if not result.written:
        print(
            f"\nDry-run.  No files written.  Pass --write-back to write {args.output}."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
