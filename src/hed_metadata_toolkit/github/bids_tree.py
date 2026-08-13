"""bids_tree.py — derive per-repo BIDS metadata from a recursive git-tree.

Pure helpers (no network, no file IO) shared by the GitHub pipeline. Given the
entries of a repository's recursive git-tree
(``GET /repos/{org}/{repo}/git/trees/HEAD?recursive=1``), derive the fields
stored per repo in ``repo_contents.json``:

  - ``top_level_files`` — root-level (non-hidden) blobs, excluding root
    ``.json`` files other than ``*_events.json``, ``dataset_description.json``,
    and ``participants.json``; plus all blobs under any explicitly included
    subdirectory (e.g. ``.nemar``), which the exclusion does not apply to.
  - ``subjects``        — sorted top-level ``sub-*`` directory names.
  - ``datatypes``       — sorted BIDS *datatype* directory names found beneath a
    top-level ``sub-*`` directory (see ``ALLOWED_DATATYPES``).
  - ``event_files``     — ``*_events.tsv`` / ``*_events.json`` blobs at the repo
    root or anywhere beneath a top-level ``sub-*`` directory.

BIDS layout assumed: ``sub-XXX`` dirs are one
level below the root, an optional ``ses-YYY`` dir sits under the subject, the
datatype directory is 2 or 3 levels down, and data files are 3 or 4 levels down.
``derivatives/`` (and any other non-``sub-`` top-level directory) is ignored for
subjects / datatypes / event files. ``phenotype`` is intentionally excluded from
the datatype set (maintainer: "ignore phenotype, we aren't doing that").

Each git-tree entry is a dict with at least ``path`` and ``type`` ("blob" or
"tree"); blobs also carry ``size`` and ``sha``. ``path`` is repo-root-relative
with POSIX separators.
"""

from __future__ import annotations

# BIDS datatype directory names (the directories that sit under sub-*/[ses-*]/).
# `phenotype` is deliberately omitted. These are matched as exact path segments,
# so filenames that merely contain a datatype token never false-match.
ALLOWED_DATATYPES = frozenset(
    {
        "anat",
        "func",
        "dwi",
        "fmap",
        "perf",
        "meg",
        "eeg",
        "ieeg",
        "beh",
        "pet",
        "micr",
        "nirs",
        "motion",
        "mrs",
        "emg",
    }
)

EVENTS_SUFFIXES = ("_events.tsv", "_events.json")

# Root-level .json files kept in top_level_files by name. Any other root .json
# that is not a *_events.json is excluded: datatype sidecars (task-X_eeg.json,
# task-X_bold.json, ...) are numerous and nothing in the pipeline reads them.
KEPT_ROOT_JSON = frozenset({"dataset_description.json", "participants.json"})


def _keep_root_blob(name: str) -> bool:
    """True when a non-hidden root blob belongs in ``top_level_files``.

    Every root file is kept except ``.json`` files; a root ``.json`` is kept
    only when it ends in ``_events.json`` or is named in ``KEPT_ROOT_JSON``.
    """
    if not name.endswith(".json"):
        return True
    return name.endswith("_events.json") or name in KEPT_ROOT_JSON


def is_event_file(path: str) -> bool:
    """True for a BIDS event file at the repo root or under a top-level ``sub-*``.

    ``path`` is repo-root-relative (POSIX separators). It qualifies when it ends
    in ``_events.tsv`` / ``_events.json`` AND it is either at the root (no ``/``)
    or its first path segment is a ``sub-`` directory (any depth below it).
    Event files under other top-level directories (``derivatives/`` etc.) are
    rejected.
    """
    if not path.endswith(EVENTS_SUFFIXES):
        return False
    if "/" not in path:
        return True  # repo root
    return path.split("/", 1)[0].startswith("sub-")


def _blob_obj(entry: dict) -> dict:
    """Normalize a git-tree blob entry to the stored ``{path, size, sha}`` shape."""
    return {
        "path": entry.get("path"),
        "size": entry.get("size"),
        "sha": entry.get("sha"),
    }


def derive_repo_metadata(tree_entries: list, include_subdirs: "list[str] | None" = None) -> dict:
    """Derive the per-repo ``repo_contents.json`` fields from a recursive tree.

    Parameters:
        tree_entries: the ``tree`` array from a recursive git-trees response
            (both ``blob`` and ``tree`` entries; only ``path``/``type``/``size``/
            ``sha`` are used).
        include_subdirs: directories whose blobs should also be treated as
            "top level" (e.g. ``[".nemar"]``). Their blobs are recorded with the
            full ``<subdir>/<file>`` path, and all of them are kept - the root
            ``.json`` exclusion does not apply under an included subdir.

    Returns:
        ``{"top_level_files": [...], "subjects": [...], "datatypes": [...],
        "event_files": [...]}`` — ``top_level_files`` and ``event_files`` are
        lists of ``{path, size, sha}`` sorted by path; ``subjects`` and
        ``datatypes`` are sorted name lists.
    """
    include = tuple(include_subdirs or ())

    subjects: set[str] = set()
    datatypes: set[str] = set()
    top_level_files: list[dict] = []
    event_files: list[dict] = []

    for entry in tree_entries:
        path = entry.get("path") or ""
        if not path:
            continue
        segments = path.split("/")
        first = segments[0]

        # subjects + datatypes are scoped to top-level sub-* trees only.
        if first.startswith("sub-"):
            subjects.add(first)
            for seg in segments[1:]:
                if seg in ALLOWED_DATATYPES:
                    datatypes.add(seg)

        if entry.get("type") != "blob":
            continue

        # top_level_files: non-hidden root blobs passing the .json filter, plus
        # all blobs under an included subdir.
        if "/" not in path:
            if not path.startswith(".") and _keep_root_blob(path):
                top_level_files.append(_blob_obj(entry))
        elif first in include:
            top_level_files.append(_blob_obj(entry))

        if is_event_file(path):
            event_files.append(_blob_obj(entry))

    return {
        "top_level_files": sorted(top_level_files, key=lambda b: b["path"] or ""),
        "subjects": sorted(subjects),
        "datatypes": sorted(datatypes),
        "event_files": sorted(event_files, key=lambda b: b["path"] or ""),
    }
