<!--
  COMMITTED AND PUBLIC, and the single source of instructions for every AI
  assistant working in this repo. Claude Code reads it through the @AGENTS.md
  import in CLAUDE.md; Copilot reads it directly.

  Write only what a reader needs going forward. No history: no dates, no "this
  was changed", no "used to", no rationale about superseded designs. If a fact
  is only interesting because of how the code got here, it belongs in
  .status/decisions.md, which is gitignored and is the place for that.

  Every line must be true on any machine and on Linux CI: no drive letters, no
  absolute paths, no secrets. Machine facts go in .status/local-environment.md.
  Do not restate anything that changes on its own - test counts, version pins,
  dataset counts. Point at the file that owns the fact. Target: under 200 lines.
-->

# hed-metadata-toolkit

Shared Python library for the HED metadata repositories: a disk cache for bibliographic API responses, seven API clients, citation identity and normalization, and pipelines that catalogue the dataset repositories of a GitHub organization.

**This repo is logic only.** Configuration and data live in the consumer repos. There are no datasets, no citation TSVs, and no `config.toml` here.

## Commands

Run from the repo root. `python` must be an interpreter with this package installed (`pip install -e ".[dev]"`).

| Task                             | Command                                                                          |
| -------------------------------- | -------------------------------------------------------------------------------- |
| Offline tests (the gate)         | `python -m pytest tests -v`                                                      |
| One test file                    | `python -m pytest tests/test_cache.py -v`                                        |
| One test                         | `python -m pytest tests/test_cache.py::test_round_trip_writes_and_reads_back -v` |
| Lint                             | `python -m ruff check .`                                                         |
| Format check                     | `python -m ruff format --check .`                                                |
| Markdown format check            | `python -m mdformat --check --wrap no --number *.md`                             |
| Integration tests (real network) | `python -m pytest tests/integration/ --integration -v`                           |

The integration tests need `--integration` and a `GITHUB_TOKEN`, hit the live GitHub API, and are skipped by the normal gate. CI runs the same four checks on Ubuntu across Python 3.10-3.14 (`.github/workflows/`), using `uvx ruff` and `uvx mdformat`. If the four commands above pass locally, CI passes.

Never quote pass or skip counts in a committed file: they change with every commit, and a stale count reads as a target.

## Layout

- `src/hed_metadata_toolkit/` - the package (src layout; import as `hed_metadata_toolkit`)
  - `cache.py` - disk cache for API responses. Every client goes through `cache_get_or_fetch`; no client calls `requests` directly. Two layouts, chosen per call: `<source>/<date>/<key>.json` for results that drift, `<source>/stable/<key>.json` for lookups that do not. The module docstring is the specification.
  - `citation_identity.py` - builds a `pub_id` and a PDF filename deterministically from (first-author family name, year, title). Pure functions.
  - `citation_normalize.py` - canonicalizes DOIs and URLs, detects links that are not publications, and synthesizes DOIs from publisher URLs. Pure apart from reading the skip list.
  - `clients/` - one module per API, all sharing the same throttle, error, and retry contract: `crossref`, `openalex`, `europepmc`, `pmc`, `osf`, `semanticscholar`, `unpaywall`.
  - `citations/` - `collect_citations` -> `assign_citation_ids` -> `enrich_pub_ids` -> `apply_manual_fills` -> `generate_review_queue`, plus `convert_pdfs` (needs the optional `pdf` extra and the `marker_single` CLI).
  - `dataset_summary/` - `extract_summary_info`, `extract_readme_summaries`, `update_summary`, `sort_datasets`.
  - `github/` - `fetch_repo_list`, `sync_repo_contents`, `sync_local_files`, `sync_repo_file_contents`, `list_event_files`, and `bids_tree` (pure helpers that read a git tree and return the subjects, datatypes, and event files it contains).
