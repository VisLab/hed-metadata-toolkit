"""enrich_pub_ids.py — Assign pub_ids to citation registry rows.

Two-pass resolver:
  Pass 1 (offline): rows with (family, year, title) populated →
    pub_id computed locally via citation_identity.build_pub_id.
  Pass 2 (network): Paths A–D resolve DOI-bearing and URL-bearing rows
    via Crossref, OpenAlex, Europe PMC, and the OSF API.

Usage:
    python src/enrich_pub_ids.py [--registry PATH] [--write-back]
                                  [--limit N] [--cache-dir PATH]
                                  [--paths A,B,C,D] [--report PATH]
                                  [--dry-run]

Idempotent: rows with pub_id already set are skipped.

Path details (see .status/instructions/phase2_5_resolver.md §Session-2.5C):
  A — DOI lookup via Crossref → OpenAlex; chases preprint→journal relations.
  B — URL → synthesised DOI → then Path A.
  C — PubMed URL → Europe PMC → DOI or bib metadata.
  D — OSF URL → GUID lookup → preprints auto-resolve; nodes/registrations cached
      for 2.5D's review-queue generator (not auto-promoted).

File writes use atomic write-tmp-then-rename + fsync, mirroring
src/cache.py and the post-truncation-fix src/apply_manual_fills.py.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Default data/output paths live under the current working directory (run the
# command from the consumer repo root); all are overridable via CLI flags.
_ROOT = Path.cwd()

from hed_metadata_toolkit.citation_identity import build_pub_id  # noqa: E402
from hed_metadata_toolkit.citation_normalize import synthesise_doi_from_url  # noqa: E402
from hed_metadata_toolkit.clients import crossref, openalex, europepmc, osf  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TERMINAL_STATUSES = {"rejected", "not_a_citation"}
PREPRINT_PREFIXES = ("10.1101/", "10.31234/", "10.31219/")

_DEFAULT_CACHE_DIR = _ROOT / "outputs" / "cache"
_REGISTRY_DEFAULT = _ROOT / "datasets" / "dataset_summaries" / "citation_registry.tsv"

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

_PSYARXIV_RE = re.compile(
    r"^https?://(www\.)?psyarxiv\.(com|org)/(?P<guid>[a-z0-9]+)",
    re.IGNORECASE,
)
_BIORXIV_MEDRXIV_RE = re.compile(
    r"^https?://(www\.)?(bio|med)rxiv\.org/content/(?P<doi>10\.1101/[^?\s]+)",
    re.IGNORECASE,
)
_ELIFE_RE = re.compile(
    r"^https?://(www\.)?elifesciences\.org/articles/(?P<id>\d+)",
    re.IGNORECASE,
)
_PMID_URL_RE = re.compile(
    r"^https?://(www\.)?(ncbi\.nlm\.nih\.gov/pubmed|pubmed\.ncbi\.nlm\.nih\.gov)"
    r"/(?P<pmid>\d+)",
    re.IGNORECASE,
)
_OSF_URL_RE = re.compile(
    r"^https?://(www\.)?osf\.io/(?P<rest>[^?&#\s]+)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def today_iso() -> str:
    return datetime.date.today().isoformat()


def _ascii_fold_lower(s: str) -> str:
    normalized = unicodedata.normalize("NFKD", s)
    return "".join(c for c in normalized if unicodedata.combining(c) == 0).lower()


def _title_token_overlap(t1: str, t2: str) -> float:
    """Jaccard coefficient of lowercased alphanumeric title tokens."""

    def tokens(t: str) -> set[str]:
        folded = _ascii_fold_lower(t)
        return set(re.sub(r"[^a-z0-9]", " ", folded).split())

    a = tokens(t1)
    b = tokens(t2)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _sanity_check(preprint_meta: dict, journal_meta: dict) -> bool:
    """Return True if journal version plausibly matches the preprint.

    Requires at least one of:
    (a) first-author family-name match (ASCII-folded, case-insensitive)
    (b) title token-overlap >= 0.5

    See thinking doc §2.5 for rationale.
    """
    p_fam = _ascii_fold_lower(preprint_meta.get("family") or "")
    j_fam = _ascii_fold_lower(journal_meta.get("family") or "")
    if p_fam and j_fam and p_fam == j_fam:
        return True
    return (
        _title_token_overlap(
            preprint_meta.get("title") or "",
            journal_meta.get("title") or "",
        )
        >= 0.5
    )


# ---------------------------------------------------------------------------
# Metadata extraction from API responses
# ---------------------------------------------------------------------------


def _meta_from_crossref(data: dict) -> dict:
    """Extract (family, year, title) from a Crossref work dict."""
    family = ""
    year = None
    title = ""

    authors = data.get("author", [])
    if authors:
        first = next((a for a in authors if a.get("sequence") == "first"), None)
        if first is None:
            first = authors[0]
        family = first.get("family", "")

    for key in ("published", "published-print", "published-online", "created"):
        pub = data.get(key) or {}
        dp = pub.get("date-parts", [])
        if dp and dp[0]:
            try:
                year = int(dp[0][0])
            except (TypeError, ValueError):
                pass
            break

    titles = data.get("title", [])
    title = titles[0] if titles else ""

    return {"family": family, "year": year, "title": title}


def _meta_from_openalex(data: dict) -> dict:
    """Extract (family, year, title) from an OpenAlex work dict."""
    family = ""
    year = None
    title = ""

    authorships = data.get("authorships", [])
    if authorships:
        first = next(
            (a for a in authorships if a.get("author_position") == "first"), None
        )
        if first is None:
            first = authorships[0]
        if first:
            display = (first.get("author") or {}).get("display_name", "")
            if display:
                # Last whitespace-delimited token as family name
                family = display.rsplit(" ", 1)[-1]

    year = data.get("publication_year")
    title = data.get("title", "")

    return {"family": family, "year": year, "title": title}


def _meta_from_europepmc(data: dict) -> dict:
    """Extract (family, year, title) from a Europe PMC result dict."""
    family = ""
    year = None
    title = ""

    authors = (data.get("authorList") or {}).get("author", [])
    if authors:
        family = authors[0].get("lastName", "")

    pub_year = data.get("pubYear")
    if pub_year:
        try:
            year = int(pub_year)
        except (ValueError, TypeError):
            pass

    title = data.get("title", "")
    return {"family": family, "year": year, "title": title}


# ---------------------------------------------------------------------------
# Relation extraction from Crossref / OpenAlex
# ---------------------------------------------------------------------------


def _extract_relation_doi(cr_data: dict) -> tuple[str | None, str]:
    """Return (journal_doi, chase_via) from Crossref relation fields.

    Priority: is-preprint-of > has-version.
    Returns (None, "") when no usable relation is found.
    """
    relation = cr_data.get("relation") or {}

    for key, via in [
        ("is-preprint-of", "crossref-is-preprint-of"),
        ("has-version", "crossref-has-version"),
    ]:
        for item in relation.get(key) or []:
            if item.get("id-type") == "doi" and item.get("id"):
                doi = item["id"].strip().lower()
                if doi and not osf.is_osf_project_doi(doi):
                    return doi, via

    return None, ""


def _extract_journal_doi_from_openalex(oa_data: dict) -> str | None:
    """Find the published-version DOI from an OpenAlex work's locations array."""
    for loc in oa_data.get("locations") or []:
        version = loc.get("version", "")
        source_type = (loc.get("source") or {}).get("type", "")
        if version == "publishedVersion" or source_type == "journal":
            url = loc.get("landing_page_url", "") or ""
            if "doi.org/" in url:
                doi = url.split("doi.org/", 1)[1].rstrip("/").lower()
                if doi and not osf.is_osf_project_doi(doi):
                    return doi
    return None


