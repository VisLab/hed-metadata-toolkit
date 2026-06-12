#!/usr/bin/env python3
"""
Convert citation PDFs to Markdown by invoking ``marker_single`` for each PDF
that does not yet have a corresponding directory in the Markdown output tree.

Shared toolkit tool (moved here from openneuro-metadata).  Like the other
``hed-*`` pipeline commands it resolves paths relative to the **current working
directory** — run it from the consumer repo's root.  Both directories are
overridable so repos with a different layout can point it elsewhere.

Behavior:
- For each ``<name>.pdf`` in ``--pdf-dir``:
  - If ``<md-dir>/<name>`` already exists as a directory, skip.
  - Else run: ``marker_single <name>.pdf --output_dir <md-dir>/<name>``
  - After a successful conversion, move ``<name>_meta.json`` from the nested
    output directory (``<md-dir>/<name>/<name>/``) up into ``<md-dir>/<name>/``.

Requires the ``marker_single`` CLI on ``PATH`` (install the toolkit's ``pdf``
extra: ``pip install 'hed-metadata-toolkit[pdf]'``).

Usage:
    hed-convert-pdfs [--pdf-dir DIR] [--md-dir DIR] [--dry-run]
    python -m hed_metadata_toolkit.citations.convert_pdfs ...
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Defaults match the documented openneuro/nemar layout: citations live under
# datasets/citations/.  Resolved relative to the current working directory.
DEFAULT_PDF_DIR = "datasets/citations/citation_pdfs"
DEFAULT_MD_DIR = "datasets/citations/citation_mds"


def parse_args(argv: "list[str] | None" = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert PDFs to Markdown via marker_single, skipping any "
        "that already have an output directory.",
    )
    parser.add_argument(
        "--pdf-dir",
        default=DEFAULT_PDF_DIR,
        help=f"Directory of input PDFs (default: {DEFAULT_PDF_DIR}).",
    )
    parser.add_argument(
        "--md-dir",
        default=DEFAULT_MD_DIR,
        help=f"Directory for Markdown output (default: {DEFAULT_MD_DIR}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show what would be done, without running marker_single.",
    )
    return parser.parse_args(argv)


def main(argv: "list[str] | None" = None) -> int:
    args = parse_args(argv)

    pdf_dir = Path(args.pdf_dir).resolve()
    md_dir = Path(args.md_dir).resolve()

    if not pdf_dir.is_dir():
        print(f"ERROR: PDF directory not found: {pdf_dir}", file=sys.stderr)
        return 1

    md_dir.mkdir(parents=True, exist_ok=True)

    # marker_single is happiest run from the directory holding the PDF.
    os.chdir(pdf_dir)

    pdf_files = sorted(
        p for p in Path(".").iterdir() if p.is_file() and p.suffix.lower() == ".pdf"
    )

    if not pdf_files:
        print(f"No PDF files found in {pdf_dir}.")
        return 0

    total = len(pdf_files)
    processed = 0
    skipped = 0
    failures = 0

    for pdf in pdf_files:
        name = pdf.stem
        out_dir_abs = md_dir / name

        if out_dir_abs.is_dir():
            print(f"[skip] {pdf.name} -> {out_dir_abs} (already exists)")
            skipped += 1
            continue

        cmd = ["marker_single", pdf.name, "--output_dir", str(out_dir_abs)]
        if args.dry_run:
            print(f"[dry-run] Would run: {' '.join(cmd)}")
            print(f"[dry-run] Would move meta.json into: {out_dir_abs}")
            processed += 1
            continue

        print(f"[run] {' '.join(cmd)}")
        try:
            subprocess.run(cmd, check=True)
            processed += 1
        except FileNotFoundError:
            print(
                "[fail] marker_single not found on PATH. Install the toolkit's "
                "'pdf' extra: pip install 'hed-metadata-toolkit[pdf]'.",
                file=sys.stderr,
            )
            failures += 1
            continue
        except subprocess.CalledProcessError as e:
            print(
                f"[fail] marker_single failed for {pdf.name} "
                f"(exit code {e.returncode})",
                file=sys.stderr,
            )
            failures += 1
            continue

        # marker_single writes into a nested <name>/ subdir; lift the meta file.
        nested_meta = out_dir_abs / name / f"{name}_meta.json"
        dest_meta = out_dir_abs / f"{name}_meta.json"
        if nested_meta.is_file():
            shutil.move(str(nested_meta), dest_meta)
            print(f"[meta] Moved {nested_meta.name} -> {out_dir_abs}")
        else:
            print(
                f"[warn] meta.json not found at expected path: {nested_meta}",
                file=sys.stderr,
            )

    print(
        f"Done. Total PDFs: {total}, processed: {processed}, "
        f"skipped: {skipped}, failures: {failures}"
    )
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
