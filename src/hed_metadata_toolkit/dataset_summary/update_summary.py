import pandas as pd
import json
import os
from dotenv import load_dotenv

def read_dataset_description(dataset_dir):
    """
    Read dataset_description.json from a dataset directory.

    Args:
        dataset_dir (str): Path to the dataset directory.

    Returns:
        dict: Dataset description data, or empty dict if not found/error.
    """
    json_path = os.path.join(dataset_dir, "dataset_description.json")

    try:
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            return {}
    except Exception as e:
        print(f"    Error reading {json_path}: {e}")
        return {}

def update_title_and_hed(summary_df, datasets_base_dir="../datasets/dataset_repos"):
    """
    Update title and HED columns from dataset_description.json files.

    Args:
        summary_df (pd.DataFrame): Summary DataFrame to update.
        datasets_base_dir (str): Base directory containing dataset folders.

    Returns:
        pd.DataFrame: Updated DataFrame with title and HED information.
    """
    updated_df = summary_df.copy()

    # Add HED column after readme column if it doesn't exist
    if 'HED' not in updated_df.columns:
        readme_idx = updated_df.columns.get_loc('readme')
        # Insert HED column after readme
        cols = updated_df.columns.tolist()
        cols.insert(readme_idx + 1, 'HED')
        updated_df = updated_df.reindex(columns=cols)
        updated_df['HED'] = ''

    # Ensure title column exists and is empty initially
    if 'title' not in updated_df.columns:
        updated_df['title'] = ''

    # Convert string columns to object dtype to avoid pandas dtype errors
    # when assigning strings to columns that were inferred as numeric
    for col in ['title', 'HED', 'tasks', 'modalities', 'contact', 'notes']:
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
            if 'Name' in desc_data:
                # Remove newlines and replace with spaces
                title = desc_data['Name'].replace('\n', ' ').strip()
                updated_df.at[idx, 'title'] = title
                print(f"    Title: {title}")
            else:
                print("    No 'Name' field found in dataset_description.json")

            # Update HED from 'HEDVersion' field
            if 'HEDVersion' in desc_data:
                updated_df.at[idx, 'HED'] = desc_data['HEDVersion']
                print(f"    HED Version: {desc_data['HEDVersion']}")
            else:
                print("    No HEDVersion found")
        else:
            print("    No dataset_description.json found or error reading file")

    return updated_df

def read_dataframes(summary_path, citations_path):
    """
    Read the summary and citations TSV files into pandas DataFrames.

    Args:
        summary_path (str): Path to the dataset_summary.tsv file.
        citations_path (str): Path to the dataset_citations.tsv file.

    Returns:
        tuple: (summary_df, citations_df) or (None, None) if error.
    """
    try:
        # Read dataset summary
        summary_df = pd.read_csv(summary_path, sep='\t')
        print(f"Loaded {len(summary_df)} entries from {summary_path}")

        # Read dataset citations
        citations_df = pd.read_csv(citations_path, sep='\t')
        print(f"Loaded {len(citations_df)} citations from {citations_path}")

        return summary_df, citations_df

    except Exception as e:
        print(f"Error reading files: {e}")
        return None, None

def sort_dataframes(summary_df, citations_df):
    """
    Sort both DataFrames by their first column in descending order.

    Args:
        summary_df (pd.DataFrame): Dataset summary DataFrame.
        citations_df (pd.DataFrame): Dataset citations DataFrame.

    Returns:
        tuple: (sorted_summary_df, sorted_citations_df)
    """
    # Get the first column names
    summary_first_col = summary_df.columns[0]
    citations_first_col = citations_df.columns[0]

    print(f"Sorting by: {summary_first_col} (summary), {citations_first_col} (citations)")

    # Sort both DataFrames in descending order
    sorted_summary = summary_df.sort_values(by=summary_first_col, ascending=False).reset_index(drop=True)
    sorted_citations = citations_df.sort_values(by=citations_first_col, ascending=False).reset_index(drop=True)

    print(f"Sorted summary: {len(sorted_summary)} entries")
    print(f"Sorted citations: {len(sorted_citations)} entries")

    return sorted_summary, sorted_citations

