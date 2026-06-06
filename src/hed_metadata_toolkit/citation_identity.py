"""
identity.py — Deterministic pub_id, canonical_string, and PDF filename generation.

Every other module in the literature-search workstream imports from here.
These are pure functions: same inputs always produce the same outputs.
No network calls, no I/O, no global state.

Algorithm source: literature_search_plan_2026-04-21.md §11.7 (authoritative)
and pdf_naming_thinking_2026-04-21.md §5.

  build_canonical_string(first_author_family, year, title) -> str
      Returns the ≤100-char lowercase alphanumeric string that is the SHA-1
      input for both pub_id and the PDF filename's trailing hash.

  build_pub_id(first_author_family, year, title) -> str
      Returns 'pub_' + sha1(canonical_string)[:8].

  build_pdf_filename(first_author_family, year, title) -> str
      Returns '<LastName>_<Year>_<CamelCaseTitle>_<hash8>.pdf'.

TODO (Phase 6): when writing a new pub_id into publications.json, check
for 8-char hash collisions and bump to 10 chars for any colliding pair.
"""

import hashlib
import re
import unicodedata


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _ascii_fold(s: str) -> str:
    """NFKD-normalize s and strip combining characters, yielding ASCII-safe text.

    Examples:
        'Schonberg' from 'Schonberg' (ö -> o + combining diaeresis, stripped)
        "O'Keefe"  -> "O'Keefe"   (apostrophe is not a combining mark)
        '工作记忆'  -> '工作记忆'  (CJK does not decompose into ASCII)
    """
    normalized = unicodedata.normalize("NFKD", s)
    return "".join(c for c in normalized if unicodedata.combining(c) == 0)


def _canonical_lastname(family: str | None) -> str:
    """Produce the last-name portion of the canonical string.

    Rules (§11.7 step 4.1):
    - ASCII-fold, lowercase, remove every char not in [a-z].
    - Empty result -> 'anonymous'.
    """
    if not family:
        return "anonymous"
    folded = _ascii_fold(family).lower()
    result = re.sub(r"[^a-z]", "", folded)
    return result if result else "anonymous"


def _canonical_title(title: str | None) -> str:
    """Produce the title portion of the canonical string.

    Rules (§11.7 step 4.3):
    - ASCII-fold, lowercase, remove every char not in [a-z0-9].
    - Empty result -> 'untitled'.
    """
    if not title:
        return "untitled"
    folded = _ascii_fold(title).lower()
    result = re.sub(r"[^a-z0-9]", "", folded)
    return result if result else "untitled"


def _canonical_year(year: int | None) -> str:
    """Produce the year portion of the canonical string (§11.7 step 4.2)."""
    if year is None:
        return "0000"
    return str(year).zfill(4)


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def build_canonical_string(
    first_author_family: str | None,
    year: int | None,
    title: str | None,
) -> str:
    """Return the <=100-char ASCII alphanumeric string used as SHA-1 input.

    Algorithm (§11.7 step 4):
        s = last + yr + title_part   (concatenated, no separators)
        s = s[:100]                  (may cut mid-word -- it is opaque)

    The result is stored as 'canonical_string' in the publication record
    so the hash is auditable: sha1(canonical_string)[:8] == pub_id[-8:].
    """
    last = _canonical_lastname(first_author_family)
    yr = _canonical_year(year)
    title_part = _canonical_title(title)
    s = last + yr + title_part
    return s[:100]


def build_pub_id(
    first_author_family: str | None,
    year: int | None,
    title: str | None,
) -> str:
    """Return 'pub_' + sha1(canonical_string)[:8].

    The 8-char hex suffix is identical to the <hash8> token in the PDF
    filename, so cross-referencing a PDF to its catalog record is a single
    substring lookup in publications.json.
    """
    canonical = build_canonical_string(first_author_family, year, title)
    hash8 = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:8]
    return f"pub_{hash8}"


# ---------------------------------------------------------------------------
# PDF filename helpers
# ---------------------------------------------------------------------------

