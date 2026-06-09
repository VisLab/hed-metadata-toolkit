#!/usr/bin/env python3
"""
setup_toolkit.py — Populate hed-metadata-toolkit from task-research +
openneuro-metadata.

Copies the canonical source files from the two existing repositories
into this toolkit's ``src/hed_metadata_toolkit/`` package, creates
empty ``__init__.py`` markers, lays down a baseline ``pyproject.toml``,
``README.md``, and ``.gitignore``, and reports a summary.

Run modes
=========

  python .status/setup_toolkit.py             # DRY-RUN (default)
  python .status/setup_toolkit.py --execute   # actually create files
  python .status/setup_toolkit.py --execute --force
                                              # also overwrite existing
                                              # destination files

Idempotent.  Re-running with --execute (without --force) skips any
destination that already exists, so it is safe to re-run after a
partial setup.

Important caveats
=================

  * Files are **copied**, not moved.  task-research and openneuro-
    metadata keep working unmodified.  After tests pass in the new
    toolkit, delete the originals and update their imports in a
    separate, reviewable PR.

  * Imports inside the copied files still reference the OLD module
    names (e.g. ``from cache import ...``).  Those need to be
    rewritten to ``from hed_metadata_toolkit.cache import ...`` once
    the files are in place.  This script intentionally does NOT
    rewrite imports — the maintainer can do that with a single
    ``ruff check --fix`` pass or by hand after looking at the layout.

  * The existing toolkit repo's ``README.md``, ``.gitignore``,
    ``LICENSE`` are left alone unless you pass --force.

Configuration
=============

If your source repos live somewhere other than the defaults below,
edit ``TASK_RESEARCH`` and ``OPENNEURO_META`` at the top of this
file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from shutil import copy2


# ---------------------------------------------------------------------------
# Source / destination roots — edit if your layout differs
# ---------------------------------------------------------------------------

TASK_RESEARCH = Path("H:/Research/task-research")
OPENNEURO_META = Path("I:/RepositoryMetadata/openneuro-metadata")
TOOLKIT = Path(__file__).resolve().parent.parent  # repo root
PKG = TOOLKIT / "src" / "hed_metadata_toolkit"


# ---------------------------------------------------------------------------
# Files to copy:  (source, destination, label)
# ---------------------------------------------------------------------------

COPIES: list[tuple[Path, Path, str]] = [
    # ---- Core shared modules
    (
        TASK_RESEARCH / "Claude-research/code/literature_search/cache.py",
        PKG / "cache.py",
        "task-research (upstream)",
    ),
    (
        TASK_RESEARCH / "Claude-research/code/literature_search/identity.py",
        PKG / "citation_identity.py",
        "task-research identity.py (renamed)",
    ),
    (
        OPENNEURO_META / "src/citation_normalize.py",
        PKG / "citation_normalize.py",
        "openneuro-metadata",
    ),
    # ---- API clients
    # task-research carries the broadest set (pmc/semanticscholar/unpaywall);
    # openneuro-metadata is the source for the OSF client.
    (
        OPENNEURO_META / "src/clients/crossref.py",
        PKG / "clients/crossref.py",
        "openneuro-metadata (mirror of task-research)",
    ),
    (
        TASK_RESEARCH / "Claude-research/code/literature_search/clients/europepmc.py",
        PKG / "clients/europepmc.py",
        "task-research",
    ),
    (
        TASK_RESEARCH / "Claude-research/code/literature_search/clients/openalex.py",
        PKG / "clients/openalex.py",
        "task-research",
    ),
    (
        OPENNEURO_META / "src/clients/osf.py",
        PKG / "clients/osf.py",
        "openneuro-metadata",
    ),
    (
        TASK_RESEARCH / "Claude-research/code/literature_search/clients/pmc.py",
        PKG / "clients/pmc.py",
        "task-research",
    ),
    (
        TASK_RESEARCH
        / "Claude-research/code/literature_search/clients/semanticscholar.py",
        PKG / "clients/semanticscholar.py",
        "task-research",
    ),
    (
        TASK_RESEARCH / "Claude-research/code/literature_search/clients/unpaywall.py",
        PKG / "clients/unpaywall.py",
        "task-research",
    ),
    # ---- GitHub-org pipeline (README Steps 1–4)
    (
        OPENNEURO_META / "src/create_repo_list.py",
        PKG / "github_org/fetch_repo_list.py",
        "openneuro-metadata create_repo_list.py (renamed)",
    ),
    (
        OPENNEURO_META / "src/sync_repo_contents.py",
        PKG / "github_org/sync_repo_contents.py",
        "openneuro-metadata",
    ),
    (
        OPENNEURO_META / "src/sync_local_files.py",
        PKG / "github_org/sync_local_files.py",
        "openneuro-metadata",
    ),
    (
        OPENNEURO_META / "src/sync_repo_file_contents.py",
        PKG / "github_org/sync_repo_file_contents.py",
        "openneuro-metadata",
    ),
    # ---- Dataset summary builders (README Steps 5–7)
    (
        OPENNEURO_META / "src/extract_summary_info.py",
        PKG / "dataset_summary/extract_summary_info.py",
        "openneuro-metadata",
    ),
    (
        OPENNEURO_META / "src/update_summary.py",
        PKG / "dataset_summary/update_summary.py",
        "openneuro-metadata",
    ),
    (
        OPENNEURO_META / "src/sort_datasets.py",
        PKG / "dataset_summary/sort_datasets.py",
        "openneuro-metadata",
    ),
    # ---- Citation pipeline (README Steps 8–9)
    (
        OPENNEURO_META / "src/collect_citations.py",
        PKG / "citations/collect_citations.py",
        "openneuro-metadata",
    ),
    (
        OPENNEURO_META / "src/assign_citation_ids.py",
        PKG / "citations/assign_citation_ids.py",
        "openneuro-metadata",
    ),
    (
        OPENNEURO_META / "src/generate_review_queue.py",
        PKG / "citations/generate_review_queue.py",
        "openneuro-metadata",
    ),
    (
        OPENNEURO_META / "src/apply_manual_fills.py",
        PKG / "citations/apply_manual_fills.py",
        "openneuro-metadata",
    ),
    (
        OPENNEURO_META / "src/enrich_pub_ids.py",
        PKG / "citations/enrich_pub_ids.py",
        "openneuro-metadata",
    ),
    # ---- Tests
    # task-research-side tests for the modules whose canonical source
    # is task-research.
    (
        TASK_RESEARCH / "Claude-research/code/literature_search/test_cache.py",
        TOOLKIT / "tests/test_cache.py",
        "task-research",
    ),
    (
        TASK_RESEARCH / "Claude-research/code/literature_search/test_identity.py",
        TOOLKIT / "tests/test_citation_identity.py",
        "task-research test_identity.py (renamed)",
    ),
    (
        TASK_RESEARCH / "Claude-research/code/literature_search/clients/test_pmc.py",
        TOOLKIT / "tests/test_pmc_client.py",
        "task-research (renamed for clarity)",
    ),
    # openneuro-side tests for everything else.
    (
        OPENNEURO_META / "tests/test_citation_normalize.py",
        TOOLKIT / "tests/test_citation_normalize.py",
        "openneuro-metadata",
    ),
    (
        OPENNEURO_META / "tests/test_citation_identity.py",
        TOOLKIT / "tests/test_citation_identity_pinned.py",
        "openneuro-metadata (renamed — pinned cross-repo determinism checks)",
    ),
    (
        OPENNEURO_META / "tests/test_doi_canon.py",
        TOOLKIT / "tests/test_doi_canon.py",
        "openneuro-metadata",
    ),
    (
        OPENNEURO_META / "tests/test_url_synthesisers.py",
        TOOLKIT / "tests/test_url_synthesisers.py",
        "openneuro-metadata",
    ),
    (
        OPENNEURO_META / "tests/test_osf_client.py",
        TOOLKIT / "tests/test_osf_client.py",
        "openneuro-metadata",
    ),
    (
        OPENNEURO_META / "tests/test_clients_drift.py",
        TOOLKIT / "tests/test_clients_drift.py",
        "openneuro-metadata (can delete after consumer refactor)",
    ),
    (
        OPENNEURO_META / "tests/test_enrich_pub_ids.py",
        TOOLKIT / "tests/test_enrich_pub_ids.py",
        "openneuro-metadata",
    ),
    (
        OPENNEURO_META / "tests/test_apply_manual_fills.py",
        TOOLKIT / "tests/test_apply_manual_fills.py",
        "openneuro-metadata",
    ),
    (
        OPENNEURO_META / "tests/test_apply_manual_fills_atomic.py",
        TOOLKIT / "tests/test_apply_manual_fills_atomic.py",
        "openneuro-metadata",
    ),
    (
        OPENNEURO_META / "tests/test_assign_citation_ids.py",
        TOOLKIT / "tests/test_assign_citation_ids.py",
        "openneuro-metadata",
    ),
    (
        OPENNEURO_META / "tests/test_generate_review_queue.py",
        TOOLKIT / "tests/test_generate_review_queue.py",
        "openneuro-metadata",
    ),
]


# ---------------------------------------------------------------------------
# Empty __init__.py markers
# ---------------------------------------------------------------------------

NEW_INIT_FILES: list[Path] = [
    PKG / "__init__.py",
    PKG / "clients/__init__.py",
    PKG / "github_org/__init__.py",
    PKG / "dataset_summary/__init__.py",
    PKG / "citations/__init__.py",
]


# ---------------------------------------------------------------------------
# Template files
# ---------------------------------------------------------------------------

PYPROJECT_TOML = """\
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "hed-metadata-toolkit"
version = "0.1.0"
description = "Shared toolkit for HED metadata repos: cache, API clients, citation identity, GitHub-org pipelines."
authors = [
    { name = "HED Group", email = "hedannotation@gmail.com" },
]
license = {text = "MIT"}
readme = "README.md"
requires-python = ">=3.10"

