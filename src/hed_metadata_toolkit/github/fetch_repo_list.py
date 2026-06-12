"""
fetch_repo_list.py — Retrieve repository list from a GitHub organization.

Fetches all repositories from a GitHub organization and stores them in a TSV file
with repository names and updated_at timestamps.

Usage:
    python fetch_repo_list.py [--org ORGANIZATION] [--token TOKEN]
                              [--output PATH]
"""

import argparse
from pathlib import Path

import os
import time

import pandas as pd
import requests
from dotenv import load_dotenv


def get_github_organization_repositories(organization, token=None):
    """Retrieve list of repositories from a GitHub organization.

    To avoid rate limiting or to access private repositories, provide a
    personal access token.

    Parameters:
        organization: The name of the GitHub organization.
        token: GitHub personal access token for authentication (optional).

    Returns:
        List of tuples (name, updated_at). Returns an empty list if the
        organization is not found or an error occurs.
    """
    repos = []
    page = 1
    per_page = 100  # GitHub API max per page
    api_url = f"https://api.github.com/orgs/{organization}/repos"

    headers = {"Accept": "application/vnd.github.v3+json"}
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

            repos.extend(
                (repo["name"], repo["updated_at"]) for repo in current_page_repos
            )

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


# ---------------------------------------------------------------------------
# Library API
# ---------------------------------------------------------------------------


def run_fetch(
    *,
    org_name: str,
    output_path: "Path | str",
    token: "str | None" = None,
) -> int:
    """Fetch every repository in ``org_name`` and write a 2-column TSV.

    Library entry point.  Returns the number of repositories written;
    raises nothing on a successful zero-result run (just returns 0).

    Parameters
    ----------
    org_name
        GitHub organization, e.g. ``"OpenNeuroDatasets"``.
    output_path
        Destination TSV with ``name`` + ``updated_at`` columns.
    token
        Optional GitHub personal-access token.  Falls back to
        ``$GITHUB_TOKEN`` if ``None``.
    """
    if token is None:
        token = os.environ.get("GITHUB_TOKEN")
    repos = get_github_organization_repositories(org_name, token=token)
    if not repos:
        return 0
    df = pd.DataFrame(repos, columns=["name", "updated_at"])
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, sep="\t", index=False)
    return len(repos)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: "list[str] | None" = None) -> int:
    """Argparse wrapper around :func:`run_fetch`."""
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Fetch every repository in a GitHub organization.",
    )
    parser.add_argument(
        "--org",
        default="OpenNeuroDatasets",
        help="GitHub organization name.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("datasets/dataset_summaries/datasets.tsv"),
        help="Destination TSV.",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="GitHub PAT (defaults to $GITHUB_TOKEN).",
    )
    args = parser.parse_args(argv)

    print(f"Fetching repositories for the '{args.org}' organization...")
    n = run_fetch(org_name=args.org, output_path=args.output, token=args.token)

    if n:
        print(f"Successfully retrieved {n} repositories.")
        print(f"Data saved to {args.output.resolve()}")
        return 0
    print("Could not retrieve repositories.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