# ---------------------------------------------------------------------------
# DOI canonicalization
# ---------------------------------------------------------------------------

_DOI_SUFFIX_PATS = [
    re.compile(r"\.full\.pdf$", re.IGNORECASE),
    re.compile(r"/full$", re.IGNORECASE),
    re.compile(r"/meta$", re.IGNORECASE),
    re.compile(r"/abstract$", re.IGNORECASE),
    re.compile(r"v\d+(\.\w+)?$", re.IGNORECASE),
]
_SDATA_RE = re.compile(r"^10\.1038/sdata(\d{4})(\d+)$")


def _canonicalise_doi(doi: str) -> str:
    """Normalize a DOI: lowercase, strip decoration/version suffixes, fix Sci Data.

    Patterns stripped in order (loop until stable):
      .full.pdf      bioRxiv PDF link suffix
      /full          Frontiers full-text suffix
      /meta          IOP metadata suffix
      /abstract      occasional abstract suffix
      v\\d+(.\\w+)?  bioRxiv version suffix (e.g. v1, v2, v1.abstract, v1.full)

    Also corrects 10.1038/sdataYYYYNNN -> 10.1038/sdata.YYYY.NNN.
    """
    doi = doi.strip().lower()
    changed = True
    while changed:
        changed = False
        for pat in _DOI_SUFFIX_PATS:
            stripped = pat.sub("", doi)
            if stripped != doi:
                doi = stripped
                changed = True
                break
    doi = _SDATA_RE.sub(r"10.1038/sdata.\1.\2", doi)
    return doi


