"""github — GitHub organization management.

Modules:
  - fetch_repo_list: Retrieve repository list from a GitHub organization
  - sync_repo_contents: Fetch one recursive git-tree per repo and derive
    top_level_files / subjects / datatypes / event_files
  - sync_local_files: Download top-level files (blobs) with SHA-based incremental skip
  - sync_repo_file_contents: Download *_events files from participant directories
"""