dependencies = [
    "requests>=2.28.0",
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "ruff>=0.8.0",
]

[project.scripts]
# CLI entry point — populate src/hed_metadata_toolkit/cli.py with the
# subcommand dispatcher in a follow-up PR.
# hed-metadata = "hed_metadata_toolkit.cli:main"

[tool.setuptools.packages.find]
where = ["src"]
"""


README_MD = """\
# hed-metadata-toolkit

Shared Python library for HED metadata repositories.  Provides:

- Shared API-response cache following the cross-repo convention
  (mirrored from task-research).
- API clients for Crossref, OpenAlex, Europe PMC, OSF, PMC,
  Semantic Scholar, and Unpaywall.
- Citation identity (`pub_id` generation) and URL/DOI canonicalisation.
- GitHub-org dataset pipelines (fetch repo list, sync top-level files,
  sync per-participant event files).
- Dataset summary builders (extract, enrich, sort).
- Citation pipeline (collect, assign IDs, manual review queue, apply
  manual fills, enrich with `pub_id` and metadata).

## Consumer repositories

- `openneuro-metadata` — datasets from the `OpenNeuroDatasets` GitHub org.
- `nemar-metadata` — datasets from the `NemarDatasets` GitHub org.
- `task-research` — cognitive-process / task catalogue (consumes the
  library subset relevant to citation enrichment).