# ---------------------------------------------------------------------------
# URL → DOI synthesis (new Path B patterns)
# ---------------------------------------------------------------------------


def _synth_psyarxiv(url: str) -> str | None:
    """psyarxiv.{com,org}/<guid> → 10.31234/osf.io/<guid>"""
    m = _PSYARXIV_RE.match(url)
    return f"10.31234/osf.io/{m.group('guid')}" if m else None


def _synth_biorxiv_medrxiv(url: str) -> str | None:
    """(bio|med)rxiv.org/content/<doi> → <doi>, stripping trailing v\\d+."""
    m = _BIORXIV_MEDRXIV_RE.match(url)
    if not m:
        return None
    doi = m.group("doi")
    doi = re.sub(r"v\d+$", "", doi, flags=re.IGNORECASE)
    return doi.lower()


def _synth_elife(url: str) -> str | None:
    """elifesciences.org/articles/<id> → 10.7554/eLife.<id>"""
    m = _ELIFE_RE.match(url)
    return f"10.7554/eLife.{m.group('id')}" if m else None


def _try_synth(url: str) -> str | None:
    """Return the first successful DOI synthesis from any registered pattern."""
    # Existing citation_normalize patterns (Nature, Springer, PLOS, T&F, MIT, Frontiers)
    doi = synthesise_doi_from_url(url)
    if doi:
        return doi.lower()

    # Phase 2.5C new patterns
    for fn in (_synth_psyarxiv, _synth_biorxiv_medrxiv, _synth_elife):
        doi = fn(url)
        if doi:
            return doi.strip().lower()

    return None


# ---------------------------------------------------------------------------
# OSF URL parsing
# ---------------------------------------------------------------------------


def _parse_osf_url(url: str) -> str | None:
    """Extract the GUID from an OSF URL; returns None if not an OSF URL."""
    m = _OSF_URL_RE.match(url)
    if not m:
        return None
    rest = m.group("rest").strip("/")
    parts = rest.split("/")
    if not parts or not parts[0]:
        return None
    # osf.io/preprints/<provider>/<guid>
    if parts[0] == "preprints" and len(parts) >= 3:
        return parts[2]
    # osf.io/<guid> or osf.io/<guid>/files/...
    return parts[0]


# ---------------------------------------------------------------------------
# Path A — DOI resolution with optional preprint→journal chase
# ---------------------------------------------------------------------------


def _resolve_path_a(
    doi: str,
    cache_dir: Path,
    today: str,
    warnings: list[str],
    cit_id: str,
    *,
    _depth: int = 0,
) -> dict | None:
    """Resolve a DOI and return metadata, or None on miss.

    For preprint-platform DOIs (10.1101/, 10.31234/, 10.31219/), chases to
    the journal version via Crossref relations then OpenAlex locations.
    One hop only (_depth guard).

    Returns dict with keys:
        family, year, title, metadata_source, notes, _chase_via
    """
    doi = doi.strip().lower()
    if _depth == 0:
        doi = _canonicalise_doi(doi)

    # --- Primary lookup ---
    cr_data = crossref.lookup_by_doi(doi, cache_dir=cache_dir)
    oa_data: dict | None = None

    if cr_data:
        meta = _meta_from_crossref(cr_data)
        source = "crossref"
    else:
        oa_data = openalex.lookup_by_doi(doi, cache_dir=cache_dir)
        if not oa_data:
            return None  # miss on both sources
        meta = _meta_from_openalex(oa_data)
        source = "openalex"

    # One hop only — no relation chase on the journal version.
    if _depth > 0:
        return {
            **meta,
            "metadata_source": source,
            "notes": "",
            "_chase_via": "",
            "_canon_doi": doi,
        }

    # --- Relation chase ---
    journal_doi: str | None = None
    chase_via = ""

    if cr_data:
        journal_doi, chase_via = _extract_relation_doi(cr_data)

    # OpenAlex fallback: only for known preprint-platform DOIs
    is_preprint_doi = doi.startswith(PREPRINT_PREFIXES)
    if journal_doi is None and is_preprint_doi:
        if oa_data is None:  # Haven't called OpenAlex yet (we got Crossref)
            oa_data = openalex.lookup_by_doi(doi, cache_dir=cache_dir)
        if oa_data:
            candidate = _extract_journal_doi_from_openalex(oa_data)
            if candidate:
                journal_doi = candidate
                chase_via = "openalex-locations"

    # Execute chase if we found a candidate journal DOI
    chase_succeeded = False
    if journal_doi:
        j_result = _resolve_path_a(
            journal_doi, cache_dir, today, warnings, cit_id, _depth=1
        )
        if j_result and _sanity_check(meta, j_result):
            chase_succeeded = True
            return {
                **j_result,
                "_chase_via": chase_via,
                "notes": f"preprint-chained to {journal_doi} via {chase_via}",
                "_canon_doi": doi,  # canonical preprint DOI (not the journal DOI)
            }
        elif j_result:
            warnings.append(
                f"{cit_id}: sanity check FAILED chasing {doi} → {journal_doi} "
                f"via {chase_via}; using preprint metadata instead"
            )

    # Preprint-only note (resolver resolved with preprint metadata)
    notes = ""
    if is_preprint_doi and not journal_doi:
        notes = (
            "preprint-only resolved (no chase candidate found in Crossref or OpenAlex)"
        )
    elif is_preprint_doi and journal_doi and not chase_succeeded:
        notes = (
            f"preprint-only resolved "
            f"(chase to {journal_doi} via {chase_via} failed sanity check)"
        )

    return {
        **meta,
        "metadata_source": source,
        "notes": notes,
        "_chase_via": "",
        "_canon_doi": doi,
    }


