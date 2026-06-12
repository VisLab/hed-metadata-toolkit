"""test_cli_entry_points.py — Smoke-test the installed console scripts.

Each ``hed-*`` command declared in pyproject's [project.scripts] is invoked as
an installed executable with ``--help``.  This exercises the entry point end to
end: the launcher resolves, the target module imports (catching missing
dependencies or broken imports), and argparse builds.  ``--help`` is handled
before any required-argument validation, so it exits 0 for every command.

If the toolkit is not pip-installed in the current environment (so the commands
are not on PATH), each case is skipped rather than failed — these only make
sense against an installed package.

Run (after `pip install -e .`):
    pytest tests/test_cli_entry_points.py -v
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

COMMANDS = [
    "hed-fetch-repo-list",
    "hed-sync-repo-contents",
    "hed-sync-local-files",
    "hed-sync-repo-file-contents",
    "hed-extract-summary-info",
    "hed-extract-readme-summaries",
    "hed-update-summary",
    "hed-sort-datasets",
    "hed-collect-citations",
    "hed-assign-citation-ids",
    "hed-generate-review-queue",
    "hed-apply-manual-fills",
    "hed-enrich-pub-ids",
    "hed-convert-pdfs",
]


@pytest.mark.parametrize("command", COMMANDS)
def test_command_help_exits_zero(command):
    exe = shutil.which(command)
    if exe is None:
        pytest.skip(f"{command} not on PATH — toolkit not pip-installed in this env")
    result = subprocess.run(
        [exe, "--help"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"{command} --help exited {result.returncode}\n{result.stderr}"
    )
    assert "usage" in (result.stdout + result.stderr).lower()