def _filename_lastname(family: str | None) -> str:
    """Produce the <LastName> token for the PDF filename (§11.7 step 1).

    Rules:
    - ASCII-fold (diacritics -> ASCII).
    - Split on whitespace; capitalize first char of each token, preserve rest.
    - Remove every char not in [A-Za-z0-9] from each token.
    - Concatenate.
    - Empty result -> 'Anonymous'.

    Examples (from §11.7):
        "O'Keefe"    -> capitalize "O", keep "'Keefe", strip apostrophe -> "OKeefe"
        "van der Berg" -> ["van","der","Berg"] -> ["Van","Der","Berg"] -> "VanDerBerg"
        "de Fockert"  -> ["de","Fockert"] -> ["De","Fockert"] -> "DeFockert"
        "Schonberg"  (after fold from "Schonberg") -> "Schonberg"
    """
    if not family:
        return "Anonymous"
    folded = _ascii_fold(family)
    tokens = folded.split()
    parts = []
    for tok in tokens:
        # Capitalize first char; preserve the rest of the original casing.
        # Removing non-alphanumeric AFTER capitalizing gives:
        #   "O'Keefe" -> "O" + "'Keefe" -> strip apostrophe -> "OKeefe"  (K stays)
        #   "van"     -> "Van" -> "Van"
        titled = tok[0].upper() + tok[1:] if tok else ""
        cleaned = re.sub(r"[^A-Za-z0-9]", "", titled)
        if cleaned:
            parts.append(cleaned)
    return "".join(parts) if parts else "Anonymous"


def _filename_year(year: int | None) -> str:
    """Produce the <Year> token for the PDF filename (§11.7 step 2)."""
    if year is None:
        return "nodate"
    return str(year)


def _is_acronym_token(token: str) -> bool:
    """Return True if token should be preserved as-is (not TitleCased).

    Rule (§11.7 step 3):
    - All-uppercase AND length >= 2:  EEG, ADHD, IAPS, TMS  -> True
    - Starts lowercase, rest uppercase, AND length >= 2:  fMRI  -> True
    - Everything else -> False (apply TitleCase).

    The second condition handles the neuroimaging convention where the
    method prefix is lowercase (f = functional) followed by all-caps (MRI).
    """
    if len(token) < 2:
        return False
    if token.upper() == token:                          # all-uppercase: EEG, ADHD
        return True
    if token[0].islower() and token[1:] == token[1:].upper():  # fMRI pattern
        return True
    return False


def _filename_camel_title(title: str | None) -> str:
    """Produce the <CamelCaseTitle> token for the PDF filename (§11.7 step 3).

    Rules:
    - ASCII-fold.
    - Split on any non-alphanumeric character (all become token boundaries).
    - For each token: if acronym (see _is_acronym_token), keep as-is;
      else TitleCase (first char upper, rest lower).
    - Filter empty tokens.
    - Concatenate tokens until adding the next would exceed 100 chars.
      If the very first token alone exceeds 100 chars, hard-truncate to 100.
    - Empty result -> 'UntitledNonLatin'.
    """
    if not title:
        return "UntitledNonLatin"

    folded = _ascii_fold(title)
    raw_tokens = re.split(r"[^a-zA-Z0-9]+", folded)

    processed = []
    for tok in raw_tokens:
        if not tok:
            continue
        if _is_acronym_token(tok):
            processed.append(tok)
        else:
            processed.append(tok[0].upper() + tok[1:].lower() if tok else "")

    if not processed:
        return "UntitledNonLatin"

    # Accumulate tokens up to the 100-char budget.
    parts: list[str] = []
    total = 0
    for tok in processed:
        if total + len(tok) <= 100:
            parts.append(tok)
            total += len(tok)
        else:
            if not parts:
                # First token alone exceeds budget: hard-truncate it.
                parts.append(tok[:100])
            break

    return "".join(parts) if parts else "UntitledNonLatin"


def build_pdf_filename(
    first_author_family: str | None,
    year: int | None,
    title: str | None,
) -> str:
    """Return the PDF filename (no directory prefix) per §11.7.

    Pattern:  <LastName>_<Year>_<CamelCaseTitle>_<hash8>.pdf

    The <hash8> is derived from build_canonical_string, not from the
    human-readable components of the filename.  That is, the hash and the
    readable parts are independently derived from the same three inputs --
    they do not hash each other.
    """
    last_part = _filename_lastname(first_author_family)
    year_part = _filename_year(year)
    title_part = _filename_camel_title(title)

    canonical = build_canonical_string(first_author_family, year, title)
    hash8 = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:8]

    return f"{last_part}_{year_part}_{title_part}_{hash8}.pdf"