# ---------------------------------------------------------------------------
# Apply resolved metadata to a registry row
# ---------------------------------------------------------------------------


def _apply_resolution(row: dict, result: dict, today: str) -> bool:
    """Write resolved metadata into a registry row in-place.

    Returns True if pub_id was successfully set (metadata is complete).
    """
    family = result.get("family", "")
    year = result.get("year")
    title = result.get("title", "")

    if not (family and year is not None and title):
        return False

    pub_id = build_pub_id(family, int(year), title)
    row["pub_id"] = pub_id
    row["first_author_family"] = family
    row["year"] = str(year)
    row["title"] = title
    row["metadata_source"] = result.get("metadata_source", "")
    row["verified_on"] = today
    row["status"] = "resolved"

    new_note = (result.get("notes") or "").strip()
    if new_note:
        existing = (row.get("notes") or "").strip()
        row["notes"] = (existing + " | " + new_note) if existing else new_note

    return True


# ---------------------------------------------------------------------------
# Stats classification helper
# ---------------------------------------------------------------------------


def _classify_path_a(stats: dict[str, list], cit_id: str, result: dict) -> None:
    chase_via = result.get("_chase_via", "")
    notes = result.get("notes", "")
    if chase_via == "crossref-is-preprint-of":
        stats["path_a_is_preprint_of"].append(cit_id)
    elif chase_via == "crossref-has-version":
        stats["path_a_has_version"].append(cit_id)
    elif chase_via == "openalex-locations":
        stats["path_a_openalex"].append(cit_id)
    elif "preprint-only" in notes:
        stats["path_a_preprint_only"].append(cit_id)
    else:
        stats["path_a_direct"].append(cit_id)


# ---------------------------------------------------------------------------
# Pass 1 — offline hand-filled rows
# ---------------------------------------------------------------------------


def _process_pass1(
    registry: dict[str, dict],
    today: str,
    stats: dict[str, list],
) -> None:
    """Compute pub_id for rows with (family, year, title) already populated."""
    for cit_id, row in registry.items():
        if (row.get("pub_id") or "").strip():
            continue
        if (row.get("status") or "").strip() in TERMINAL_STATUSES:
            continue
        family = (row.get("first_author_family") or "").strip()
        year_str = (row.get("year") or "").strip()
        title = (row.get("title") or "").strip()
        if not (family and year_str and title):
            continue
        try:
            year = int(year_str)
        except ValueError:
            continue

        pub_id = build_pub_id(family, year, title)
        row["pub_id"] = pub_id
        row["status"] = "resolved"
        row["metadata_source"] = "manual"
        if not (row.get("verified_on") or "").strip():
            row["verified_on"] = today
        stats["pass1_resolved"].append(cit_id)


# ---------------------------------------------------------------------------
# Path D — OSF URL handler
# ---------------------------------------------------------------------------