- `tests/` - **pytest, not unittest**: plain test functions and fixtures, no `TestCase` classes. `@pytest.mark.parametrize` for cases, `tmp_path` / `monkeypatch` / `capsys` for isolation, and `unittest.mock.patch` where a mock is needed - that is the stdlib mocking library, not the unittest runner. `tests/fixtures/<source>/<key>.json` are recorded API responses; no test touches the network.
- `tests/integration/` - opt-in, network-touching.
- `config/` - `citation_skip_list.txt` only.
- `.status/` - working notes. **Gitignored; local to each machine.**

Every pipeline step is a console script declared in `[project.scripts]` (`hed-sync-repo-contents`, `hed-collect-citations`, ...). Consumer repos run these commands from their own root; they do not vendor copies. Adding or renaming a step means editing `pyproject.toml` and `tests/test_cli_entry_points.py`.

Consumers install from a pinned git tag (see `README.md`). A non-editable install means a consumer picks up a toolkit change only after reinstalling. Dependencies and the extras `pdf`, `test`, `docs`, and `dev` are declared in `pyproject.toml`; there is no coverage plugin in any of them.

## The files the GitHub pipeline reads and writes

None of these files belong to this repo. The commands write them into whatever working directory they are run from - the consumer repo's root - at the default paths below, and every path is overridable with a flag (`--tsv`, `--out`, `--contents`, `--datasets`). What a consumer names them, where it puts them, and whether it tracks them in git are that repo's decisions, not the toolkit's; the toolkit only reads and writes the paths it is given.

**`datasets.tsv`** (default `datasets/dataset_summaries/datasets.tsv`) - the list of repositories in the organization. Two columns, `name` and `updated_at`. Written by `hed-fetch-repo-list`.

**`repo_contents.json`** (default `datasets/dataset_summaries/repo_contents.json`) - **an inventory of what is inside each dataset repository, so the rest of the pipeline can answer questions about a dataset without calling GitHub again.** `hed-sync-repo-contents` builds it with one recursive git-tree request per repository and records, per dataset: the files at the top level, the subject directories, the BIDS datatypes present, and the event files. It downloads no file contents - only names, sizes, and SHAs. It is rebuildable from the organization at any time, which is the only property of it the toolkit guarantees.

It is a JSON object keyed by repository name. Every value has exactly these seven fields:

| Field             | Type                        | Contents                                                                                                                                                                 |
| ----------------- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `synced_at`       | ISO 8601 UTC string         | when this entry was last built                                                                                                                                           |
| `updated_at`      | ISO 8601 UTC string         | the repository's GitHub `updated_at` as of that build. A repository is re-fetched only when a newer `updated_at` appears in `datasets.tsv`; `--force` fetches regardless |
| `truncated`       | boolean                     | true when GitHub capped the tree response, in which case the four lists below may be incomplete                                                                          |
| `top_level_files` | list of `{path, size, sha}` | non-hidden blobs at the repository root, plus blobs under any `--include-subdir`, whose `path` is then `<subdir>/<file>`                                                 |
| `subjects`        | list of string              | names of top-level directories beginning with `sub-`                                                                                                                     |
| `datatypes`       | list of string              | BIDS datatype directory names appearing as a path segment under a top-level `sub-*` directory                                                                            |
| `event_files`     | list of `{path, size, sha}` | files ending `_events.tsv` or `_events.json`, at the repository root or anywhere under a top-level `sub-*` directory                                                     |

`github/bids_tree.py` is the definition of the last four: `derive_repo_metadata` computes them from a git tree, `ALLOWED_DATATYPES` is the set of names admitted as a datatype, and `is_event_file` decides the last. Read that module rather than inferring the fields from a sample file.

Three commands read this inventory instead of the network: `hed-sync-local-files` downloads the files `top_level_files` names, `hed-sync-repo-file-contents` uses `subjects` to find the participant directory to walk, and `hed-extract-summary-info` turns the four lists into the columns of the summary TSV.

