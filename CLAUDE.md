<!--
  COMMITTED AND PUBLIC. Every line must be true on any machine and on Linux CI.
  No drive letters, no absolute paths, no secrets, no "on my machine".
  Machine-specific facts belong in CLAUDE.local.md (gitignored).
  HTML comments are stripped before Claude sees this file, so notes are free.
  Target: under 200 lines.
-->

# hed-metadata-toolkit

Shared Python library for the HED metadata repositories: a disk cache for bibliographic API responses, seven API clients, citation identity/normalization, and GitHub-org dataset pipelines.

**This repo is logic only.** Configuration and data live in the consumer repos. There are no datasets, no citation TSVs, and no `config.toml` here.

## Commands

Run from the repo root. `python` must be an interpreter with this package installed (`pip install -e ".[dev]"`).

| Task                             | Command                                                                          |
| -------------------------------- | -------------------------------------------------------------------------------- |
| Unit tests (the gate)            | `python -m pytest tests -v`                                                      |
| One test file                    | `python -m pytest tests/test_cache.py -v`                                        |
| One test                         | `python -m pytest tests/test_cache.py::test_round_trip_writes_and_reads_back -v` |
| Lint                             | `python -m ruff check .`                                                         |
| Format check                     | `python -m ruff format --check .`                                                |
| Markdown format check            | `python -m mdformat --check --wrap no --number *.md`                             |
| Integration tests (real network) | `python -m pytest tests/integration/ --integration -v`                           |

Expected baseline: **526 passed, 4 skipped** (measured 2026-08-06). The 4 skips are the integration tests, which need `--integration` and a `GITHUB_TOKEN`; they hit the live GitHub API and are not part of the normal gate.

CI runs the same four checks on Ubuntu across Python 3.10-3.14 (`.github/workflows/`), using `uvx ruff` and `uvx mdformat` rather than a venv. If the four commands above pass locally, CI passes.

## Layout

- `src/hed_metadata_toolkit/` - the package (src layout; import as `hed_metadata_toolkit`)
  - `cache.py` - immutable date-bucketed disk cache; every client goes through it
  - `citation_identity.py`, `citation_normalize.py` - pure functions, no I/O, no network
  - `clients/` - one module per API: Crossref, OpenAlex, Europe PMC, PMC, OSF, Semantic Scholar, Unpaywall
  - `citations/`, `dataset_summary/`, `github/` - pipeline steps, each with a `main()`
- `tests/` - pytest; `tests/fixtures/<source>/<key>.json` are recorded API responses
- `tests/integration/` - opt-in, network-touching
- `config/` - `citation_skip_list.txt` only
- `.status/` - `README.md`, `decisions.md`, `plans/`, `notes/`, `archive/`, `scratch/`. **Gitignored; local to each machine.**

Every pipeline step is exposed as a console script in `[project.scripts]` (`hed-collect-citations`, `hed-sync-repo-contents`, ...). Consumer repos call these commands; they do not vendor copies. Adding or renaming a step means editing `pyproject.toml` and `tests/test_cli_entry_points.py`.

## Conventions that differ from defaults

- **Google-style docstrings, but the section header is `Parameters:`, not `Args:`.**
- **All imports at module level.** No imports inside functions, including in tests.
- **Markdown headers are sentence case** - capitalize the first word only, plus proper nouns and acronyms.
- **ASCII only** in prose, comments, docstrings, and filenames: `-` not em/en dashes, `->` not arrows, `...` not an ellipsis character, `section` not a section sign, straight quotes, and `|--` rather than box-drawing characters in tree diagrams. **The exception is genuine data, and it is narrow.** Three places legitimately hold non-ASCII and must not be "cleaned": the accented-surname and non-Latin-title fixtures in `tests/test_citation_identity.py`, the docstring examples in `citation_identity.py` that document how those two fold, and any recorded API response under `tests/fixtures/`. Author names and dataset titles arriving from an API keep whatever characters they contain - folding them is `citation_identity`'s job, not the writer's.
- Root-level `*.md` is formatted by `mdformat --wrap no --number` and is CI-checked. Files under `.github/` are not.
- **IMPORTANT: no committed file may record which work session produced something.** No `Session 2C`, no `Phase 2.5A`, no `PR-G`, no pointer to a plan or thinking document. Docstrings, comments, and READMEs state what the code does and why it is that way; the reader is a stranger on GitHub, to whom a phase label is noise and a working-note filename is a dead end. If a note holds the only explanation, move the explanation into the docstring rather than citing the note.
- **IMPORTANT: no committed file may reference `.status/`.** Not `README.md`, not a docstring, not a comment. `.status/` is gitignored, so any such pointer is a dead link for every reader except the machine that wrote it, and this repo is public. Rationale that deserves to ship goes in the module docstring or the README; if it is only working notes, cite nothing. Exactly three committed files may name it: `.gitignore` (which ignores it), `.claude/settings.json` (which denies reads under it), and `.github/copilot-instructions.md` (which points other assistants at `.status/local-environment.md`). No committed file may reference any other path under it, and none may contain a local path or drive letter.
- `ruff format` is the authority on Python formatting. No `line-length` is configured, so it wraps at ruff's default of 88. `E501` is disabled in `pyproject.toml`, so long strings, URLs, and comments that the formatter cannot split are allowed to stay long.

