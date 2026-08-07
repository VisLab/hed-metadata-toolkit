# hed-metadata-toolkit

Shared Python library for the HED metadata repositories. Provides the bits that are common to every repo that catalogues datasets from a GitHub organization and resolves their citations against public bibliographic APIs:

- A **shared on-disk API-response cache** so the same DOI is never looked up twice, even across repos.
- **API clients** for Crossref, OpenAlex, Europe PMC, OSF, PMC, Semantic Scholar, and Unpaywall — all of them backed by the cache and following the same throttle / error / retry contract.
- **Citation identity and normalisation** — canonical DOI/URL forms, publisher-URL→DOI synthesis, deterministic `pub_id` generation from `(family, year, title)`.
- **GitHub-org dataset pipelines** — fetch the repo list, sync top-level file listings, download top-level files, sync per- participant event files.
- **Dataset summary builders** — extract, enrich, and sort the per-dataset TSVs that drive curation.
- **Citation pipeline** — collect raw links from `dataset_description.json` and READMEs, assign stable `cit_######` IDs, build manual-review queues, apply curator decisions, and enrich resolved rows with `pub_id` plus author/year/title from the cached APIs.

All consumer repos point at the same cache root via `HED_CACHE_DIR` and inherit the same canonical implementation, so behaviour stays identical as new metadata sources are added.

______________________________________________________________________

## Consumer repositories

