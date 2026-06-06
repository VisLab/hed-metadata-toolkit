"""apply_manual_fills.py — Bank a curator JSON into the citation registry.

Reads a JSON file of manually curated citation decisions and applies them to
citation_registry.tsv.  Dry-run by default; pass --write-back to commit.

Usage:
    python src/apply_manual_fills.py [--input PATH] [--registry PATH]
                                     [--write-back] [--report PATH]

No network calls.  Safe to re-run: idempotent.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

_URL_RE = re.compile(r"^(https?://|doi:)", re.IGNORECASE)

TERMINAL_STATUSES = {"rejected", "not_a_citation"}

# JSON null-DOI statuses that map to registry "rejected"
NULL_DOI_STATUSES = {
    "rejected", "reject", "supplement", "unresolved",
    "no_paper", "dataset", "preprint_only", "conference_proceeding",
}


def is_url_shaped(s: str) -> bool:
    """True iff s looks like a URL or doi: reference."""
    return bool(_URL_RE.match(s))


def today_iso() -> str:
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------------------
# Registry I/O
# ---------------------------------------------------------------------------

def load_registry(path: Path) -> tuple[dict[str, dict], list[str]]:
    """Load TSV into an ordered dict keyed by citation_id.

    Returns (registry_dict, column_order).
    """
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        columns = list(reader.fieldnames or [])
        rows: dict[str, dict] = {}
        for row in reader:
            rows[row["citation_id"]] = dict(row)
    return rows, columns


def write_registry(path: Path, rows: dict[str, dict], columns: list[str]) -> None:
    """Write registry TSV preserving column order.

    Atomic: serialises to a per-PID sibling tmp file, fsyncs it, then
    atomically renames onto the destination.  This guarantees that a
    partial write (interrupted process, transient I/O failure, file
    handle held by another process) cannot leave the on-disk registry
    truncated.  See `.status/session_2026-05-06_phase2_5b_fix.md` for
    the regression that motivated this; mirrors the pattern in
    `src/cache.py`.
    """
    tmp_path = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    try:
        with tmp_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=columns, delimiter="\t",
                extrasaction="ignore", lineterminator="\n",
            )
            writer.writeheader()
            for row in rows.values():
                writer.writerow(row)
            # Force buffered data to disk before the rename so a crash
            # between rename and disk-flush cannot lose the fresh content.
            fh.flush()
            os.fsync(fh.fileno())
        # POSIX rename(2) and Windows MoveFileExW (called by Path.replace)
        # are atomic on the same filesystem; the tmp file is created next
        # to the destination so the rename never crosses a boundary.
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Core algorithm
# ---------------------------------------------------------------------------

def _apply_manual_fill(
    cit: str,
    manual_fill: dict,
    r: dict,
    today: str,
    warnings_out: list[str],
    stats: dict[str, list[str]],
) -> None:
    """Apply a manual_fill sub-object to a registry row in-place.

    Three accepted shapes (exactly one of doi/family/rejected must be present):
      {"doi": "10.xxxx/yyyy"}               → set row.doi, row.status="manual"
      {"family": "...", "year": int,
       "title": "..."}                       → set metadata fields for Pass-1 resolver
      {"rejected": "reason text"}            → set row.status="rejected"

    Multiple keys raise a validation warning; only the highest-priority key
    (doi > family > rejected) is applied.
    """
    has_doi = bool((manual_fill.get("doi") or "").strip())
    has_family = "family" in manual_fill
    has_rejected = "rejected" in manual_fill

    key_count = sum([has_doi, has_family, has_rejected])
    if key_count > 1:
        warnings_out.append(
            f"{cit}: manual_fill has multiple shape keys {sorted(manual_fill.keys())!r}; "
            f"applying in priority order doi > family > rejected"
        )

    if not key_count:
        warnings_out.append(
            f"{cit}: manual_fill present but has no recognised keys "
            f"(expected doi, family, or rejected); skipping"
        )
        return

    if has_doi:
        json_doi = manual_fill["doi"].strip()
        reg_doi = r.get("doi", "").strip()

        # Idempotency: already applied in a prior run.
        if reg_doi == json_doi and r.get("status", "").strip() == "manual":
            stats["skipped_already_resolved"].append(cit)
            return

        if reg_doi and reg_doi != json_doi:
            warnings_out.append(
                f"{cit}: registry doi={reg_doi!r}, manual_fill doi={json_doi!r}; "
                f"preferring manual_fill (curator decision)"
            )

        r["doi"] = json_doi
        r["status"] = "manual"
        existing_notes = (r.get("notes") or "").strip()
        mf_note = "manual_fill: doi"
        r["notes"] = (existing_notes + " | " + mf_note) if existing_notes else mf_note
        r["verified_on"] = today
        stats["applied_doi"].append(cit)

    elif has_family:
        family = str(manual_fill.get("family") or "").strip()
        year_raw = manual_fill.get("year")
        title = str(manual_fill.get("title") or "").strip()

        if not family or year_raw is None or not title:
            warnings_out.append(
                f"{cit}: manual_fill family/year/title shape is incomplete "
                f"(family={family!r}, year={year_raw!r}, title={title!r}); skipping"
            )
            return

        try:
            year_str = str(int(year_raw))
        except (TypeError, ValueError):
            warnings_out.append(f"{cit}: manual_fill year={year_raw!r} is not an integer; skipping")
            return

        # Idempotency: already applied in a prior run.
        if (r.get("first_author_family", "").strip() == family
                and r.get("year", "").strip() == year_str
                and r.get("title", "").strip() == title):
            stats["skipped_already_resolved"].append(cit)
            return

        r["first_author_family"] = family
        r["year"] = year_str
        r["title"] = title
        r["verified_on"] = today
        stats["applied_manual_fill_meta"].append(cit)

    else:  # has_rejected
        reason = str(manual_fill.get("rejected") or "").strip()
        r["status"] = "rejected"
        r["notes"] = f"manual-reject: {reason}"
        r["verified_on"] = today
        stats["applied_rejected"].append(cit)


def apply_fills(
    json_entries: list[dict],
    registry: dict[str, dict],
    today: str,
    warnings_out: list[str],
) -> dict[str, list[str]]:
    """Apply curator JSON entries to the registry dict in-place.

    Supports both the legacy top-level-field schema
    (resolved_references_050526.json) and the new manual_fill sub-object
    schema (manual_review_<date>.json).  When manual_fill is present and
    non-null it takes precedence; otherwise the legacy path is used.

    Returns a stats dict with lists of citation_ids per outcome category.
    No I/O — all data passed in; caller handles reading and writing.
    """
    stats: dict[str, list[str]] = {
        "applied_doi": [],
        "applied_rejected": [],
        "applied_manual_fill_meta": [],
        "skipped_already_resolved": [],
        "skipped_not_in_registry": [],
        "deferred_no_intent": [],
    }

    for entry in json_entries:
        cit = (entry.get("citation_id") or "").strip()

        if cit not in registry:
            warnings_out.append(f"{cit}: not in registry, skipping")
            stats["skipped_not_in_registry"].append(cit)
            continue

        r = registry[cit]

        # Already resolved by a prior resolver run — never overwrite.
        if r.get("pub_id", "").strip():
            entry_doi = (entry.get("doi") or "").strip()
            reg_doi = r.get("doi", "").strip()
            if entry_doi and reg_doi and entry_doi != reg_doi:
                warnings_out.append(
                    f"{cit}: already resolved (pub_id={r['pub_id'].strip()!r}), "
                    f"skipping; json doi={entry_doi!r} differs from registry doi={reg_doi!r}"
                )
            stats["skipped_already_resolved"].append(cit)
            continue

        # Terminal status — skip (silently for null-doi re-runs; warn if JSON has a doi).
        if r.get("status", "").strip() in TERMINAL_STATUSES:
            entry_doi_check = (entry.get("doi") or "").strip()
            if entry_doi_check:
                warnings_out.append(
                    f"{cit}: already terminal status={r['status'].strip()!r}, "
                    f"skipping; json has doi={entry_doi_check!r}"
                )
            stats["skipped_already_resolved"].append(cit)
            continue

        # --- New schema: manual_fill sub-object takes precedence ---
        manual_fill = entry.get("manual_fill")
        if manual_fill is not None:
            _apply_manual_fill(cit, manual_fill, r, today, warnings_out, stats)
            continue

        # --- Legacy schema: use top-level doi / status / notes / resolved_url ---
        json_doi = (entry.get("doi") or "").strip()
        json_status = (entry.get("status") or "").strip().lower()
        json_notes = (entry.get("notes") or "").strip()
        json_resolved_url = (entry.get("resolved_url") or "").strip()

        if json_doi:
            # Idempotency guard: if the DOI is already set and status is already
            # "manual", this entry was applied in a previous run — skip cleanly.
            reg_doi = r.get("doi", "").strip()
            if reg_doi == json_doi and r.get("status", "").strip() == "manual":
                stats["skipped_already_resolved"].append(cit)
                continue

            if reg_doi and reg_doi != json_doi:
                warnings_out.append(
                    f"{cit}: registry has doi={reg_doi!r}, json has doi={json_doi!r}; "
                    f"preferring json (manually curated)"
                )

            r["doi"] = json_doi
            r["status"] = "manual"

            note_parts = [f"manual: {json_status}"]
            if json_notes:
                note_parts.append(json_notes)

            if json_resolved_url:
                if not is_url_shaped(json_resolved_url):
                    warnings_out.append(
                        f"{cit}: malformed resolved_url (not a URL): "
                        f"{json_resolved_url[:80]!r}; moved to notes"
                    )
                    note_parts.append(f"[malformed resolved_url] {json_resolved_url}")
                else:
                    note_parts.append(f"resolved_url: {json_resolved_url}")

            r["notes"] = " | ".join(note_parts)
            r["verified_on"] = today
            stats["applied_doi"].append(cit)

        else:
            # No DOI.  Decide between rejection (curator-vetted) and
            # deferral (auto-staged placeholder) based on whether the
            # curator left any intent in `notes` or `resolved_url`.
            #
            # Rationale (audit 2026-05-06): the JSON contained 7 rows
            # with status='preprint_only' and ALL other fields empty
            # (notes, resolved_url, doi).  These were auto-staged by
            # an earlier classification step, not curator decisions.
            # All 7 turned out to be PsyArXiv/bioRxiv preprints with
            # journal-published versions Crossref knows about.  Auto-
            # rejecting them was wrong; deferring lets the resolver's
            # Path B + relation-chase recover them.  See
            # .status/session_2026-05-06_phase2_5b_truncation_fix.md
            # for the audit details.
            has_intent = bool(json_notes) or bool(json_resolved_url)
            if not has_intent:
                warnings_out.append(
                    f"{cit}: status={json_status!r} with no curator notes "
                    f"and no resolved_url — treating as auto-staged; "
                    f"deferring to resolver instead of rejecting"
                )
                stats["deferred_no_intent"].append(cit)
                continue

            if json_status not in NULL_DOI_STATUSES:
                warnings_out.append(
                    f"{cit}: unknown null-doi status={json_status!r}; rejecting anyway"
                )

            r["status"] = "rejected"
            note_parts = [f"manual-reject: {json_status}"]
            if json_notes:
                note_parts.append(json_notes)
            r["notes"] = " | ".join(note_parts)
            r["verified_on"] = today
            stats["applied_rejected"].append(cit)

    return stats


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def format_report(
    stats: dict[str, list[str]],
    warnings_out: list[str],
    today: str,
    input_path: Path,
    registry_path: Path,
    write_back: bool,
) -> str:
    lines = [
        f"# apply_manual_fills run {today}",
        "",
        f"- Input: `{input_path}`",
        f"- Registry: `{registry_path}`",
        f"- Mode: {'WRITE-BACK' if write_back else 'DRY-RUN'}",
        "",
        "## Summary",
        "",
        "| Category | Count |",
        "|---|---|",
        f"| Applied (DOI set, status=manual) | {len(stats['applied_doi'])} |",
        f"| Applied (rejected) | {len(stats['applied_rejected'])} |",
        f"| Applied (manual_fill: family/year/title set) | {len(stats.get('applied_manual_fill_meta', []))} |",
        f"| Deferred (status-only, no curator intent) | {len(stats['deferred_no_intent'])} |",
        f"| Skipped (already resolved) | {len(stats['skipped_already_resolved'])} |",
        f"| Skipped (cit_id not in registry) | {len(stats['skipped_not_in_registry'])} |",
        f"| Warnings | {len(warnings_out)} |",
        "",
    ]

    def _section(title: str, items: list[str]) -> None:
        if not items:
            return
        lines.append(f"## {title}")
        lines.append("")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")

    _section("Applied (DOI set, status=manual)", stats["applied_doi"])
    _section("Applied (rejected)", stats["applied_rejected"])
    _section("Applied (manual_fill: family/year/title set for resolver Pass 1)",
             stats.get("applied_manual_fill_meta", []))
    _section("Deferred (status-only, no curator intent → resolver should attempt)",
             stats["deferred_no_intent"])
    _section("Skipped (already resolved)", stats["skipped_already_resolved"])
    _section("Skipped (cit_id not in registry)", stats["skipped_not_in_registry"])
    _section("Warnings", warnings_out)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Apply curator JSON decisions to the citation registry."
    )
    parser.add_argument(
        "--input",
        default=str(REPO_ROOT / "datasets" / "dataset_summaries" / "resolved_references_050526.json"),
        help="Path to the curator JSON file (default: resolved_references_050526.json)",
    )
    parser.add_argument(
        "--registry",
        default=str(REPO_ROOT / "datasets" / "dataset_summaries" / "citation_registry.tsv"),
        help="Path to citation_registry.tsv",
    )
    parser.add_argument(
        "--write-back",
        action="store_true",
        help="Write changes to the registry (default: dry-run)",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Path for the Markdown run report (default: .status/apply_manual_fills_run_<date>.md)",
    )
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    registry_path = Path(args.registry)
    today = today_iso()

    report_path = (
        Path(args.report)
        if args.report
        else REPO_ROOT / ".status" / f"apply_manual_fills_run_{today}.md"
    )

    with input_path.open(encoding="utf-8") as fh:
        json_entries = json.load(fh)

    registry, columns = load_registry(registry_path)

    warnings_out: list[str] = []
    stats = apply_fills(json_entries, registry, today, warnings_out)

    report_text = format_report(
        stats, warnings_out, today, input_path, registry_path, args.write_back
    )

    print(report_text)

    if args.write_back:
        write_registry(registry_path, registry, columns)
        print(f"\nRegistry written: {registry_path}")
    else:
        print("\nDry-run.  Pass --write-back to commit changes.")

    report_path.write_text(report_text, encoding="utf-8")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