Each consumer holds its own `config.toml`, skip list, and data
directories; all pipeline logic lives here.

## Installation (consumers)

```toml
[project]
dependencies = [
    "hed-metadata-toolkit @ git+https://github.com/hed-standard/hed-metadata-toolkit.git@v0.1.0",
]
```

For local development with edits flowing across repos:

```
pip install -e ../hed-metadata-toolkit
```

## License

MIT — see `LICENSE`.
"""


GITIGNORE = """\
__pycache__/
*.py[cod]
*$py.class
*.egg-info/
.venv/
venv/
.pytest_cache/
.ruff_cache/
.mypy_cache/
build/
dist/
.coverage
htmlcov/
.env
*.bak

# Local cache (use $HED_CACHE_DIR for the shared cache instead)
outputs/cache/
.scratch/

# Editor scratch
.vscode/
.idea/
"""


TEMPLATES: list[tuple[Path, str, str]] = [
    (TOOLKIT / "pyproject.toml", PYPROJECT_TOML, "package metadata"),
    (TOOLKIT / "README.md", README_MD, "intro README"),
    (TOOLKIT / ".gitignore", GITIGNORE, "gitignore"),
]


# ---------------------------------------------------------------------------
# Action helpers
# ---------------------------------------------------------------------------


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(TOOLKIT))
    except ValueError:
        return str(path)


def _print(verb: str, path: Path, note: str = "") -> None:
    suffix = f"  ({note})" if note else ""
    print(f"  [{verb:>7s}]  {_rel(path)}{suffix}")


def make_dir(path: Path, *, execute: bool) -> bool:
    if path.exists():
        return False
    _print("MKDIR", path)
    if execute:
        path.mkdir(parents=True, exist_ok=True)
    return True


def make_init(path: Path, *, execute: bool, force: bool) -> bool:
    if path.exists() and not force:
        _print("SKIP", path, "already exists")
        return False
    _print("CREATE", path, "empty __init__.py")
    if execute:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    return True


def make_template(
    path: Path, content: str, label: str, *, execute: bool, force: bool
) -> bool:
    if path.exists() and not force:
        _print("SKIP", path, f"already exists ({label})")
        return False
    _print("CREATE", path, label)
    if execute:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return True


def copy_one(
    src: Path, dst: Path, label: str, *, execute: bool, force: bool
) -> tuple[bool, bool]:
    """Returns (copy_planned, source_missing)."""
    if not src.exists():
        _print("MISS", dst, f"source not found: {src}")
        return False, True
    if dst.exists() and not force:
        _print("SKIP", dst, "already exists (use --force to overwrite)")
        return False, False
    _print("COPY", dst, f"from {label}")
    if execute:
        dst.parent.mkdir(parents=True, exist_ok=True)
        copy2(src, dst)
    return True, False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--execute",
        action="store_true",
        help="Actually create files.  Default is dry-run.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite destination files that already exist.",
    )
    args = p.parse_args(argv)

    # Pre-flight: source roots must exist.
    for label, root in (
        ("task-research", TASK_RESEARCH),
        ("openneuro-metadata", OPENNEURO_META),
    ):
        if not root.exists():
            print(f"ERROR: source root not found ({label}): {root}", file=sys.stderr)
            return 2
    if not TOOLKIT.exists():
        print(f"ERROR: toolkit root not found: {TOOLKIT}", file=sys.stderr)
        return 2

    if not args.execute:
        print("DRY-RUN — nothing will be created or modified.")
        print("Re-run with --execute to perform the actions below.")
        print()
    else:
        print(f"EXECUTE — applying changes to {TOOLKIT}")
        if args.force:
            print("(force=True — existing destination files WILL be overwritten)")
        print()

    print("Sources:")
    print(f"  task-research      : {TASK_RESEARCH}")
    print(f"  openneuro-metadata : {OPENNEURO_META}")
    print("Destination:")
    print(f"  toolkit            : {TOOLKIT}")
    print()

    # ---- 1. Directories
    print("=== Directories ===")
    dirs = [
        PKG,
        PKG / "clients",
        PKG / "github_org",
        PKG / "dataset_summary",
        PKG / "citations",
        TOOLKIT / "tests",
    ]
    for d in dirs:
        make_dir(d, execute=args.execute)
    print()

    # ---- 2. __init__.py markers
    print("=== Package __init__.py markers ===")
    for init_path in NEW_INIT_FILES:
        make_init(init_path, execute=args.execute, force=args.force)
    print()

    # ---- 3. Template files
    print("=== Template files (pyproject / README / .gitignore) ===")
    for path, content, label in TEMPLATES:
        make_template(path, content, label, execute=args.execute, force=args.force)
    print()

    # ---- 4. Source copies
    print("=== File copies ===")
    n_copied = 0
    n_missing = 0
    n_skipped = 0
    for src, dst, label in COPIES:
        planned, missing = copy_one(
            src, dst, label, execute=args.execute, force=args.force
        )
        if planned:
            n_copied += 1
        elif missing:
            n_missing += 1
        else:
            n_skipped += 1
    print()

    # ---- 5. Summary + next steps
    mode = "executed" if args.execute else "PLANNED"
    print(f"Summary ({mode}):")
    print(f"  copies            : {n_copied}")
    print(f"  destinations skipped (already existed) : {n_skipped}")
    print(f"  sources missing   : {n_missing}")
    print()
    print("Next steps (manual, after running with --execute):")
    print()
    print("  1.  Review the new layout under src/hed_metadata_toolkit/.")
    print("  2.  Rewrite import statements inside the copied files so")
    print("      they use the hed_metadata_toolkit.* namespace, e.g.:")
    print(
        "           from cache import ...        ->   from hed_metadata_toolkit.cache import ..."
    )
    print(
        "           from clients import crossref ->   from hed_metadata_toolkit.clients import crossref"
    )
    print("      `ruff check --fix --select=F401,I` will not do this for")
    print("      you — but a single sed/grep pass plus `pytest tests/`")
    print("      will close the loop.")
    print()
    print("  3.  cd", TOOLKIT)
    print("      .venv/Scripts/activate  (or source on bash)")
    print("      pip install -e .[dev]")
    print("      pytest tests/")
    print()
    print("  4.  Once tests pass, commit:")
    print("      git add -A && git commit -m 'Initial toolkit extraction from")
    print("      task-research and openneuro-metadata'")
    print()
    print("  5.  Plan the consumer migrations in separate PRs:")
    print("      - openneuro-metadata: add hed-metadata-toolkit dep, delete")
    print("        the now-duplicated files, update imports.")
    print("      - task-research: same, but only for the modules it shares")
    print("        (cache, identity, clients/) — keep acquire/, analyze_*,")
    print("        enrich_no_doi where they are.")
    print("      - nemar-metadata: stand up from scratch using the toolkit")
    print("        from day one.")
    print()
    print("Originals in task-research and openneuro-metadata are NOT")
    print("touched by this script — those repos keep working as-is until")
    print("you choose to delete the now-duplicate files in a separate PR.")

    return 0 if n_missing == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