**`repo_file_contents.json`** (default `datasets/dataset_summaries/repo_file_contents.json`) - **a sample, not a full inventory, and deliberately so.** `hed-sync-repo-file-contents` picks one subject directory per dataset - the first `participant_id` in that dataset's local `participants.tsv` that matches a name in `subjects` - lists every file in that one directory, and downloads only the `*_events.tsv` and `*_events.json` among them. Its purpose is to see what a subject directory looks like in each dataset and to have a few real event files to hand, at two API calls per dataset. It is the only command that fetches event-file content, and it will never give you all of them; a dataset with 100 subjects contributes one. **Complete event-file discovery is `event_files` in `repo_contents.json`, or `hed-list-event-files`, neither of which downloads anything.** Do not treat a dataset's presence here as coverage, and do not "fix" the single-subject behaviour without asking - it is the intended design.

**Other files the commands write**, each with the same rule that its writer's module docstring is the definition: `event_files.json` and `event_files.tsv` (`hed-list-event-files`), `dataset_summary.tsv` (`hed-extract-summary-info`, columns `name, subjs, links, readme, events, title, tasks, datatypes, contact, notes`), and a `*_failures.json` beside each command's output recording what could not be fetched, where an entry with `"skip": true` is never retried.

## Conventions that differ from defaults

- **Google-style docstrings, but the section header is `Parameters:`, not `Args:`.**
- **All imports at module level.** No imports inside functions, including in tests.
- **Markdown headers are sentence case** - capitalize the first word only, plus proper nouns and acronyms.
- **ASCII only** in prose, comments, docstrings, and filenames: `-` not em/en dashes, `->` not arrows, `...` not an ellipsis character, straight quotes, and `|--` rather than box-drawing characters in tree diagrams. **The exception is genuine data, and it is narrow.** Three places legitimately hold non-ASCII and must not be "cleaned": the accented-surname and non-Latin-title fixtures in `tests/test_citation_identity.py`, the docstring examples in `citation_identity.py` documenting how those fold, and any recorded API response under `tests/fixtures/`. Author names and dataset titles from an API keep whatever characters they contain - folding them is `citation_identity`'s job, not the writer's.
- **Committed files carry no project history.** No dates, no "this was changed", no "previously", no phase or session labels, no pointers to plans or notes. A docstring says what the code does and the rule a reader must follow; the reader is a stranger on GitHub. Rationale about how the code got here goes in `.status/decisions.md`.
- **Nothing that ships may reference `.status/`.** It is gitignored, so such a pointer is a dead link for every reader but its author. The exception is the files whose job is to orient a tool - this file, `CLAUDE.md`, `.github/copilot-instructions.md`, `.gitignore`, and `.claude/settings.json` - which may name `.status/README.md`, `decisions.md`, `plans/`, and `local-environment.md` as places to look.
- **No committed file contains a local path or a drive letter.** Those go in `.status/local-environment.md`.
- **Examples use placeholders.** `REPO_NAME`, not a real dataset ID, in docstrings and help text. Concrete IDs belong in tests, where they are data.
- Root-level `*.md` is formatted by `mdformat --wrap no --number` and is CI-checked. Files under `.github/` are not.
- `ruff format` is the authority on Python formatting: `line-length = 120`, `line-ending = "lf"`, and `E501` disabled so unsplittable strings, URLs, and comments may exceed the limit.

## Rules that are easy to get wrong

