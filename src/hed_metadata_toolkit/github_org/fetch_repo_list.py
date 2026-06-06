import requests
import pandas as pd
import time
import os
from dotenv import load_dotenv

def get_github_organization_repositories(organization, token=None):
    """
    Retrieves a list of tuples (repository name, updated_at) for a GitHub organization.

    To avoid rate limiting or to access private repositories, you should use a
    personal access token.

    Args:
        organization (str): The name of the GitHub organization.
        token (str, optional): A GitHub personal access token for authentication.
                               Defaults to None.

    Returns:
        list: A list of tuples (name, updated_at). Returns an empty list if the
              organization is not found or an error occurs.
    """
    repos = []
    page = 1
    per_page = 100  # GitHub API max per page
    api_url = f"https://api.github.com/orgs/{organization}/repos"

    headers = {
        "Accept": "application/vnd.github.v3+json"
    }
    if token:
        headers["Authorization"] = f"token {token}"

    while True:
        params = {"per_page": per_page, "page": page, "type": "all"}
        try:
            response = requests.get(api_url, headers=headers, params=params)
            response.raise_for_status()  # Raises an HTTPError for bad responses (4XX or 5XX)

            current_page_repos = response.json()
            if not current_page_repos:
                # No more repositories to fetch
                break

            repos.extend((repo['name'], repo['updated_at']) for repo in current_page_repos)

            # If the number of repos returned is less than per_page, it's the last page
            if len(current_page_repos) < per_page:
                break

            page += 1
            # To avoid hitting the rate limit too quickly, sleep for a short duration
            time.sleep(5)
            print(page)


        except requests.exceptions.HTTPError as http_err:
            print(f"HTTP error occurred: {http_err}")
            if response.status_code == 404:
                print(f"Organization '{organization}' not found.")
            return []
        except requests.exceptions.RequestException as req_err:
            print(f"An error occurred: {req_err}")
            return []

    return repos

# --- Example Usage ---
if __name__ == "__main__":
    # Assumes personal access token is stored in a .env file
    load_dotenv() # Load environment variables from .env file

    # Replace 'google' with the GitHub organization you are interested in.
    org_name = "OpenNeuroDatasets"

     # For higher rate limits or to access private repositories, generate a
    # personal access token from your GitHub settings and provide it here.
    personal_access_token = os.environ.get("GITHUB_TOKEN")
    print(f"Fetching repositories for the '{org_name}' organization...")
    all_repos = get_github_organization_repositories(org_name, token=personal_access_token)

    if all_repos:
        print(f"Successfully retrieved {len(all_repos)} repositories.")

        # Create a pandas DataFrame and save it to a TSV file
        df = pd.DataFrame(all_repos, columns=['name', 'updated_at'])
        df.to_csv("../datasets/dataset_summaries/datasets.tsv", sep='\t', index=False)

        print("Data saved to ../datasets/dataset_summaries/datasets.tsv")

        # To print all repository names and last update time:
        # for repo_name, last_updated in all_repos:
        #     print(f"- {repo_name} (Last updated: {last_updated})")
    else:
        print("Could not retrieve repositories.")
