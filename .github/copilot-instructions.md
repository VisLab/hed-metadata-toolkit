# HED-Metadata-Toolkit developer instructions

This file is the architecture reference, written for GitHub Copilot. The short, durable rules also live in `CLAUDE.md` at the repo root, which is what Claude Code reads. When the two disagree, `CLAUDE.md` is authoritative - keep the sections below descriptive and leave policy to that file.

**Machine-specific facts are in `.status/local-environment.md`** - interpreter and full path to it, where the consumer repos and the shared cache live, and local quirks. That file is gitignored, so it is absent from clones and from CI; read it when it exists and ignore its absence when it does not. It is written for any assistant, not one in particular. Nothing in this repo's committed files may contain a local path or drive letter: the repo is public.

## Code style

- Google-style docstrings; use `Parameters:` not `Args:`
- `ruff format` is the authority on formatting. No `line-length` is configured, so it wraps at ruff's default of 88. `E501` is disabled in `pyproject.toml`, so long strings, URLs, and unsplittable comments may exceed that
- Markdown headers use sentence case: capitalize only the first word (and proper nouns/acronyms)
- **All imports must be at the module level** - no local imports inside functions. Place all imports at the top of the file
- **Every text-mode file write must pass `newline=""`.** Otherwise Python translates `\n` to `\r\n` on Windows and the toolkit writes CRLF into the consumer repos. Binary writes (`"wb"`) for downloaded dataset content are unaffected

## Project overview

**hed-metadata-toolkit** is a Python library for managing citations and metadata related to HED (Hierarchical Event Descriptors) datasets. It provides tools for:

- **Citation management**: Collecting, normalizing, and enriching publication citations from multiple sources
- **API client integration**: Unified clients for Crossref, OpenAlex, EuropePMC, PMC, OSF, Semantic Scholar, Unpaywall
- **Dataset discovery**: Syncing and organizing datasets from GitHub and OSF repositories
- **Citation normalization**: URL/DOI canonicalization, junk-link detection, DOI synthesis from URLs
- **Publication ID generation**: Building canonical identifiers for citations

### Package distribution

- **Distribution**: consumers install from a pinned git tag, as documented in `README.md` (`hed-metadata-toolkit @ git+https://github.com/hed-standard/hed-metadata-toolkit.git@v0.1.0`). Local development uses `pip install -e`; note that a non-editable install means consumers only pick up a toolkit fix after reinstalling.
- **Python Version**: 3.10+ required
- **Source**: `src/hed_metadata_toolkit/` (src layout; import as `hed_metadata_toolkit`)

## Architecture & module structure

**Core modules** (`src/hed_metadata_toolkit/`):

- **`citations/`**: Citation workflow modules

  - `collect_citations.py`: Gather citations from datasets
  - `assign_citation_ids.py`: Generate unique IDs for citations
  - `apply_manual_fills.py`: Apply manual citation metadata overrides
  - `enrich_pub_ids.py`: Enrich citations with additional metadata (URL synthesis, DOI canonicalization)
  - `generate_review_queue.py`: Create review tasks for unresolved citations
  - `convert_pdfs.py`: PDF -> Markdown via the `marker_single` CLI (needs the optional `pdf` extra)

- **`clients/`**: API client implementations for citation sources

  - `crossref.py`: Crossref API client for journal articles
  - `openalex.py`: OpenAlex API client
  - `europepmc.py`: EuropePMC API client
  - `pmc.py`: PubMed Central API client
  - `osf.py`: Open Science Framework API client
  - `semanticscholar.py`: Semantic Scholar API client
  - `unpaywall.py`: Unpaywall API client for OA status

- **`dataset_summary/`**: Dataset metadata and organization

  - `extract_summary_info.py`: Extract metadata from datasets
  - `extract_readme_summaries.py`: Build the README summary corpus
  - `sort_datasets.py`: Organize datasets by criteria
  - `update_summary.py`: Update dataset metadata

- **`github/`**: GitHub organization management

  - `fetch_repo_list.py`: List repositories in an organization
  - `sync_repo_contents.py`: Sync repository contents locally
  - `sync_repo_file_contents.py`: Download the contents of selected files
  - `sync_local_files.py`: Download each repo's top-level blobs into `datasets/dataset_repos/<repo>/` (SHA-based incremental skip; nothing is ever uploaded)
  - `list_event_files.py`: Enumerate BIDS `*_events.tsv` files per dataset
  - `bids_tree.py`: Pure helpers deriving `subjects` / `datatypes` / `event_files` from a recursive git tree

- **`cache.py`**: Caching layer for API responses with date-stamped buckets

- **`citation_identity.py`**: Generate canonical IDs and filenames for citations

- **`citation_normalize.py`**: Normalize URLs/DOIs, detect junk links, synthesize DOIs

## Development environment

### Setup

Install in editable mode:

