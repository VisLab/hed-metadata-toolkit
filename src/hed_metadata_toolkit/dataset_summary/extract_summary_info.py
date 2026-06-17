"""
extract_summary_info.py

Reads repo_contents.json (produced by sync_repo_contents.py) and extracts
per-dataset statistics to build a summary TSV:
  - Subject count (number of top-level sub-* directories)
  - Presence of README files
  - Presence of *events.json files
  - Task names extracted from filenames (task-<name> pattern)

The output TSV provides a template for manual curation; the title, links,
modalities, contact, and notes columns are filled by subsequent scripts or
human reviewers.

NEMAR enrichment: if a dataset has a local ``.nemar/metadata.json`` (under
``--datasets-dir/<repo>/.nemar/metadata.json``), its ``title`` and ``modalities``
and the ``related_identifiers`` (as ``links``) are pulled into those columns.
Datasets without that file (e.g. OpenNeuro) are unchanged.

Input:
    datasets/dataset_summaries/repo_contents.json
    Schema:
        {
          "ds000001": {
            "synced_at": "2026-06-01T21:19:43Z",
            "entries": [
              {"name": "README", "type": "blob", "size": 1175, "sha": "abc123"},
              {"name": "sub-01", "type": "tree"},
              ...
            ]
          },
          ...
        }

Output:
    datasets/dataset_summaries/dataset_summary.tsv
    Columns: name, subjs, links, readme, events, title, tasks, modalities, contact, notes

Usage:
    python extract_summary_info.py

The script expects repo_contents.json to exist. If you have not yet run
sync_repo_contents.py, this script will exit with an error.
"""

import argparse
import json
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv


def count_subjects(file_list):
    """Count the number of subjects (entries starting with 'sub').

    Parameters:
        file_list: List of files/directories in the repository.

    Returns:
        Number of subjects found.
    """
    subject_count = 0
    for item in file_list:
        if item.startswith("sub"):
            subject_count += 1
    return subject_count


def check_has_events(file_list):
    """Check if there are any events.json files.

    Parameters:
        file_list: List of files/directories in the repository.

    Returns:
        'yes' if events.json files found, 'no' otherwise.
    """
    for item in file_list:
        if item.endswith("events.json"):
            return "yes"
    return "no"


def extract_task_names(file_list):
    """Extract task names from filenames containing 'task'.

    Parameters:
        file_list: List of files/directories in the repository.

    Returns:
        Comma-separated list of task names.
    """
    task_names = set()  # Use set to avoid duplicates

    for item in file_list:
        if "task" in item.lower():
            # Split by underscores
            parts = item.split("_")

            for part in parts:
                if part.startswith("task-"):
                    # Extract task name after 'task-'
                    task_name = part[5:]  # Remove 'task-' prefix
                    if task_name:  # Only add non-empty task names
                        task_names.add(task_name)

    # Convert set to sorted list and join with commas
    if task_names:
        return ",".join(sorted(task_names))
    else:
        return ""


def check_has_readme(file_list):
    """Check if there are any README files.

    Parameters:
        file_list: List of files/directories in the repository.

    Returns:
        'yes' if README files found, 'no' otherwise.
    """
    for item in file_list:
        if item.lower().startswith("readme"):
            return "yes"
    return "no"


def _load_nemar_metadata(datasets_dir, dataset_name):
    """Return the parsed ``.nemar/metadata.json`` for a dataset, or None.

    Looks for ``<datasets_dir>/<dataset_name>/.nemar/metadata.json``. Returns
    None when ``datasets_dir`` is not given, the file is absent, or it does not
    parse as JSON.
    """
    if not datasets_dir:
        return None
    path = Path(datasets_dir) / dataset_name / ".nemar" / "metadata.json"
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"  Warning: could not read {path}: {exc}")
        return None


def _nemar_overrides(meta):
    """Map a ``.nemar/metadata.json`` dict to summary columns.

    Returns a dict with ``title``, ``modalities`` (comma-joined), and ``links``
    (the ``related_identifiers`` values joined by ``; ``).
    """
    modalities = meta.get("modalities") or []
    if not isinstance(modalities, str):
        modalities = ",".join(str(m) for m in modalities)
    identifiers = []
    for rel in meta.get("related_identifiers") or []:
        if isinstance(rel, dict) and rel.get("identifier"):
            identifiers.append(str(rel["identifier"]))
    return {
        "title": meta.get("title") or "",
        "modalities": modalities,
        "links": "; ".join(identifiers),
    }