def merge_citations_efficiently(summary_df, citations_df):
    """
    Efficiently merge citation links into the summary DataFrame.

    Args:
        summary_df (pd.DataFrame): Sorted dataset summary DataFrame.
        citations_df (pd.DataFrame): Sorted dataset citations DataFrame.

    Returns:
        pd.DataFrame: Updated summary DataFrame with citation links.
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
    if 'links' not in updated_summary.columns:
        updated_summary['links'] = 0
    else:
        updated_summary['links'] = updated_summary['links'].fillna(0).astype(int)

    links_updated = 0

    for idx, row in updated_summary.iterrows():
        dataset_id = row[summary_key_col]

        if dataset_id in citations_dict:
            # Store the count of links instead of the actual links
            link_count = len(citations_dict[dataset_id])
            updated_summary.at[idx, 'links'] = link_count
            links_updated += 1
            print(f"  Updated {dataset_id}: {link_count} links")
        else:
            # Ensure no links datasets have 0 instead of empty string
            updated_summary.at[idx, 'links'] = 0

    print(f"Updated links for {links_updated} datasets")
    return updated_summary

def save_updated_summary(updated_df, output_path):
    """
    Save the updated summary DataFrame to a TSV file.

    Args:
        updated_df (pd.DataFrame): Updated summary DataFrame.
        output_path (str): Path to save the output file.
    """
    try:
        updated_df.to_csv(output_path, sep='\t', index=False)
        print(f"Updated summary saved to {output_path}")
    except Exception as e:
        print(f"Error saving file: {e}")

def print_update_summary(original_df, updated_df):
    """Print a summary of the update process."""
    print("\n" + "="*50)
    print("UPDATE SUMMARY")
    print("="*50)

    total_datasets = len(updated_df)
    # Check for datasets with links (count > 0)
    datasets_with_links = len(updated_df[updated_df['links'] > 0])
    datasets_without_links = total_datasets - datasets_with_links

    print(f"Total datasets: {total_datasets}")
    print(f"Datasets with citation links: {datasets_with_links}")
    print(f"Datasets without citation links: {datasets_without_links}")

    if datasets_with_links > 0:
        print("\nSample datasets with links:")
        sample_with_links = updated_df[updated_df['links'] > 0].head(3)
        for _, row in sample_with_links.iterrows():
            dataset_name = row[updated_df.columns[0]]
            num_links = row['links']
            print(f"  {dataset_name}: {num_links} link(s)")

    print("="*50)

# --- Example Usage ---
if __name__ == "__main__":
    # Load environment variables from .env file
    load_dotenv()

    # Configuration
    summary_path = "../datasets/dataset_summaries/dataset_summary.tsv"
    citations_path = "../datasets/dataset_summaries/dataset_citations.tsv"
    datasets_dir = "../datasets/dataset_repos"
    output_path = "../datasets/dataset_summaries/dataset_summary_updated.tsv"

    print("Updating dataset summary with citation links...")
    print(f"Summary file: {os.path.abspath(summary_path)}")
    print(f"Citations file: {os.path.abspath(citations_path)}")
    print(f"Output file: {os.path.abspath(output_path)}")

    # Check if input files exist
    if not os.path.exists(summary_path):
        print(f"Error: {summary_path} not found.")
        exit(1)

    if not os.path.exists(citations_path):
        print(f"Error: {citations_path} not found.")
        exit(1)

    # Read the DataFrames
    summary_df, citations_df = read_dataframes(summary_path, citations_path)

    if summary_df is None or citations_df is None:
        print("Failed to read input files.")
        exit(1)

    # Sort the DataFrames
    sorted_summary, sorted_citations = sort_dataframes(summary_df, citations_df)

    # Update title and HED information from dataset_description.json files
    updated_summary = update_title_and_hed(sorted_summary, datasets_dir)

    # Merge citations efficiently
    final_summary = merge_citations_efficiently(updated_summary, sorted_citations)

    # Save the updated summary
    save_updated_summary(final_summary, output_path)

    # Print summary
    print_update_summary(summary_df, final_summary)

    print("\nDataset summary update complete!")
