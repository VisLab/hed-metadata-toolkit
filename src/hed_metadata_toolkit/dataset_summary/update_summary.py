import argparse
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
from dotenv import find_dotenv, load_dotenv

DEFAULT_CITATIONS = Path("datasets/dataset_summaries/dataset_citations.tsv")


def file_sha256(path) -> str:
    """Return the SHA-256 hex digest of a file's bytes."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def provenance_path(output_path) -> Path:
    """The sidecar that records which raw summary an updated summary was built from.

    ``dataset_summary_updated.tsv`` gets ``dataset_summary_updated.provenance.json``.
    """
    return Path(output_path).with_suffix(".provenance.json")


def write_provenance(summary_path, output_path) -> Path:
    """Record the raw summary's fingerprint next to the updated summary.

    ``hed-sort-datasets`` compares this fingerprint with the raw summary it
    finds before trusting the updated one, so a rerun of extraction cannot
    leave a stale updated summary in use. Modification times cannot do this
    job: a consumer may track these files, and a checkout rewrites the times.
    """
    sidecar = provenance_path(output_path)
    record = {"source": Path(summary_path).name, "source_sha256": file_sha256(summary_path)}
    sidecar.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return sidecar


def read_dataset_description(dataset_dir):
    """Read dataset_description.json from a dataset directory.

    Parameters:
        dataset_dir: Path to the dataset directory.

    Returns:
        Dataset description data, or empty dict if not found/error.
    """
    json_path = os.path.join(dataset_dir, "dataset_description.json")

    try:
        if os.path.exists(json_path):
            with open(json_path, encoding="utf-8") as f:
                return json.load(f)
        else:
            return {}
    except Exception as e:
        print(f"    Error reading {json_path}: {e}")
        return {}


def update_title_and_hed(summary_df, datasets_base_dir="datasets/dataset_repos"):
    """Update title and HED columns from dataset_description.json files.

    Parameters:
        summary_df: Summary DataFrame to update.
        datasets_base_dir: Base directory containing dataset folders.

    Returns:
        Updated DataFrame with title and HED information.
    """
    updated_df = summary_df.copy()

    # Add HED column after readme column if it doesn't exist
    if "HED" not in updated_df.columns:
        readme_idx = updated_df.columns.get_loc("readme")
        # Insert HED column after readme
        cols = updated_df.columns.tolist()
        cols.insert(readme_idx + 1, "HED")
        updated_df = updated_df.reindex(columns=cols)
        updated_df["HED"] = ""

    # Ensure title column exists and is empty initially
    if "title" not in updated_df.columns:
        updated_df["title"] = ""

    # Convert string columns to object dtype to avoid pandas dtype errors
    # when assigning strings to columns that were inferred as numeric
    for col in ["title", "HED", "tasks", "modalities", "contact", "notes"]:
        if col in updated_df.columns:
            updated_df[col] = updated_df[col].astype(object)

    print(f"Updating title and HED information for {len(updated_df)} datasets...")

    for idx, row in updated_df.iterrows():
        dataset_name = row[updated_df.columns[0]]  # First column is dataset name
        dataset_dir = os.path.join(datasets_base_dir, dataset_name)

        print(f"  Processing {dataset_name}...")

        if not os.path.exists(dataset_dir):
            print(f"    Directory not found: {dataset_dir}")
            continue

        # Read dataset description
        desc_data = read_dataset_description(dataset_dir)

        if desc_data:
            # Update title from 'Name' field
            if "Name" in desc_data:
                # Remove newlines and replace with spaces
                title = desc_data["Name"].replace("\n", " ").strip()
                updated_df.at[idx, "title"] = title
                print(f"    Title: {title}")
            else:
                print("    No 'Name' field found in dataset_description.json")

            # Update HED from 'HEDVersion' field
            if "HEDVersion" in desc_data:
                updated_df.at[idx, "HED"] = desc_data["HEDVersion"]
                print(f"    HED Version: {desc_data['HEDVersion']}")
            else:
                print("    No HEDVersion found")
        else:
            print("    No dataset_description.json found or error reading file")

    return updated_df


def read_dataframes(summary_path, citations_path):
    """Read the summary and citations TSV files into pandas DataFrames.

    Parameters:
        summary_path: Path to the dataset_summary.tsv file.
        citations_path: Path to the dataset_citations.tsv file, or None to
            read the summary alone.

    Returns:
        Tuple of (summary_df, citations_df) or (None, None) if error.
        citations_df is None when citations_path is None.
    """
    try:
        # Read dataset summary
        summary_df = pd.read_csv(summary_path, sep="\t")
        print(f"Loaded {len(summary_df)} entries from {summary_path}")

        if citations_path is None:
            return summary_df, None

        # Read dataset citations
        citations_df = pd.read_csv(citations_path, sep="\t")
        print(f"Loaded {len(citations_df)} citations from {citations_path}")

        return summary_df, citations_df

    except Exception as e:
        print(f"Error reading files: {e}")
        return None, None


def sort_dataframes(summary_df, citations_df):
    """Sort both DataFrames by their first column in descending order.

    Parameters:
        summary_df: Dataset summary DataFrame.
        citations_df: Dataset citations DataFrame, or None.

    Returns:
        Tuple of (sorted_summary_df, sorted_citations_df); the second is
        None when citations_df is None.
    """
    summary_first_col = summary_df.columns[0]
    sorted_summary = summary_df.sort_values(by=summary_first_col, ascending=False).reset_index(drop=True)
    print(f"Sorted summary by {summary_first_col}: {len(sorted_summary)} entries")

    if citations_df is None:
        return sorted_summary, None

    citations_first_col = citations_df.columns[0]
    sorted_citations = citations_df.sort_values(by=citations_first_col, ascending=False).reset_index(drop=True)
    print(f"Sorted citations by {citations_first_col}: {len(sorted_citations)} entries")

    return sorted_summary, sorted_citations


def merge_citations_efficiently(summary_df, citations_df):
    """Efficiently merge citation links into the summary DataFrame.

    Parameters:
        summary_df: Sorted dataset summary DataFrame.
        citations_df: Sorted dataset citations DataFrame.

    Returns:
        Updated summary DataFrame with citation links.
    """
    # Get column names
    summary_key_col = summary_df.columns[0]  # e.g., 'name'
    citations_key_col = citations_df.columns[0]  # e.g., 'dataset_id'
    citations_link_col = citations_df.columns[1]  # e.g., 'citation_link'

    print(f"Merging citations using keys: {summary_key_col} <- {citations_key_col}")

    # Create a dictionary to group citations by dataset
    citations_dict = {}
    for _, row in citations_df.iterrows():
        dataset_id = row[citations_key_col]
        link = row[citations_link_col]

        if dataset_id not in citations_dict:
            citations_dict[dataset_id] = []
        citations_dict[dataset_id].append(link)

    print(f"Grouped citations for {len(citations_dict)} unique datasets")

    # Update the summary DataFrame
    updated_summary = summary_df.copy()

    # Ensure links column is integer type and fill any NaN with 0
    if "links" not in updated_summary.columns:
        updated_summary["links"] = 0
    else:
        updated_summary["links"] = updated_summary["links"].fillna(0).astype(int)

    links_updated = 0

    for idx, row in updated_summary.iterrows():
        dataset_id = row[summary_key_col]

        if dataset_id in citations_dict:
            # Store the count of links instead of the actual links
            link_count = len(citations_dict[dataset_id])
            updated_summary.at[idx, "links"] = link_count
            links_updated += 1
            print(f"  Updated {dataset_id}: {link_count} links")
        else:
            # Ensure no links datasets have 0 instead of empty string
            updated_summary.at[idx, "links"] = 0

    print(f"Updated links for {links_updated} datasets")
    return updated_summary


def save_updated_summary(updated_df, output_path):
    """Save the updated summary DataFrame to a TSV file.

    Parameters:
        updated_df: Updated summary DataFrame.
        output_path: Path to save the output file.

    Raises ``RuntimeError`` if the file cannot be written, so that the caller
    never records provenance for an updated summary that was not saved.
    """
    try:
        updated_df.to_csv(output_path, sep="\t", index=False)
    except OSError as e:
        raise RuntimeError(f"failed to save {output_path}: {e}") from e
    print(f"Updated summary saved to {output_path}")


def print_update_summary(original_df, updated_df, citations_merged=True):
    """Print a summary of the update process.

    Parameters:
        original_df: Summary DataFrame before the update.
        updated_df: Summary DataFrame after the update.
        citations_merged: False when no citations file was merged; the
            links column then keeps whatever the summary held and is not
            a count, so link statistics are skipped.
    """
    print("\n" + "=" * 50)
    print("UPDATE SUMMARY")
    print("=" * 50)

    total_datasets = len(updated_df)
    print(f"Total datasets: {total_datasets}")
    if "HED" in updated_df.columns:
        hed_values = updated_df["HED"].fillna("").astype(str).str.strip()
        with_hed = int((~hed_values.isin(["", "[]"])).sum())
        print(f"Datasets declaring a HEDVersion: {with_hed}")

    if not citations_merged:
        print("No citations merged; links column left as read from the summary.")
        print("=" * 50)
        return

    # Check for datasets with links (count > 0)
    datasets_with_links = len(updated_df[updated_df["links"] > 0])
    datasets_without_links = total_datasets - datasets_with_links

    print(f"Datasets with citation links: {datasets_with_links}")
    print(f"Datasets without citation links: {datasets_without_links}")

    if datasets_with_links > 0:
        print("\nSample datasets with links:")
        sample_with_links = updated_df[updated_df["links"] > 0].head(3)
        for _, row in sample_with_links.iterrows():
            dataset_name = row[updated_df.columns[0]]
            num_links = row["links"]
            print(f"  {dataset_name}: {num_links} link(s)")

    print("=" * 50)


# ---------------------------------------------------------------------------
# Library API
# ---------------------------------------------------------------------------


def run_update(
    *,
    summary_path: "Path | str",
    citations_path: "Path | str | None",
    datasets_dir: "Path | str",
    output_path: "Path | str",
):
    """Update the dataset summary with citation counts, titles, and HED info.

    Library entry point.  Reads ``summary_path`` and ``citations_path``,
    enriches the summary by walking each dataset's
    ``dataset_description.json`` under ``datasets_dir``, merges the
    citation counts, writes the result to ``output_path``, and
    returns the final DataFrame.

    Pass ``citations_path=None`` for a consumer with no citation registry:
    titles and HED versions are still filled, and the ``links`` column is
    left exactly as the summary has it.

    Raises ``FileNotFoundError`` if the summary is missing, or if
    ``citations_path`` is given and missing.
    """
    summary_path = Path(summary_path)
    if citations_path is not None:
        citations_path = Path(citations_path)
    if not summary_path.exists():
        raise FileNotFoundError(f"summary not found: {summary_path}")
    if citations_path is not None and not citations_path.exists():
        raise FileNotFoundError(f"citations not found: {citations_path}")

    summary_df, citations_df = read_dataframes(
        str(summary_path), None if citations_path is None else str(citations_path)
    )
    if summary_df is None or (citations_path is not None and citations_df is None):
        raise RuntimeError("failed to read input files")
    sorted_summary, sorted_citations = sort_dataframes(summary_df, citations_df)
    final_summary = update_title_and_hed(sorted_summary, str(datasets_dir))
    if sorted_citations is not None:
        final_summary = merge_citations_efficiently(final_summary, sorted_citations)
    save_updated_summary(final_summary, str(output_path))
    write_provenance(summary_path, output_path)
    return final_summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: "list[str] | None" = None) -> int:
    """Argparse wrapper around :func:`run_update`."""
    load_dotenv(find_dotenv(usecwd=True))

    parser = argparse.ArgumentParser(
        description="Update the dataset summary with citation links + HED metadata.",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("datasets/dataset_summaries/dataset_summary.tsv"),
    )
    parser.add_argument(
        "--citations",
        type=Path,
        default=None,
        help=(
            f"Path to the citations TSV. Default: {DEFAULT_CITATIONS} if it exists; "
            "otherwise titles and HED versions are filled without merging citations."
        ),
    )
    parser.add_argument(
        "--datasets-dir",
        type=Path,
        default=Path("datasets/dataset_repos"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("datasets/dataset_summaries/dataset_summary_updated.tsv"),
    )
    args = parser.parse_args(argv)

    # An explicit --citations must exist; the default is used only if present,
    # so a consumer without a citation registry still gets titles and HED.
    citations = args.citations
    if citations is None and DEFAULT_CITATIONS.exists():
        citations = DEFAULT_CITATIONS

    print("Updating dataset summary with titles, HED versions, and citation links...")
    print(f"Summary file:   {args.summary.resolve()}")
    if citations is None:
        print(f"Citations file: none ({DEFAULT_CITATIONS} not found; citation merge skipped)")
    else:
        print(f"Citations file: {citations.resolve()}")
    print(f"Output file:    {args.output.resolve()}")

    # Capture original for the print summary.
    if not args.summary.exists():
        print(f"Error: {args.summary} not found.")
        return 1
    if citations is not None and not citations.exists():
        print(f"Error: {citations} not found.")
        return 1

    summary_df, _ = read_dataframes(str(args.summary), None)

    try:
        final_summary = run_update(
            summary_path=args.summary,
            citations_path=citations,
            datasets_dir=args.datasets_dir,
            output_path=args.output,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"Error: {exc}")
        return 1

    print_update_summary(summary_df, final_summary, citations_merged=citations is not None)
    print("\nDataset summary update complete!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