def extract_dataset_info(repo_contents_json_path, datasets_dir=None):
    """Extract dataset information from repo_contents.json.

    Parameters:
        repo_contents_json_path: Path to the repo_contents.json file
            (produced by sync_repo_contents.py).
        datasets_dir: Optional path to the local ``dataset_repos`` directory.
            When given, a dataset's ``.nemar/metadata.json`` (if present) fills
            the ``title``, ``modalities``, and ``links`` columns.

    Returns:
        List of dictionaries containing dataset information.
    """
    # Load the repository contents data
    try:
        with open(repo_contents_json_path, "r", encoding="utf-8") as f:
            repo_data = json.load(f)
        print(
            f"Loaded data for {len(repo_data)} repositories from {repo_contents_json_path}"
        )
    except Exception as e:
        print(f"Error reading JSON file: {e}")
        return []

    dataset_info = []

    for dataset_name, dataset_entry in repo_data.items():
        print(f"Processing dataset: {dataset_name}")

        # Extract the list of entry names from the new repo_contents.json format
        # repo_contents.json has structure: {"ds000001": {"synced_at": "...", "entries": [...]}}
        if isinstance(dataset_entry, dict) and "entries" in dataset_entry:
            # New format from sync_repo_contents.py
            file_list = [entry["name"] for entry in dataset_entry.get("entries", [])]
        elif isinstance(dataset_entry, list):
            # Legacy format from get_repo_files.py (list of strings)
            file_list = dataset_entry
        else:
            print(f"  Warning: unexpected format for {dataset_name}, skipping")
            continue

        # Extract information
        subjs = count_subjects(file_list)
        events = check_has_events(file_list)
        tasks = extract_task_names(file_list)
        readme = check_has_readme(file_list)

        # Create dataset info record
        info = {
            "name": dataset_name,
            "subjs": subjs,
            "title": "",  # Will be filled by another script
            "links": "",  # Will be filled by another script
            "readme": readme,
            "events": events,
            "tasks": tasks,
            "modalities": "",  # Will be filled by another script
            "contact": "",  # Will be filled by another script
            "notes": "",  # Will be filled by another script
        }

        # NEMAR enrichment: when a .nemar/metadata.json is present, fill
        # title, modalities, and links from it.
        nemar_meta = _load_nemar_metadata(datasets_dir, dataset_name)
        if nemar_meta:
            info.update(_nemar_overrides(nemar_meta))
            print("  .nemar metadata: filled title, modalities, links")

        dataset_info.append(info)

        # Print summary for this dataset
        print(f"  Subjects: {subjs}")
        print(f"  README: {readme}")
        print(f"  Events: {events}")
        if tasks:
            print(f"  Tasks: {tasks}")
        else:
            print("  Tasks: none")

    return dataset_info


def save_dataset_summary(dataset_info, output_file="dataset_summary.tsv"):
    """Save dataset information to a TSV file.

    Parameters:
        dataset_info: List of dictionaries containing dataset information.
        output_file: Path to the output TSV file.
    """
    # Convert to DataFrame
    df = pd.DataFrame(dataset_info)

    # Ensure column order
    columns = [
        "name",
        "subjs",
        "links",
        "readme",
        "events",
        "title",
        "tasks",
        "modalities",
        "contact",
        "notes",
    ]
    df = df[columns]

    # Save to TSV
    df.to_csv(output_file, sep="\t", index=False)
    print(f"Dataset summary saved to {output_file}")


def print_extraction_summary(dataset_info):
    """Print a summary of the extraction process."""
    print("\n" + "=" * 50)
    print("DATASET EXTRACTION SUMMARY")
    print("=" * 50)

    total_datasets = len(dataset_info)
    total_subjects = sum(info["subjs"] for info in dataset_info)
    datasets_with_events = sum(1 for info in dataset_info if info["events"] == "yes")
    datasets_with_readme = sum(1 for info in dataset_info if info["readme"] == "yes")
    datasets_with_tasks = sum(1 for info in dataset_info if info["tasks"])

    print(f"Total datasets processed: {total_datasets}")
    print(f"Total subjects across all datasets: {total_subjects}")
    print(f"Datasets with events.json files: {datasets_with_events}")
    print(f"Datasets with README files: {datasets_with_readme}")
    print(f"Datasets with task information: {datasets_with_tasks}")

    if dataset_info:
        print(f"\nAverage subjects per dataset: {total_subjects / total_datasets:.1f}")

        # Show some examples
        print("\nSample dataset info:")
        for info in dataset_info[:3]:
            print(
                f"  {info['name']}: {info['subjs']} subjects, events={info['events']}, readme={info['readme']}"
            )
            if info["tasks"]:
                print(f"    Tasks: {info['tasks']}")

    print("=" * 50)


