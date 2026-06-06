import pandas as pd
import os
from dotenv import load_dotenv

def read_dataset_summary(summary_path):
    """
    Read the dataset summary TSV file into a pandas DataFrame.

    Args:
        summary_path (str): Path to the dataset_summary_updated.tsv file.

    Returns:
        pd.DataFrame: Dataset summary DataFrame, or None if error.
    """
    try:
        df = pd.read_csv(summary_path, sep='\t')
        print(f"Loaded {len(df)} datasets from {summary_path}")
        return df
    except Exception as e:
        print(f"Error reading file: {e}")
        return None

def sort_datasets(df):
    """
    Sort datasets by HED, links, events, and name columns in reverse order.
    Treats empty lists [] in HED column as empty values.

    Args:
        df (pd.DataFrame): Dataset summary DataFrame.

    Returns:
        pd.DataFrame: Sorted DataFrame.
    """
    print("Sorting datasets by HED, links, events, and name (all in reverse order)...")

    # Define the sort columns in the new order
    sort_columns = ['HED', 'links', 'events', 'name']

    # Verify all columns exist
    missing_columns = [col for col in sort_columns if col not in df.columns]
    if missing_columns:
        print(f"Warning: Missing columns in dataset: {missing_columns}")
        # Use only existing columns
        sort_columns = [col for col in sort_columns if col in df.columns]

    print(f"Sorting by columns: {sort_columns}")

    # Create a copy of the DataFrame for sorting
    df_for_sort = df.copy()

    # Handle HED column - treat empty lists as empty strings for sorting
    if 'HED' in df_for_sort.columns:
        def clean_hed_value(value):
            # Convert various empty representations to empty string
            if pd.isna(value) or value == '' or str(value).strip() == '[]' or str(value).strip() == '':
                return ''
            return str(value)

        df_for_sort['HED'] = df_for_sort['HED'].apply(clean_hed_value)
        print("Cleaned HED column - empty lists and NaN values converted to empty strings")

    # Sort by all specified columns in descending order
    sorted_df = df_for_sort.sort_values(by=sort_columns, ascending=False).reset_index(drop=True)

    print(f"Sorted {len(sorted_df)} datasets")
    return sorted_df

def save_sorted_datasets(sorted_df, output_path):
    """
    Save the sorted DataFrame to a TSV file.

    Args:
        sorted_df (pd.DataFrame): Sorted dataset DataFrame.
        output_path (str): Path to save the output file.
    """
    try:
        sorted_df.to_csv(output_path, sep='\t', index=False)
        print(f"Sorted datasets saved to {output_path}")
    except Exception as e:
        print(f"Error saving file: {e}")

def print_sort_summary(original_df, sorted_df):
    """Print a summary of the sorting process."""
    print("\n" + "="*50)
    print("DATASET SORTING SUMMARY")
    print("="*50)

    total_datasets = len(sorted_df)
    print(f"Total datasets processed: {total_datasets}")

    if total_datasets > 0:
        print("\nTop 5 datasets after sorting:")
        top_datasets = sorted_df.head(5)
        for idx, row in top_datasets.iterrows():
            name = row['name']
            links = row.get('links', 'N/A')
            events = row.get('events', 'N/A')
            hed = row.get('HED', 'N/A')
            print(f"  {idx+1}. {name} (HED: {hed}, links: {links}, events: {events})")

        print("\nBottom 5 datasets after sorting:")
        bottom_datasets = sorted_df.tail(5)
        for idx, row in bottom_datasets.iterrows():
            name = row['name']
            links = row.get('links', 'N/A')
            events = row.get('events', 'N/A')
            hed = row.get('HED', 'N/A')
            print(f"  {len(sorted_df)-len(bottom_datasets)+idx-bottom_datasets.index[0]+1}. {name} (HED: {hed}, links: {links}, events: {events})")

    print("="*50)

# --- Example Usage ---
if __name__ == "__main__":
    # Load environment variables from .env file
    load_dotenv()

    # Configuration
    input_path = "../datasets/dataset_summaries/dataset_summary_updated.tsv"
    output_path = "../datasets/dataset_summaries/dataset_summary_sorted.tsv"

    print("Sorting datasets by HED, links, events, and name...")
    print(f"Input file: {os.path.abspath(input_path)}")
    print(f"Output file: {os.path.abspath(output_path)}")

    # Check if input file exists
    if not os.path.exists(input_path):
        print(f"Error: {input_path} not found.")
        exit(1)

    # Read the dataset summary
    dataset_df = read_dataset_summary(input_path)

    if dataset_df is None:
        print("Failed to read input file.")
        exit(1)

    # Sort the datasets
    sorted_datasets = sort_datasets(dataset_df)

    # Save the sorted datasets
    save_sorted_datasets(sorted_datasets, output_path)

    # Print summary
    print_sort_summary(dataset_df, sorted_datasets)

    print("\nDataset sorting complete!")