def _resolve_path_d(
    row: dict,
    url: str,
    cache_dir: Path,
    today: str,
    warnings: list[str],
    stats: dict[str, list],
    cit_id: str,
    *,
    _depth: int = 0,
) -> bool:
    """Process an OSF URL.  Returns True if the row was resolved."""
    if _depth > 1:
        return False

    guid = _parse_osf_url(url)
    if not guid:
        return False

    guid_data = osf.lookup_guid(guid, cache_dir=cache_dir, today=today)
    if not guid_data:
        return False  # 401 private or 404

    referent = (
        guid_data.get("data", {})
        .get("relationships", {})
        .get("referent", {})
        .get("data", {})
    )
    obj_type = referent.get("type", "")
    obj_id = referent.get("id", "")

    if not obj_type or not obj_id:
        return False

    typed_data = osf.lookup_typed(obj_type, obj_id, cache_dir=cache_dir, today=today)

    if obj_type == "files":
        if _depth == 0:
            parent_id = (
                typed_data.get("data", {})
                .get("relationships", {})
                .get("node", {})
                .get("data", {})
                .get("id", "")
            )
            if parent_id:
                return _resolve_path_d(
                    row,
                    f"https://osf.io/{parent_id}",
                    cache_dir,
                    today,
                    warnings,
                    stats,
                    cit_id,
                    _depth=1,
                )
        return False

    if obj_type == "preprints":
        meta = osf.extract_publication_metadata(typed_data)
        pub_doi = meta.get("publication_doi") or ""
        if pub_doi and not osf.is_osf_project_doi(pub_doi):
            result = _resolve_path_a(pub_doi, cache_dir, today, warnings, cit_id)
            if result and _apply_resolution(row, result, today):
                row["doi"] = pub_doi
                stats["path_d_preprint"].append(cit_id)
                return True
        return False

    if obj_type in ("nodes", "registrations"):
        # Cache is already written by lookup_typed; 2.5D will consume it.
        # Per thinking doc §2.2: do NOT auto-promote nodes/registrations.
        stats["path_d_node"].append(cit_id)
        return False  # Not resolved

    return False


# ---------------------------------------------------------------------------
# Pass 2 — network resolution
# ---------------------------------------------------------------------------


def _process_pass2(
    registry: dict[str, dict],
    today: str,
    paths_enabled: set[str],
    cache_dir: Path,
    limit: int | None,
    warnings: list[str],
    stats: dict[str, list],
) -> None:
    """Process eligible rows in Pass 2 via network paths A–D."""
    count = 0

    for cit_id, row in registry.items():
        # Skip already-resolved (idempotency)
        if (row.get("pub_id") or "").strip():
            stats["skipped_already"].append(cit_id)
            continue
        # Skip terminal statuses
        status = (row.get("status") or "").strip()
        if status in TERMINAL_STATUSES:
            stats["skipped_terminal"].append(cit_id)
            continue
        # Skip rows already resolved in Pass 1 (status=resolved, pub_id set by now)
        # The pub_id check above covers this.

        doi = (row.get("doi") or "").strip()
        url = (row.get("url") or "").strip()

        if not doi and not url:
            continue

        if limit is not None and count >= limit:
            break
        count += 1

        resolved = False

        # --- Path A: DOI lookup ---
        if not resolved and "A" in paths_enabled and doi:
            result = _resolve_path_a(doi, cache_dir, today, warnings, cit_id)
            if result and _apply_resolution(row, result, today):
                canon_doi = (result.get("_canon_doi") or "").strip()
                if canon_doi and canon_doi != doi:
                    row["doi"] = canon_doi
                _classify_path_a(stats, cit_id, result)
                resolved = True

        # --- Path B: URL → synthesised DOI → Path A ---
        if not resolved and "B" in paths_enabled and not doi and url:
            synth_doi = _try_synth(url)
            if synth_doi:
                result = _resolve_path_a(synth_doi, cache_dir, today, warnings, cit_id)
                if result and _apply_resolution(row, result, today):
                    row["doi"] = synth_doi
                    stats["path_b"].append(cit_id)
                    resolved = True

        # --- Path C: PMID URL → Europe PMC ---
        if not resolved and "C" in paths_enabled and url:
            m = _PMID_URL_RE.match(url)
            if m:
                pmid = m.group("pmid")
                epmc_data = europepmc.lookup_by_pmid(pmid, cache_dir=cache_dir)
                if epmc_data:
                    epmc_doi = (epmc_data.get("doi") or "").strip().lower()
                    if epmc_doi:
                        result = _resolve_path_a(
                            epmc_doi, cache_dir, today, warnings, cit_id
                        )
                        if result and _apply_resolution(row, result, today):
                            row["doi"] = epmc_doi
                            stats["path_c"].append(cit_id)
                            resolved = True
                    elif not resolved:
                        # No DOI but have bib metadata from Europe PMC
                        epmc_meta = _meta_from_europepmc(epmc_data)
                        fake_result = {
                            **epmc_meta,
                            "metadata_source": "europepmc",
                            "notes": "",
                            "_chase_via": "",
                        }
                        if _apply_resolution(row, fake_result, today):
                            stats["path_c"].append(cit_id)
                            resolved = True

        # --- Path D: OSF URL ---
        if not resolved and "D" in paths_enabled and url:
            if _OSF_URL_RE.match(url):
                resolved = _resolve_path_d(
                    row, url, cache_dir, today, warnings, stats, cit_id
                )

        if not resolved:
            # Not counting path_d_node rows as "pending" here; they are in their
            # own bucket already — but "still_pending" captures truly-unresolved.
            if cit_id not in stats["path_d_node"]:
                stats["still_pending"].append(cit_id)