| Repo                                                             | GitHub org it catalogues                      | Status                                                         |
| ---------------------------------------------------------------- | --------------------------------------------- | -------------------------------------------------------------- |
| `nemar-metadata`                                                 | `NemarDatasets`                               | live; the only GitHub-org consumer                             |
| [`task-research`](https://github.com/hed-standard/task-research) | (no GitHub org — cognitive-process catalogue) | consumes the shared subset (cache, clients, citation identity) |

Each consumer holds its own `config.toml`, citation skip list, and data directories (`datasets/`, `citations/`). All pipeline logic lives in this toolkit; the consumer repos are configuration + data.

______________________________________________________________________

## Installation

### As a dependency (production)

In a consumer repo's `pyproject.toml`:

```toml
[project]
dependencies = [
    "hed-metadata-toolkit @ git+https://github.com/hed-standard/hed-metadata-toolkit.git@v0.1.0",
]
```

Pin to a tag (`@v0.1.0`) or to a commit SHA so releases are reproducible. Bump consumers deliberately, one repo at a time.

### Editable install (local development)

From the consumer repo with its venv active:

```bash
pip install -e ../hed-metadata-toolkit
```

Edits in the toolkit flow immediately into every consumer that installed it editably. Run the toolkit's own tests from this repo (`pytest tests/`) before pushing changes — consumers' tests are not sufficient because they exercise only the call paths that consumer uses.

______________________________________________________________________

## Shared API-response cache

Every client in this toolkit goes through `cache.cache_get_or_fetch`, which writes to a configurable cache root using the same layout in every consumer repo. The module docstring of [`src/hed_metadata_toolkit/cache.py`](src/hed_metadata_toolkit/cache.py) is the specification: both bucket layouts, the staleness window, the self-describing file format, and the atomic-write semantics. The root is resolved with this precedence, highest first:

1. `--cache-dir PATH` argument (any script that takes one).
2. `$HED_CACHE_DIR` environment variable.
3. `<consumer-repo>/outputs/cache/` default.

To share the cache across every HED metadata repo on a single machine, set `HED_CACHE_DIR` once in your shell profile:

```powershell
# Windows PowerShell — both lines.  The first persists the value;
# the second makes it visible inside VS Code's integrated terminal.
[Environment]::SetEnvironmentVariable("HED_CACHE_DIR", "H:\HED-cache", "User")
# Then add to $PROFILE:
$env:HED_CACHE_DIR = "H:\HED-cache"
```

```bash
# macOS / Linux / git-bash — append to ~/.bashrc or ~/.zshrc
export HED_CACHE_DIR="$HOME/HED-cache"
```

The directory is created on first write. The cache is **ephemeral** — nothing in it is canonical data. Delete it any time without losing project state; the next run rebuilds whatever it needs.

If VS Code's integrated terminal reports `HED_CACHE_DIR` as empty after you set it, restart the window: the terminal inherits the environment VS Code was launched with, and a `[Environment]::SetEnvironmentVariable(...)` call does not reach an already-running process.

______________________________________________________________________

## What's in the package

```
src/hed_metadata_toolkit/
├── cache.py                  # shared-cache helper (cache_get_or_fetch)
├── citation_identity.py      # deterministic pub_id generation
├── citation_normalize.py     # DOI/URL canonicalisation, skip-list logic
│
├── clients/                  # API clients, all routed through the cache
│   ├── crossref.py
│   ├── europepmc.py
│   ├── openalex.py
│   ├── osf.py
│   ├── pmc.py                # PMC BioC full-text + OA Web Service
│   ├── semanticscholar.py
│   └── unpaywall.py
│
├── github/               # GitHub-organization dataset discovery
│   ├── fetch_repo_list.py    # list every repo in the org
│   ├── sync_repo_contents.py # one recursive git-tree per repo -> repo_contents.json
│   ├── sync_local_files.py   # SHA-based download of top-level blobs
│   └── sync_repo_file_contents.py  # recursive per-participant events
│
├── dataset_summary/          # per-dataset TSV builders
│   ├── extract_summary_info.py
│   ├── update_summary.py     # enrich with titles, HED versions, link counts
│   └── sort_datasets.py
│
└── citations/                # citation pipeline
    ├── collect_citations.py        # extract raw links from descriptions/READMEs
    ├── assign_citation_ids.py      # stable cit_###### assignment (idempotent)
    ├── generate_review_queue.py    # build the curator worksheet
    ├── apply_manual_fills.py       # apply curator decisions back
    └── enrich_pub_ids.py           # resolve DOIs/URLs to pub_id (cache-writing step)
```

Each module is self-contained and importable directly:

```python
from hed_metadata_toolkit.cache import cache_get_or_fetch
from hed_metadata_toolkit.citation_identity import compute_pub_id
from hed_metadata_toolkit.clients import crossref, openalex, europepmc
from hed_metadata_toolkit.citations.enrich_pub_ids import enrich_registry
```

Every pipeline step is also a console command (`hed-fetch-repo-list`, `hed-sync-repo-contents`, `hed-collect-citations`, …), declared in `[project.scripts]` in `pyproject.toml`. Consumer repos call those commands from their own root rather than vendoring wrapper scripts; each step resolves its default paths relative to the current working directory.

______________________________________________________________________

## Testing

```bash
pip install -e .[dev]
pytest tests/
```

The test suite covers every shared module plus the citation pipeline end-to-end with fixture data — no network is touched. All API-client tests inject fake `requests` sessions or stubbed `cache_get_or_fetch` returns.

Run a single file:

```bash
pytest tests/test_cache.py -v
```

______________________________________________________________________

## Development guidelines

- **Pure-function bias.** Modules that take paths as arguments (almost all of them) must NOT hard-code consumer-repo paths. Add a parameter; let the consumer decide.

- **Cache writes go through `cache_get_or_fetch`.** Never bypass it with a direct `requests.get` in client code — the cache convention is the whole point of the toolkit.

- **No absolute Windows paths** in committed files (`C:\Users\…`, `H:\…`). Tests and runtime code use `pathlib` anchored at `Path(__file__).resolve().parent` or accept paths as arguments.

- **Versioning.** Bump `pyproject.toml`'s `version` and tag the commit (`git tag v0.2.0 && git push --tags`) when consumers should pick up new behaviour. Backwards-incompatible changes bump the minor (0.x.0) version until 1.0; patches bump the patch.

- **Tests must pass before tagging.** Consumers update their pinned tag in their own PR after seeing the new version's tests are green.

- **Committed files never reference working notes.** Design rationale that belongs in the published record goes in a module docstring or in this README; local working notes are gitignored and must not be cited from anything that ships, because the reference is dead for every reader but its author.

______________________________________________________________________

## License

MIT — see [`LICENSE`](LICENSE).