```bash
pip install -e .
```

Or with development dependencies:

```bash
pip install -e ".[dev]"
```

### Package structure

- Entry point: `src/hed_metadata_toolkit/__init__.py` exports main API
- Configuration: `pyproject.toml` (build, project metadata, tool configs)
- Tests: `tests/` directory with pytest-based test suite
- Fixtures: `tests/fixtures/` contains API response fixtures
- Config: `config/` directory for runtime configuration files

### Dependencies

Declared in `pyproject.toml`; read them there rather than here, so a version pin cannot drift between two files. The extras are `pdf` (heavy, opt-in, needed only by `hed-convert-pdfs`), `test`, `docs`, and `dev`. There is no coverage plugin in any extra.

### Running tests

`CLAUDE.md` holds the authoritative command table, including the lint, format, and markdown checks that CI also runs. Do not restate pass or skip counts anywhere: they change with every commit and a stale count reads as a target.

```bash
# The gate
python -m pytest tests -v

# Single test file
python -m pytest tests/test_cache.py -v

# Single test
python -m pytest tests/test_cache.py::test_round_trip_writes_and_reads_back -v

# Integration tests: opt-in, real network, needs GITHUB_TOKEN
python -m pytest tests/integration/ --integration -v
```

**Test structure**:

- Tests use pytest framework with parametrization (`@pytest.mark.parametrize`)
- Fixtures in `tests/fixtures/` directory (API responses, config files)
- Temporary directories via `tmp_path` fixture
- Mocking via `unittest.mock.patch`

### Key patterns

**API client usage**:

```python
from hed_metadata_toolkit.cache import cache_get_or_fetch
from hed_metadata_toolkit.clients import crossref

# Clients use cache_get_or_fetch for responses
result = crossref.lookup_by_doi("10.1038/s41597-022-01407-x", cache_dir="/tmp")
if result:
    print(result["title"])  # List of title strings
    print(result["author"])  # List of author dicts with 'family', 'given' keys
```

**Citation identity generation**:

```python
from hed_metadata_toolkit.citation_identity import build_pub_id, build_canonical_string

# Generate canonical identifiers
pub_id = build_pub_id(family="Markiewicz", year=2021, title="OpenNeuro: ...")
canonical = build_canonical_string(family="Markiewicz", year=2021, title="OpenNeuro: ...")
```

**Citation normalization**:

```python
from hed_metadata_toolkit.citation_normalize import (
    canonicalize_doi,
    canonicalize_url,
    is_junk_link,
    synthesise_doi_from_url,
)

# Canonicalize identifiers
canonical_doi = canonicalize_doi("https://doi.org/10.1234/xyz")
canonical_url = canonicalize_url("https://Example.ORG/Foo/")
doi = synthesise_doi_from_url("https://nature.com/articles/s41598-021-00001-1")
junk = is_junk_link("https://github.com/some/repo")
```

## Common patterns

### Cache structure

The cache system uses date-stamped buckets:

- Location: `~/.hed_cache/` (configurable via `HED_CACHE_DIR` env var)
- Format: `<source>/<date>/<key>.json` (e.g., `crossref/2026-06-09/10.1038_s41597-022-01407-x.json`)
- Responses cached with metadata including fetch timestamp and source key

### Citation workflow

1. **Collect**: Gather citations from dataset metadata via `collect_citations`
2. **Assign IDs**: Generate unique citation IDs via `assign_citation_ids`
3. **Enrich**: Add metadata (DOI synthesis, URL canonicalization) via `enrich_pub_ids`
4. **Manual fills**: Apply human-curated overrides via `apply_manual_fills`
5. **Review**: Generate review queue for unresolved citations via `generate_review_queue`

## Common pitfalls to avoid

- API clients require cache_dir parameter for all network requests
- Fixture files in `tests/fixtures/` must match expected structure (source/filename.json)
- Cache responses are JSON; ensure metadata fields are preserved through transformations
- DOI/URL canonicalization removes trailing punctuation, whitespace, and fragments
- OSF project DOIs (10.17605/OSF.IO/...) should be filtered from publication DOIs
- `repo_contents.json` has one schema: `top_level_files`/`subjects`/`datatypes`/`event_files`. The pre-2026-06-15 flat `entries` list is no longer read (dropped 2026-08-06 with `openneuro-metadata`); a stale file yields zero rows and a warning. `tests/test_consumers_schema.py` enforces this
- `--org` is required on every `github/` command and `organization` has no default in the library functions; `--prefix` defaults to `nm` and `on`
- The `openneuro.org` and `doi:10.18112/openneuro.` skip-list patterns are live citation rules, not leftovers - most nemar datasets are mirrored OpenNeuro datasets. Do not remove them
- A change to a public function or to a JSON schema is a breaking change for `nemar-metadata` and `task-research` even when the tests here pass
