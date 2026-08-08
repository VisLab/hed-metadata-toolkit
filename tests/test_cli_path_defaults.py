"""test_cli_path_defaults.py — CLI defaults must be cwd-relative, not package-relative.

A default path built from ``Path(__file__).resolve().parent.parent`` resolves to
``.../site-packages/hed_metadata_toolkit`` once the toolkit is installed, so the
command looks for ``datasets/`` and ``config/`` *inside the package* and fails on
any real run.  Defaults must be anchored to the current working directory, which
is the consumer repo's root.

These tests assert that each CLI's default paths are NOT located inside the
installed package — i.e. they are anchored to the current working directory.
Each assertion fails against the old (buggy) code and passes after the fix.

Run:
    pytest tests/test_cli_path_defaults.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hed_metadata_toolkit.citations import (
    apply_manual_fills,
    assign_citation_ids,
    collect_citations,
    enrich_pub_ids,
    generate_review_queue,
)


def _package_dir(module) -> Path:
    """.../hed_metadata_toolkit — the dir the buggy code anchored to."""
    return Path(module.__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "module, attr",
    [
        (collect_citations, "_REPO_ROOT"),
        (enrich_pub_ids, "_ROOT"),
        (generate_review_queue, "_ROOT"),
        (apply_manual_fills, "REPO_ROOT"),
    ],
)
def test_module_root_not_inside_package(module, attr):
    """The module-level path anchor must not live inside the package."""
    root = getattr(module, attr).resolve()
    pkg_dir = _package_dir(module)
    assert not root.is_relative_to(pkg_dir), (
        f"{module.__name__}.{attr} == {root} resolves inside the installed "
        f"package ({pkg_dir}); default paths must be relative to the working "
        "directory instead."
    )


def test_assign_citation_ids_defaults_not_inside_package(monkeypatch):
    """assign_citation_ids builds defaults from a local repo_root in parse_args."""
    monkeypatch.setattr(sys, "argv", ["hed-assign-citation-ids"])
    args = assign_citation_ids.parse_args()
    pkg_dir = _package_dir(assign_citation_ids)
    for value in (args.registry, args.citations, args.skip_list):
        assert not Path(value).resolve().is_relative_to(pkg_dir), (
            f"default {value} resolves inside the package ({pkg_dir})"
        )
