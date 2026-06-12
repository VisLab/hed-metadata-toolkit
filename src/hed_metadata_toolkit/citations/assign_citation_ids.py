"""assign_citation_ids.py — Idempotent, permanent citation-ID assignment.

Reads the citation registry and the mapping file (dataset_citations.tsv),
assigns the next free cit_###### to any raw link whose canonical key is not
yet in the registry, and writes both files back.

Re-running on an already-complete registry is a no-op (zero new assignments).

Run from the repo root:
    python src/assign_citation_ids.py             # dry-run (default)
    python src/assign_citation_ids.py --dry-run   # explicit dry-run
    python src/assign_citation_ids.py --write-back

Spec: .status/citation_id_design_v2.md §3
      .status/instructions/phase2_citation_redesign.md (Session 2C)
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path

from hed_metadata_toolkit.citation_normalize import (
    canonicalize_url,
    extract_doi,
    is_junk_link,
    load_skip_list,
)


REGISTRY_COLUMNS = [
    "citation_id",
    "doi",
    "url",
    "source_link",
    "pub_id",
    "first_author_family",
    "year",
    "title",
    "status",
    "metadata_source",
    "verified_on",
    "notes",
]

MAPPING_COLUMNS = ["dataset_id", "citation_id", "raw_link", "UnlinkedAck"]

_CIT_ID_RE = re.compile(r"^cit_(\d+)$")


def cit_id_num(cit_id: str) -> int:
    m = _CIT_ID_RE.match(cit_id)
    if not m:
        raise ValueError(f"unexpected citation_id: {cit_id!r}")
    return int(m.group(1))


def make_cit_id(n: int) -> str:
    return f"cit_{n:06d}"


def canonical_key(raw_link: str) -> tuple[str, str] | None:
    """Return (kind, canonical_value) or None for empty links."""
    s = raw_link.strip() if raw_link else ""
    if not s:
        return None
    doi = extract_doi(s)
    if doi:
        return ("doi", doi)
    return ("url", canonicalize_url(s))


def read_tsv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        return list(reader)


def write_tsv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=columns,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})


def assign(
    registry_path: Path,
    citations_path: Path,
    skip_list_path: Path,
) -> tuple[list[dict], list[dict], int]:
    """Run assignment in memory.

    Returns (registry_rows, mapping_rows, new_count).
    new_count == 0 means the run was a no-op (idempotent).
    """
    skip_patterns = load_skip_list(skip_list_path)
    registry_rows = read_tsv(registry_path)
    mapping_rows = read_tsv(citations_path)

    # Build lookup: canonical_key → index in registry_rows.
    # Primary key: ("doi", doi) when doi is set; ("url", url) otherwise.
    # Secondary key: the source_link canonical URL, added when Phase 2D has
    # since resolved a DOI for a previously URL-only entry.  This means the
    # primary key is now a DOI key, but the mapping still holds the original
    # raw URL.  Without the secondary index those rows would appear as new
    # and receive duplicate cit_######.
    key_to_idx: dict[tuple[str, str], int] = {}
    for idx, row in enumerate(registry_rows):
        doi = (row.get("doi") or "").strip()
        url = (row.get("url") or "").strip()
        source_link = (row.get("source_link") or "").strip()
        if doi:
            key_to_idx[("doi", doi)] = idx
        elif url:
            key_to_idx[("url", url)] = idx
        # Secondary: canonical URL of source_link (only when source_link is
        # a URL, i.e. extract_doi returns None for it).
        if source_link and not extract_doi(source_link):
            secondary = ("url", canonicalize_url(source_link))
            if secondary not in key_to_idx:
                key_to_idx[secondary] = idx

    # Tertiary fallback: if canonical-key lookup fails, honour an already-
    # assigned citation_id in the mapping row (migration output is trusted).
    # This catches entries where Phase 2D added a DOI but cleared source_link,
    # so neither the primary key (doi) nor the secondary key (source_link URL)
    # can be recovered from the registry row alone.
    valid_cit_ids: set[str] = {
        r["citation_id"]
        for r in registry_rows
        if _CIT_ID_RE.match(r.get("citation_id", ""))
    }

    # Determine next free counter from the max existing number
    existing_nums = [cit_id_num(c) for c in valid_cit_ids]
    next_num = (max(existing_nums) + 1) if existing_nums else 1

    new_count = 0

    for map_row in mapping_rows:
        raw = (map_row.get("raw_link") or "").strip()
        if not raw:
            map_row["citation_id"] = ""
            continue

        key = canonical_key(raw)
        if key is None:
            map_row["citation_id"] = ""
            continue

        existing_id = (map_row.get("citation_id") or "").strip()

        if key in key_to_idx:
            # Primary/secondary: found by canonical key (or source_link fallback)
            map_row["citation_id"] = registry_rows[key_to_idx[key]]["citation_id"]
        elif existing_id and existing_id in valid_cit_ids:
            # Tertiary: canonical key lookup failed but mapping already has a
            # valid registry ID from the migration — reuse it without creating
            # a duplicate registry entry.
            map_row["citation_id"] = existing_id
        else:
            # New canonical key — assign next ID
            new_cit_id = make_cit_id(next_num)
            next_num += 1
            new_count += 1

            kind, canonical = key
            if is_junk_link(raw, skip_patterns):
                status = "not_a_citation"
            elif kind == "doi":
                status = "auto"
            else:
                status = "needs_review"

            new_row: dict = {
                "citation_id": new_cit_id,
                "doi": canonical if kind == "doi" else "",
                "url": canonical if kind == "url" else "",
                "source_link": raw,
                "pub_id": "",
                "first_author_family": "",
                "year": "",
                "title": "",
                "status": status,
                "metadata_source": "",
                "verified_on": "",
                "notes": "",
            }
            registry_rows.append(new_row)
            key_to_idx[key] = len(registry_rows) - 1
            map_row["citation_id"] = new_cit_id

    return registry_rows, mapping_rows, new_count


# ---------------------------------------------------------------------------
# Library API
# ---------------------------------------------------------------------------


@dataclass
class AssignmentResult:
    """Outcome of :func:`run_assignment`."""

    registry: list[dict]
    mapping: list[dict]
    new_count: int
    written: bool


def run_assignment(
    *,
    registry_path: Path,
    citations_path: Path,
    skip_list_path: Path,
    write_back: bool = False,
) -> AssignmentResult:
    """Idempotently assign ``cit_######`` IDs to new registry rows.

    Library entry point.  Loads both files, runs the assigner, and
    optionally writes the results back to disk.

    Parameters
    ----------
    registry_path
        Path to ``citation_registry.tsv``.
    citations_path
        Path to ``dataset_citations.tsv`` (the mapping file).
    skip_list_path
        Path to ``citation_skip_list.txt``.
    write_back
        If True, persist the updated registry + mapping to their
        respective paths.
    """
    registry, mapping, new_count = assign(
        registry_path,
        citations_path,
        skip_list_path,
    )
    if write_back:
        write_tsv(registry_path, registry, REGISTRY_COLUMNS)
        write_tsv(citations_path, mapping, MAPPING_COLUMNS)
    return AssignmentResult(
        registry=registry,
        mapping=mapping,
        new_count=new_count,
        written=write_back,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    repo_root = Path.cwd()
    parser = argparse.ArgumentParser(
        description="Idempotent citation-ID assignment.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Show what would happen without writing files (default).",
    )
    group.add_argument(
        "--write-back",
        action="store_true",
        default=False,
        help="Write updated registry and mapping file.",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=(
            repo_root / "datasets" / "dataset_summaries" / "citation_registry.tsv"
        ),
    )
    parser.add_argument(
        "--citations",
        type=Path,
        default=(
            repo_root / "datasets" / "dataset_summaries" / "dataset_citations.tsv"
        ),
    )
    parser.add_argument(
        "--skip-list",
        type=Path,
        default=repo_root / "config" / "citation_skip_list.txt",
    )
    return parser.parse_args()


def main() -> int:
    """Argparse wrapper around :func:`run_assignment`."""
    args = parse_args()

    print(f"Registry:  {args.registry}")
    print(f"Citations: {args.citations}")
    print(f"Skip-list: {args.skip_list}")
    print(f"Mode:      {'WRITE-BACK' if args.write_back else 'DRY-RUN'}")
    print()

    result = run_assignment(
        registry_path=args.registry,
        citations_path=args.citations,
        skip_list_path=args.skip_list,
        write_back=args.write_back,
    )

    print(f"New IDs assigned:  {result.new_count}")
    print(f"Registry rows:     {len(result.registry)}")
    print(f"Mapping rows:      {len(result.mapping)}")

    if result.written:
        print(f"\nWrote {args.registry}")
        print(f"Wrote {args.citations}")
    else:
        print("\nDry-run.  No files written.  Pass --write-back to write.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