## Gotchas

- **IMPORTANT: every text-mode write must pass `newline=""`.** Without it, Python's text mode turns `\n` into `\r\n` on Windows, the toolkit writes CRLF into consumer repos, and a second pass produces `\r\r\n` corruption. This was fixed across every writer in the toolkit; do not reintroduce it. Downloaded dataset content is written binary (`"wb"`) and is unaffected. `.gitattributes` (`* text=auto eol=lf`) is the backstop, not the fix.
- **`repo_contents.json` has one shape: `top_level_files` / `subjects` / `datatypes` / `event_files`.** The pre-2026-06-15 flat `entries` list is no longer read - support for it went out with `openneuro-metadata` on 2026-08-06. A stale file now yields zero rows and a warning telling you to re-run `hed-sync-repo-contents`, rather than half-populated output. `tests/test_consumers_schema.py` is the guard.
- **The field is `datatypes`, not `modalities`** - it holds BIDS datatype directory names found under a top-level `sub-*` directory. `phenotype` is deliberately excluded, and `derivatives/` is ignored.
- **`--org` is required on every `github/` command; there is no default organization.** Same for the library functions - `organization` has no default. A wrong or missing org used to 404 silently and leave empty dataset directories behind. `--prefix` defaults to `nm` and `on` (`DEFAULT_PREFIXES`), the two prefixes present in `NemarDatasets`.
- **`openneuro.org` and `doi:10.18112/openneuro.` in `config/citation_skip_list.txt` are not leftovers - do not remove them.** They express "a link to a dataset is not a publication", and 543 of nemar's 734 datasets are mirrored OpenNeuro datasets whose READMEs carry exactly those links. `tests/test_citation_normalize.py` pins both patterns. The OpenNeuro *repo* is gone; OpenNeuro as *citation data* is very much still here.
- The cache is keyed by identifier, not by dataset. Two datasets can share a citation entry.
- API clients require a `cache_dir`. The cache root comes from `HED_CACHE_DIR`, defaulting to `~/.hed_cache`. Cache buckets are date-stamped and treated as immutable - do not rewrite an existing bucket to "refresh" it.
- `GITHUB_TOKEN` is read from `.env` at the repo root via `python-dotenv`. `.env` is gitignored and `Read(.env)` is denied in `.claude/settings.json`. Never echo it, never paste it into a report or a `.status/` file.
- OSF project DOIs (`10.17605/OSF.IO/...`) are not publication DOIs and are filtered out.
- `ci.yaml` runs `python -m pytest spec_tests` with `continue-on-error: true`. There is no `spec_tests` directory; that step always fails silently. Ignore it, or delete it - it is not a signal.

## Consumer repositories

Referred to by name, never by path - paths are machine-specific and live in `CLAUDE.local.md`.

- `nemar-metadata` - catalogues the `NemarDatasets` org, which holds both native NEMAR datasets (`nm*`, 191) and OpenNeuro datasets mirrored into it (`on*`, 543). Built on this toolkit from the start; the only GitHub-org consumer.
- `task-research` - cognitive-process catalogue; uses only cache, clients, and citation identity.

A change to a function these repos call, or to a JSON schema they read, is a breaking change even when tests here pass. Say so explicitly rather than assuming it is safe.

## Where the thinking lives

`.status/` is gitignored, so it exists only on this machine and never in a fresh worktree or clone.

- `.status/README.md` - the index. Read this first; it lists what is active.
- `.status/decisions.md` - why things are the way they are. Read before proposing structural changes. Append; never rewrite an existing entry.
- `.status/plans/*.md` - active plans. Check the `Status:` header and the `[ ]` / `[x]` markers before starting work.
- `.status/notes/*.md` - dated records of what happened. Write-once reference material, not instructions.
- `.status/local-environment.md` - this machine's paths, interpreter, and quirks. Tool-agnostic on purpose: more than one AI assistant works in this repo and only Claude Code reads `CLAUDE.local.md`, so machine facts live in one file that anything can be pointed at. Never move them into a committed file - this repo is public.
- IMPORTANT: do not read `.status/archive/` unless a file is named for you; it is finished work and reading it wastes the context window. Nothing new is created at the `.status/` root - new material goes in `plans/`, `notes/`, or `scratch/`.

## Working agreements

- IMPORTANT: never delete or rewrite a file under `.status/` without asking first. Appending is fine.
- Show evidence, not assertions: the command you ran and its actual output. For metadata work, include counts and a sample of records.
- For a change spanning more than three files, write a plan to `.status/` and stop for review before editing.
- When you are guessing about an external API's response shape, say so. Prefer adding a fixture under `tests/fixtures/` over guessing twice.
- Do not commit, push, or create branches unless asked.
