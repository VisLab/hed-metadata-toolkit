# HED-Metadata-Toolkit Developer Instructions

## Code style

- Google-style docstrings; use `Parameters:` not `Args:`
- Line length: 120 characters (configured in `pyproject.toml`)
- Markdown headers use sentence case: capitalize only the first word (and proper nouns/acronyms)

## Project overview

**hed-metadata-toolkit** is a Python library for managing citations and metadata related to HED (Hierarchical Event Descriptors) datasets. It provides tools for:

- **Citation management**: Collecting, normalizing, and enriching publication citations from multiple sources
- **API client integration**: Unified clients for Crossref, OpenAlex, EuropePMC, PMC, OSF, Semantic Scholar, Unpaywall
- **Dataset discovery**: Syncing and organizing datasets from GitHub and OSF repositories
- **Citation normalization**: URL/DOI canonicalization, junk-link detection, DOI synthesis from URLs
- **Publication ID generation**: Building canonical identifiers for citations

### Package distribution

- **PyPI Package**: `hed-metadata-toolkit` (install via `pip install hed-metadata-toolkit`)
- **Python Version**: 3.10+ required
- **Source**: `src/hed_metadata_toolkit/`

## Architecture & module structure

**Core modules** (`src/hed_metadata_toolkit/`):

- **`citations/`**: Citation workflow modules
  - `collect_citations.py`: Gather citations from datasets
  - `assign_citation_ids.py`: Generate unique IDs for citations
  - `apply_manual_fills.py`: Apply manual citation metadata overrides
  - `enrich_pub_ids.py`: Enrich citations with additional metadata (URL synthesis, DOI canonicalization)
  - `generate_review_queue.py`: Create review tasks for unresolved citations

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
  - `sort_datasets.py`: Organize datasets by criteria
  - `update_summary.py`: Update dataset metadata

- **`github_org/`**: GitHub organization management
  - `fetch_repo_list.py`: List repositories in an organization
  - `sync_repo_contents.py`: Sync repository contents locally
  - `sync_local_files.py`: Sync local changes to remote

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

- Python 3.10+ required; declared in `pyproject.toml`
- Core: `requests>=2.28.0`, `python-dotenv>=1.0.0`
- Testing: `pytest>=9.0.0`

### Running tests

```bash
# All tests using pytest (391 tests)
python -m pytest tests/ -v

# Single test file
python -m pytest tests/test_cache.py -v

# Single test
python -m pytest tests/test_cache.py::test_round_trip_writes_and_reads_back -v

# Run with coverage
python -m pytest tests/ --cov=src/hed_metadata_toolkit
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

- Always activate the virtual environment before running Python/pip commands
- API clients require cache_dir parameter for all network requests
- Fixture files in `tests/fixtures/` must match expected structure (source/filename.json)
- Cache responses are JSON; ensure metadata fields are preserved through transformations
- DOI/URL canonicalization removes trailing punctuation, whitespace, and fragments
- OSF project DOIs (10.17605/OSF.IO/...) should be filtered from publication DOIs
