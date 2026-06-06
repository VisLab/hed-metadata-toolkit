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

import json
import os
import pandas as pd
from dotenv import load_dotenv

def count_subjects(file_list):
    """
    Count the number of subjects (entries starting with 'sub').

    Args:
        file_list (list): List of files/directories in the repository.

    Returns:
        int: Number of subjects found.
    """
    subject_count = 0
    for item in file_list:
        if item.startswith('sub'):
            subject_count += 1
    return subject_count

def check_has_events(file_list):
    """
    Check if there are any events.json files.

    Args:
        file_list (list): List of files/directories in the repository.

    Returns:
        str: 'yes' if events.json files found, 'no' otherwise.
    """
    for item in file_list:
        if item.endswith('events.json'):
            return 'yes'
    return 'no'

def extract_task_names(file_list):
    """
    Extract task names from filenames containing 'task'.

    Args:
        file_list (list): List of files/directories in the repository.

    Returns:
        str: Comma-separated list of task names.
    """
    task_names = set()  # Use set to avoid duplicates

    for item in file_list:
        if 'task' in item.lower():
            # Split by underscores
            parts = item.split('_')

            for part in parts:
                if part.startswith('task-'):
                    # Extract task name after 'task-'
                    task_name = part[5:]  # Remove 'task-' prefix
                    if task_name:  # Only add non-empty task names
                        task_names.add(task_name)

    # Convert set to sorted list and join with commas
    if task_names:
        return ','.join(sorted(task_names))
    else:
        return ''

def check_has_readme(file_list):
    """
    Check if there are any README files.

    Args:
        file_list (list): List of files/directories in the repository.

    Returns:
        str: 'yes' if README files found, 'no' otherwise.
    """
    for item in file_list:
        if item.lower().startswith('readme'):
            return 'yes'
    return 'no'

def extract_dataset_info(repo_contents_json_path):
    """
    Extract dataset information from repo_contents.json.

    Args:
        repo_contents_json_path (str): Path to the repo_contents.json file
                                       (produced by sync_repo_contents.py).

    Returns:
        list: List of dictionaries containing dataset information.
    """
    # Load the repository contents data
    try:
        with open(repo_contents_json_path, 'r', encoding='utf-8') as f:
            repo_data = json.load(f)
        print(f"Loaded data for {len(repo_data)} repositories from {repo_contents_json_path}")
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
            'name': dataset_name,
            'subjs': subjs,
            'title': '',  # Will be filled by another script
            'links': '',  # Will be filled by another script
            'readme': readme,
            'events': events,
            'tasks': tasks,
            'modalities': '',  # Will be filled by another script
            'contact': '',  # Will be filled by another script
            'notes': ''  # Will be filled by another script
        }

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
    """
    Save dataset information to a TSV file.

    Args:
        dataset_info (list): List of dictionaries containing dataset information.
        output_file (str): Path to the output TSV file.
    """
    # Convert to DataFrame
    df = pd.DataFrame(dataset_info)

    # Ensure column order
    columns = ['name', 'subjs', 'links', 'readme', 'events', 'title', 'tasks', 'modalities', 'contact', 'notes']
    df = df[columns]

    # Save to TSV
    df.to_csv(output_file, sep='\t', index=False)
    print(f"Dataset summary saved to {output_file}")

def print_extraction_summary(dataset_info):
    """Print a summary of the extraction process."""
    print("\n" + "="*50)
    print("DATASET EXTRACTION SUMMARY")
    print("="*50)

    total_datasets = len(dataset_info)
    total_subjects = sum(info['subjs'] for info in dataset_info)
    datasets_with_events = sum(1 for info in dataset_info if info['events'] == 'yes')
    datasets_with_readme = sum(1 for info in dataset_info if info['readme'] == 'yes')
    datasets_with_tasks = sum(1 for info in dataset_info if info['tasks'])

    print(f"Total datasets processed: {total_datasets}")
    print(f"Total subjects across all datasets: {total_subjects}")
    print(f"Datasets with events.json files: {datasets_with_events}")
    print(f"Datasets with README files: {datasets_with_readme}")
    print(f"Datasets with task information: {datasets_with_tasks}")

    if dataset_info:
        print(f"\nAverage subjects per dataset: {total_subjects/total_datasets:.1f}")

        # Show some examples
        print("\nSample dataset info:")
        for info in dataset_info[:3]:
            print(f"  {info['name']}: {info['subjs']} subjects, events={info['events']}, readme={info['readme']}")
            if info['tasks']:
                print(f"    Tasks: {info['tasks']}")

    print("="*50)

# --- Example Usage ---
if __name__ == "__main__":
    # Load environment variables from .env file
    load_dotenv()

    # Configuration
    repo_contents_json_path = "../datasets/dataset_summaries/repo_contents.json"
    # Fallback to legacy repo_files.json if repo_contents.json doesn't exist
    legacy_path = "../datasets/dataset_summaries/repo_files.json"
    output_file = "../datasets/dataset_summaries/dataset_summary.tsv"

    print("Extracting dataset information from repo contents...")

    # Check which file exists
    input_path = None
    if os.path.exists(repo_contents_json_path):
        input_path = repo_contents_json_path
        print(f"Using current pipeline output: {os.path.abspath(input_path)}")
    elif os.path.exists(legacy_path):
        input_path = legacy_path
        print(f"Using legacy output: {os.path.abspath(input_path)}")
        print("Warning: repo_files.json is from the legacy pipeline. Consider running sync_repo_contents.py.")
    else:
        print(f"Error: Neither {repo_contents_json_path} nor {legacy_path} found.")
        print("Please run sync_repo_contents.py first (or get_repo_files.py for legacy pipeline).")
        exit(1)

    print(f"Output file: {os.path.abspath(output_file)}")

    # Extract dataset information
    dataset_info = extract_dataset_info(input_path)

    if dataset_info:
        save_dataset_summary(dataset_info, output_file)
        print_extraction_summary(dataset_info)
        print("\nDataset information extraction complete!")
    else:
        print("No dataset information was extracted.")