- **IMPORTANT: every text-mode write passes `newline=""`.** Without it, Python's text mode turns `\n` into `\r\n` on Windows, writing CRLF into the consumer repos; re-writing such a file through text mode again produces `\r\r\n`. Downloaded dataset content is written binary (`"wb"`) and is exempt. `.gitattributes` (`* text=auto eol=lf`) is a backstop at commit time, not a substitute. Linux CI cannot catch a regression here - verify on Windows.
- **A reader of `repo_contents.json` requires `subjects` to be present in an entry.** An entry lacking it is skipped with a warning naming `hed-sync-repo-contents`, because a partially readable entry would otherwise yield a summary row that looks complete with an empty `datatypes` column. Rebuild rather than hand-edit the file. `tests/test_consumers_schema.py` is the guard.
- **`datatypes` is derived from the directory tree only.** A dataset's `.nemar/metadata.json` also carries a `modalities` list; it is not used, and the two can disagree. That file supplies `title` and `links` and nothing else. `phenotype` is not admitted as a datatype, and `derivatives/` and every other non-`sub-` top-level directory are ignored.
- **`--org` is required on every `github/` command, and `organization` has no default in the library functions.** A wrong organization returns 404 per file and leaves empty dataset directories rather than raising. `--prefix` selects repositories by name and defaults to `DEFAULT_PREFIXES`.
- **`config/citation_skip_list.txt` must contain `openneuro.org` and `doi:10.18112/openneuro.`.** The skip list encodes "this link is not a publication". Most catalogued datasets are OpenNeuro datasets mirrored into the organization, so their READMEs cite exactly those two forms; without both patterns, a dataset's link to itself enters the citation registry as a paper. `tests/test_citation_normalize.py` pins them.
- The cache is keyed by identifier, not by dataset: two datasets can share a citation entry. Clients require a `cache_dir`. The commands resolve it as `--cache-dir`, then `$HED_CACHE_DIR`, then `outputs/cache/` under the directory the command was run from. Date-stamped buckets are immutable - never rewrite one to refresh it.
- Every command loads `.env` from the directory it runs in, via `python-dotenv`. `GITHUB_TOKEN` and `HED_CACHE_DIR` both belong there; `.env.example` documents them. `.env` is gitignored and `Read(.env)` is denied in `.claude/settings.json`. Never echo the token or paste it into a report.
- OSF project DOIs (`10.17605/OSF.IO/...`) register a project, not a paper, and are filtered out of publication candidates.
- A `cit_######` and a `pub_id` do different jobs. The citation ID permanently identifies a row in the registry; the `pub_id` is derived from the publication's own (family, year, title) and is shared with `task-research`. Changing how `citation_identity` folds its input renumbers every `pub_id` in every consumer.

## Consumer repositories

Referred to by name, never by path.

- `nemar-metadata` - catalogues the `NemarDatasets` organization, which holds both native NEMAR datasets and OpenNeuro datasets mirrored into it. The only consumer of the GitHub pipeline.
- `task-research` - cognitive-process catalogue; uses only the cache, the clients, and citation identity.

A change to a function these repos call, or to a file they read, is a breaking change even when the tests here pass. Say so explicitly rather than assuming it is safe.

## Where the thinking lives

`.status/` is gitignored, so it exists only on the machine that wrote it and never in a fresh clone or worktree.

- `.status/README.md` - the index. Read this first; it lists what is active.
- `.status/decisions.md` - why things are the way they are, and the home for anything historical. Read before proposing structural changes. Append entries; never rewrite one.
- `.status/plans/*.md` - active plans. Check the `Status:` header and the `[ ]` / `[x]` markers before starting work.
- `.status/notes/*.md` - dated records of what happened. Write-once reference material, not instructions.
- `.status/local-environment.md` - this machine's paths, interpreter, and quirks. Tool-agnostic, because more than one assistant works here. Never copy its contents into a committed file.
- IMPORTANT: do not read `.status/archive/` unless a file is named for you. Nothing new is created at the `.status/` root - new material goes in `plans/`, `notes/`, or `scratch/`.

## Working agreements

- **IMPORTANT: every file written to `.status/` opens with a `For humans:` summary.** Three or four sentences, at the very top, before any other heading: what this file is, and the one or two things a person needs to take away from it. Everything below it may be written for an assistant to consume; that block is not. Write it plainly - no throat-clearing, no restating the title, no listing what the document will cover. The same applies to a long answer in a session: lead with the conclusion.
- IMPORTANT: never delete or rewrite a file under `.status/` without asking first. Appending is fine.
- Show evidence, not assertions: the command you ran and its actual output. For metadata work, include counts and a sample of records.
- For a change spanning more than three files, write a plan to `.status/plans/` and stop for review before editing.
- When you are guessing about an external API's response, say so. Prefer adding a fixture under `tests/fixtures/` over guessing twice.
- Do not commit, push, or create branches unless asked.