# ---------------------------------------------------------------------------
# Registry I/O (atomic write mirroring apply_manual_fills.py)
# ---------------------------------------------------------------------------


def load_registry(path: Path) -> tuple[dict[str, dict], list[str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        columns = list(reader.fieldnames or [])
        rows: dict[str, dict] = {}
        for row in reader:
            rows[row["citation_id"]] = dict(row)
    return rows, columns


def write_registry(path: Path, rows: dict[str, dict], columns: list[str]) -> None:
    """Atomic: write to tmp file, fsync, then rename onto destination."""
    tmp_path = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    try:
        with tmp_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=columns,
                delimiter="\t",
                extrasaction="ignore",
                lineterminator="\n",
            )
            writer.writeheader()
            for row in rows.values():
                writer.writerow(row)
            fh.flush()
            os.fsync(fh.fileno())
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------


def _host_of(url: str) -> str:
    m = re.match(r"^https?://([^/]+)", url)
    return m.group(1) if m else url[:40]


def format_report(
    stats: dict[str, list],
    warnings: list[str],
    today: str,
    registry_path: Path,
    cache_dir: Path,
    paths_enabled: set[str],
    write_back: bool,
    registry: dict[str, dict],
    watched_ids: list[str],
) -> str:
    total_resolved = (
        len(stats["pass1_resolved"])
        + len(stats["path_a_direct"])
        + len(stats["path_a_is_preprint_of"])
        + len(stats["path_a_has_version"])
        + len(stats["path_a_openalex"])
        + len(stats["path_a_preprint_only"])
        + len(stats["path_b"])
        + len(stats["path_c"])
        + len(stats["path_d_preprint"])
    )

    lines = [
        f"# enrich_pub_ids run {today}",
        "",
        f"- Registry: `{registry_path}`",
        f"- Cache dir: `{cache_dir}`",
        f"- Paths enabled: {', '.join(sorted(paths_enabled))}",
        f"- Mode: {'WRITE-BACK' if write_back else 'DRY-RUN'}",
        "",
        "## Summary",
        "",
        "| Category | Count |",
        "|---|---|",
        f"| **Total resolved this run** | **{total_resolved}** |",
        f"| Pass 1 (offline, manual metadata) | {len(stats['pass1_resolved'])} |",
        f"| Path A direct (DOI → Crossref/OpenAlex) | {len(stats['path_a_direct'])} |",
        f"| Path A is-preprint-of chained | {len(stats['path_a_is_preprint_of'])} |",
        f"| Path A has-version chained | {len(stats['path_a_has_version'])} |",
        f"| Path A OpenAlex-locations chained | {len(stats['path_a_openalex'])} |",
        f"| Path A preprint-only (no journal found) | {len(stats['path_a_preprint_only'])} |",
        f"| Path B (URL→synth DOI) | {len(stats['path_b'])} |",
        f"| Path C (PMID URL → Europe PMC) | {len(stats['path_c'])} |",
        f"| Path D preprint (OSF preprint) | {len(stats['path_d_preprint'])} |",
        "| --- | --- |",
        f"| OSF nodes cached for 2.5D (not resolved) | {len(stats['path_d_node'])} |",
        f"| Still pending (no resolution attempt succeeded) | {len(stats['still_pending'])} |",
        f"| Skipped (already resolved / pub_id set) | {len(stats['skipped_already'])} |",
        f"| Skipped (terminal status) | {len(stats['skipped_terminal'])} |",
        f"| Warnings | {len(warnings)} |",
        "",
    ]

    # Pending rows by host
    if stats["still_pending"]:
        lines.append("## Pending rows — host distribution")
        lines.append("")
        host_counts: dict[str, int] = {}
        for cit_id in stats["still_pending"]:
            row = registry.get(cit_id, {})
            url = (row.get("url") or "").strip()
            if url:
                host = _host_of(url)
                host_counts[host] = host_counts.get(host, 0) + 1
        for host, cnt in sorted(host_counts.items(), key=lambda x: -x[1]):
            lines.append(f"- `{host}`: {cnt}")
        lines.append("")

    # Watched cit_ids
    if watched_ids:
        lines.append("## Watched cit_ids (2.5B auto-stagers)")
        lines.append("")
        lines.append("| cit_id | status | pub_id | notes |")
        lines.append("|---|---|---|---|")
        for cit_id in watched_ids:
            row = registry.get(cit_id, {})
            lines.append(
                f"| {cit_id} | {row.get('status', '')} "
                f"| {row.get('pub_id', '')} "
                f"| {(row.get('notes') or '')[:60]} |"
            )
        lines.append("")

    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Library API
# ---------------------------------------------------------------------------

WATCHED_IDS = [
    "cit_000992",
    "cit_001056",
    "cit_001110",
    "cit_001238",
    "cit_001239",
    "cit_001643",
    "cit_001646",
]

_RESOLVED_STAT_KEYS = (
    "pass1_resolved",
    "path_a_direct",
    "path_a_is_preprint_of",
    "path_a_has_version",
    "path_a_openalex",
    "path_a_preprint_only",
    "path_b",
    "path_c",
    "path_d_preprint",
)

_ALL_STAT_KEYS = _RESOLVED_STAT_KEYS + (
    "path_d_node",
    "skipped_already",
    "skipped_terminal",
    "still_pending",
)


@dataclass
class EnrichmentResult:
    """Structured outcome of :func:`enrich_registry`.

    ``stats`` keys (lists of cit_ids):
        ``pass1_resolved`` — resolved offline from manual metadata.
        ``path_a_direct``  — DOI looked up directly via Crossref/OpenAlex.
        ``path_a_is_preprint_of`` — DOI chased through is-preprint-of.
        ``path_a_has_version``    — DOI chased through has-version.
        ``path_a_openalex``       — DOI chased through OpenAlex locations.
        ``path_a_preprint_only``  — preprint DOI, no journal counterpart.
        ``path_b``                — URL synthesised into a DOI then Path A.
        ``path_c``                — PubMed URL → Europe PMC → DOI.
        ``path_d_preprint``       — OSF preprint resolved.
        ``path_d_node``           — OSF node/registration cached for review.
        ``skipped_already``       — row had pub_id already.
        ``skipped_terminal``      — row in a terminal status.
        ``still_pending``         — no resolution attempt succeeded.
    """

    today: str
    registry_path: Path
    cache_dir: Path
    paths_enabled: set[str]
    write_back: bool
    stats: dict[str, list[str]]
    warnings: list[str]
    registry: dict[str, dict]
    report_text: str
    report_path: Path | None = None

    @property
    def total_resolved(self) -> int:
        return sum(len(self.stats[k]) for k in _RESOLVED_STAT_KEYS)

    @property
    def pass1_count(self) -> int:
        return len(self.stats["pass1_resolved"])

    @property
    def pass2_count(self) -> int:
        return self.total_resolved - self.pass1_count


def enrich_registry(
    *,
    registry_path: Path,
    cache_dir: Path,
    write_back: bool = False,
    paths_enabled: Iterable[str] = ("A", "B", "C", "D"),
    limit: int | None = None,
    report_path: Path | None = None,
    watched_ids: list[str] | None = None,
    today: str | None = None,
) -> EnrichmentResult:
    """Enrich a citation registry with ``pub_id`` values.

    Runs two passes over ``registry_path``:

      Pass 1 (offline)
          Rows that already carry ``first_author_family + year + title``
          get a deterministic ``pub_id`` computed locally via
          :func:`citation_identity.build_pub_id`.  No network.

      Pass 2 (network)
          Rows still unresolved are walked through paths A–D
          (Crossref, OpenAlex, Europe PMC, OSF) per
          ``paths_enabled``.  Cached responses live under
          ``cache_dir``.

    This is the **library entry point**; callers from consumer repos
    (or test fixtures) construct paths themselves and pass them in.
    For a command-line driver see :func:`main`.

    Parameters
    ----------
    registry_path
        Path to ``citation_registry.tsv``.  Read on entry; written
        back when ``write_back=True``.
    cache_dir
        Cache root for API responses.  Caller resolves the
        ``--cache-dir`` / ``$HED_CACHE_DIR`` / fallback precedence
        before calling.
    write_back
        Persist the modified registry to ``registry_path``.  Default
        is dry-run.
    paths_enabled
        Subset of ``("A", "B", "C", "D")``.  Case-insensitive.
    limit
        Cap on the number of Pass-2 rows processed.  ``None`` = no
        cap.
    report_path
        Optional path to write the Markdown run report to.  ``None``
        skips the file write but the rendered text still appears on
        the returned :class:`EnrichmentResult`.
    watched_ids
        Optional list of cit_ids to feature in the report's "watched"
        section.  ``None`` omits the section.
    today
        ISO-8601 date stamped on resolved rows and the report.
        ``None`` → today's UTC date.  Override for deterministic
        tests.

    Returns
    -------
    EnrichmentResult
        Carries ``stats``, ``warnings``, the final in-memory
        registry, and the rendered report text.
    """
    if today is None:
        today = today_iso()

    paths_set = {p.strip().upper() for p in paths_enabled}
    watched = list(watched_ids) if watched_ids else []

    registry, columns = load_registry(registry_path)

    stats: dict[str, list[str]] = {k: [] for k in _ALL_STAT_KEYS}
    warnings_out: list[str] = []

    _process_pass1(registry, today, stats)
    _process_pass2(
        registry,
        today,
        paths_set,
        cache_dir,
        limit,
        warnings_out,
        stats,
    )

    report_text = format_report(
        stats,
        warnings_out,
        today,
        registry_path,
        cache_dir,
        paths_set,
        write_back,
        registry,
        watched,
    )

    if write_back:
        write_registry(registry_path, registry, columns)

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_text, encoding="utf-8")

    return EnrichmentResult(
        today=today,
        registry_path=registry_path,
        cache_dir=cache_dir,
        paths_enabled=paths_set,
        write_back=write_back,
        stats=stats,
        warnings=warnings_out,
        registry=registry,
        report_text=report_text,
        report_path=report_path,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_cli_cache_dir(arg_value: str | None) -> Path:
    """``--cache-dir`` > ``$HED_CACHE_DIR`` > module default.

    Lifted out of ``main()`` so the precedence is testable.
    """
    if arg_value:
        return Path(arg_value)
    env_val = os.environ.get("HED_CACHE_DIR")
    if env_val:
        return Path(env_val)
    return _DEFAULT_CACHE_DIR


def main(argv: list[str] | None = None) -> int:
    """Argparse wrapper around :func:`enrich_registry`.

    Reads command-line flags, resolves their values into a call to
    :func:`enrich_registry`, then prints a one-screen summary of the
    returned result.  Exits ``0`` on success.

    Consumer drivers that want different argument shapes (a TOML
    config, a Python dict, etc.) should NOT call ``main`` — call
    :func:`enrich_registry` directly with explicit arguments.
    """
    parser = argparse.ArgumentParser(
        description="Enrich citation registry rows with pub_ids."
    )
    parser.add_argument(
        "--registry",
        default=str(_REGISTRY_DEFAULT),
        help="Path to citation_registry.tsv",
    )
    parser.add_argument(
        "--write-back",
        action="store_true",
        help="Write changes to the registry (default: dry-run)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicit dry-run (takes precedence over --write-back if both given)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of rows processed in Pass 2 (for smoke testing)",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help=(
            "Cache directory root.  Checked in order: this arg, $HED_CACHE_DIR, "
            "outputs/cache/ (repo-relative default)."
        ),
    )
    parser.add_argument(
        "--paths",
        default="A,B,C,D",
        help="Comma-separated list of paths to run (default: A,B,C,D)",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Path for the Markdown run report",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    write_back = args.write_back and not args.dry_run
    registry_path = Path(args.registry)
    cache_dir = _resolve_cli_cache_dir(args.cache_dir)
    paths_enabled = [p.strip().upper() for p in args.paths.split(",")]
    today = today_iso()
    report_path = (
        Path(args.report)
        if args.report
        else _ROOT / ".status" / f"enrich_pub_ids_run_{today}.md"
    )

    result = enrich_registry(
        registry_path=registry_path,
        cache_dir=cache_dir,
        write_back=write_back,
        paths_enabled=paths_enabled,
        limit=args.limit,
        report_path=report_path,
        watched_ids=WATCHED_IDS,
        today=today,
    )

    # CLI summary
    print(f"Pass 1 complete: {result.pass1_count} rows resolved offline.")
    print(f"Pass 2 complete: {result.pass2_count} rows resolved via network paths.")
    print(f"Total resolved this run: {result.total_resolved}")
    print(f"OSF nodes cached for review (2.5D): {len(result.stats['path_d_node'])}")
    print(f"Still pending: {len(result.stats['still_pending'])}")

    if write_back:
        print(f"\nRegistry written: {registry_path}")
    else:
        print("\nDry-run.  Pass --write-back to commit changes.")
    print(f"Report: {report_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