# ---------------------------------------------------------------------------
# Library API
# ---------------------------------------------------------------------------


def run_extraction(
    *,
    input_path: "Path | str | None" = None,
    repo_contents_path: "Path | str | None" = None,
    legacy_repo_files_path: "Path | str | None" = None,
    output_path: "Path | str",
    datasets_dir: "Path | str | None" = None,
) -> int:
    """Extract per-dataset summary info and write the summary TSV.

    Library entry point.

    Pass either ``input_path`` directly OR a pair of candidate paths
    (``repo_contents_path`` preferred, ``legacy_repo_files_path``
    fallback).  Returns the number of dataset rows written; returns
    0 (and skips the write) if no dataset info was extracted.

    Raises ``FileNotFoundError`` if neither candidate path exists.
    """
    if input_path is None:
        if repo_contents_path and Path(repo_contents_path).exists():
            input_path = Path(repo_contents_path)
        elif legacy_repo_files_path and Path(legacy_repo_files_path).exists():
            input_path = Path(legacy_repo_files_path)
        else:
            raise FileNotFoundError(
                f"neither repo_contents path nor legacy fallback was found "
                f"({repo_contents_path!r}, {legacy_repo_files_path!r})"
            )
    dataset_info = extract_dataset_info(str(input_path), datasets_dir=datasets_dir)
    if not dataset_info:
        return 0
    save_dataset_summary(dataset_info, str(output_path))
    return len(dataset_info)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: "list[str] | None" = None) -> int:
    """Argparse wrapper around :func:`run_extraction`."""
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Extract dataset information from repo contents.",
    )
    parser.add_argument(
        "--repo-contents",
        type=Path,
        default=Path("datasets/dataset_summaries/repo_contents.json"),
        help="Path to the current-pipeline repo_contents.json.",
    )
    parser.add_argument(
        "--legacy-repo-files",
        type=Path,
        default=Path("datasets/dataset_summaries/repo_files.json"),
        help="Fallback path to the legacy repo_files.json.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("datasets/dataset_summaries/dataset_summary.tsv"),
        help="Destination path for the dataset summary TSV.",
    )
    parser.add_argument(
        "--datasets-dir",
        type=Path,
        default=Path("datasets/dataset_repos"),
        help="Local dataset_repos directory. A dataset's .nemar/metadata.json "
        "(if present) fills the title, modalities, and links columns.",
    )
    args = parser.parse_args(argv)

    print("Extracting dataset information from repo contents...")

    if args.repo_contents.exists():
        print(f"Using current pipeline output: {args.repo_contents.resolve()}")
    elif args.legacy_repo_files.exists():
        print(f"Using legacy output: {args.legacy_repo_files.resolve()}")
        print(
            "Warning: repo_files.json is from the legacy pipeline. "
            "Consider running sync_repo_contents first."
        )
    else:
        print(
            f"Error: Neither {args.repo_contents} nor {args.legacy_repo_files} found."
        )
        print("Run sync_repo_contents first.")
        return 1

    print(f"Output file: {args.output.resolve()}")

    try:
        n = run_extraction(
            repo_contents_path=args.repo_contents,
            legacy_repo_files_path=args.legacy_repo_files,
            output_path=args.output,
            datasets_dir=args.datasets_dir,
        )
    except FileNotFoundError as exc:
        print(f"Error: {exc}")
        return 1

    if n == 0:
        print("No dataset information was extracted.")
        return 1

    # Re-extract for the per-extraction summary; cheap to do
    # once more so the CLI summary doesn't need to be threaded
    # through the library function.
    input_used = (
        args.repo_contents if args.repo_contents.exists() else args.legacy_repo_files
    )
    print_extraction_summary(
        extract_dataset_info(str(input_used), datasets_dir=args.datasets_dir)
    )
    print("\nDataset information extraction complete!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
