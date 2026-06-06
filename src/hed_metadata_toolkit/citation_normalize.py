r"""
citation_normalize.py — DOI/URL canonicalisation and junk-link detection.

Pure functions; the only I/O is `load_skip_list`, which reads a text file.
No network calls.  This module is the building block for both the
one-shot `migrate_citations.py` (Session 2B) and the permanent
`assign_citation_ids.py` (Session 2C).

Algorithm sources:
  - .status/citation_id_design_v2.md §4 (current spec, including
    §4.3 publisher → DOI synthesis patterns and §4.3.2 whitespace
    handling)
  - .status/citation_id_design.md §2 (v1; v2 inherits §2 unchanged)
  - .status/cross_repo_id_thinking_2026-05-01.md §3-5 (rationale)

Public surface:

    extract_doi(link)              → str | None   canonical DOI or None
    canonicalize_doi(doi)          → str          canonical DOI form
    canonicalize_url(url)          → str          canonical URL form
    is_junk_link(link, patterns)   → bool         skip-list match
    synthesise_doi_from_url(url)   → str | None   publisher → DOI
    load_skip_list(path)           → list[str]    read citation_skip_list.txt

Implementation note (priority of extract_doi):
    The v1 §2.1 spec lists the priority as (1) bare-DOI regex,
    (2) doi.org URL, (3) publisher synthesis.  Step (1) is
    permissive — `\b10\.\d{4,9}/[-._;()/:A-Z0-9]+` matches across
    `/` boundaries, so a Frontiers URL like
    `frontiersin.org/.../articles/10.3389/fnhum.2024.1329086/full`
    captures `10.3389/fnhum.2024.1329086/full` (with a spurious
    `/full` suffix).  Publisher synthesis is tighter — it stops at
    the article-id boundary.  We therefore try synthesis FIRST for
    any URL that matches a known publisher pattern, then fall back
    to the bare regex for everything else.  Same end result for
    `doi.org` URLs (regex catches them); strictly better result
    for publisher URLs.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


# ---------------------------------------------------------------------------
# DOI patterns
# ---------------------------------------------------------------------------

_DOI_REGEX = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)

_DOI_PREFIX_RE = re.compile(
    r"^(doi:|https?://(dx\.)?doi\.org/)",
    re.IGNORECASE,
)

# Trailing punctuation to strip after canonicalisation.  Whitespace and
# literal '\n' / '\r' escape sequences are stripped separately (v2 §4.3.2
# finding — the production data contains links with the 2-char sequence
# '\' + 'n' as a suffix, presumably a TSV-write artefact from the
# original collection script).
_TRAILING_PUNCT = ".,;)]>"

# One regex pattern handles both real whitespace (incl. \n / \r chars) and
# the literal 2-char sequences `\n` / `\r` that appear in some legacy rows.
_LEADING_JUNK_RE = re.compile(r"^(?:\s|\\[rn])+")
_TRAILING_JUNK_RE = re.compile(r"(?:\s|\\[rn])+$")


def _strip_outer_junk(s: str) -> str:
    """Strip leading and trailing whitespace and literal '\\n' / '\\r'."""
    s = _LEADING_JUNK_RE.sub("", s)
    return _TRAILING_JUNK_RE.sub("", s)


def canonicalize_doi(doi: str) -> str:
    """Return canonical DOI form.

    Rules (v1 §2.2 + v2 §4.3.2):
      - Strip surrounding whitespace (incl. trailing newline) and any
        literal '\\n' / '\\r' escape sequences.
      - Strip leading `doi:` or `https?://(dx.)?doi.org/`.
      - Lowercase the whole string (DOIs are case-insensitive by spec).
      - Strip trailing punctuation (`. , ; ) ] >`).
      - Re-strip whitespace revealed by punctuation removal.
    """
    s = _strip_outer_junk(doi)
    s = _DOI_PREFIX_RE.sub("", s)
    s = s.lower()
    while s and s[-1] in _TRAILING_PUNCT:
        s = s[:-1]
    return _strip_outer_junk(s)


# ---------------------------------------------------------------------------
# Publisher URL → DOI synthesis (v2 §4.3)
# ---------------------------------------------------------------------------

def _synth_nature(url: str) -> str | None:
    """nature.com/articles/<suffix> → 10.1038/<suffix>"""
    m = re.search(r"nature\.com/articles/([^/?#\s]+)", url, re.IGNORECASE)
    return f"10.1038/{m.group(1)}" if m else None


def _synth_springer(url: str) -> str | None:
    """link.springer.com/article/<DOI> — DOI is literal in the path."""
    m = re.search(
        r"link\.springer\.com/article/(10\.\d{4,9}/[^/?#\s]+)",
        url, re.IGNORECASE,
    )
    return m.group(1) if m else None


def _synth_plos(url: str) -> str | None:
    """journals.plos.org/<journal>/article?id=<DOI>"""
    m = re.search(
        r"journals\.plos\.org/[^/]+/article\?id=(10\.\d{4,9}/[^&\s]+)",
        url, re.IGNORECASE,
    )
    return m.group(1) if m else None


def _synth_tandfonline(url: str) -> str | None:
    """tandfonline.com/doi/(full|abs|epdf)/<DOI>"""
    m = re.search(
        r"tandfonline\.com/doi/(?:full|abs|epdf)/(10\.\d{4,9}/[^/?#\s]+)",
        url, re.IGNORECASE,
    )
    return m.group(1) if m else None


def _synth_mit(url: str) -> str | None:
    """direct.mit.edu paths sometimes embed a literal DOI segment."""
    m = re.search(
        r"direct\.mit\.edu/.*?(10\.\d{4,9}/[^/?#\s]+)",
        url, re.IGNORECASE,
    )
    return m.group(1) if m else None


def _synth_frontiers(url: str) -> str | None:
    """frontiersin.org/journals/<journal>/articles/10.3389/<id>/..."""
    m = re.search(
        r"frontiersin\.org/journals/[^/]+/articles/(10\.\d{4,9}/[^/?#\s]+)",
        url, re.IGNORECASE,
    )
    return m.group(1) if m else None


_SYNTH_FUNCS = [
    _synth_nature,
    _synth_springer,
    _synth_plos,
    _synth_tandfonline,
    _synth_mit,
    _synth_frontiers,
]


def synthesise_doi_from_url(url: str) -> str | None:
    """Return a synthesised DOI for known publisher URL patterns; None
    otherwise.

    Conservative: only matches the six v2 §4.3 patterns.  Hosts listed
    in v2 §4.3.1 (Cambridge, eLife, OSF, NCBI/PubMed) deliberately stay
    URL-only.
    """
    s = url.strip()
    for fn in _SYNTH_FUNCS:
        result = fn(s)
        if result is not None:
            return result
    return None


# ---------------------------------------------------------------------------
# DOI extraction
# ---------------------------------------------------------------------------

def extract_doi(link: str) -> str | None:
    """Return the bare canonical DOI ('10.xxxx/yyy') if findable; else None.

    Priority (see module docstring for the v1-vs-implementation note):
      1. Publisher URL synthesis (tightest result for known hosts).
      2. Bare-DOI regex (catches doi.org URLs, doi: prefixes, and
         inline bare DOIs).
    """
    s = _strip_outer_junk(link)

    synth = synthesise_doi_from_url(s)
    if synth:
        return canonicalize_doi(synth)

    m = _DOI_REGEX.search(s)
    if m:
        return canonicalize_doi(m.group(0))

    return None


# ---------------------------------------------------------------------------
# URL canonicalisation
# ---------------------------------------------------------------------------

_HTTP_PREFIX_RE = re.compile(r"^https?://", re.IGNORECASE)


def canonicalize_url(url: str) -> str:
    """Return canonical URL form.

    Rules (v1 §2.3 + v2 §4.3.2):
      - Strip surrounding whitespace and literal '\\n' / '\\r'.
      - If the URL has no scheme but starts with `www.`, prepend `https://`.
        Force `https://` over `http://`.
      - Lowercase the scheme and host.  Path remains case-sensitive.
      - Drop the `#fragment` and any query parameter whose name starts
        with `utm_` (case-insensitive).
      - Strip trailing `/`, then trailing punctuation, then whitespace
        revealed by either.
    """
    s = _strip_outer_junk(url)

    if not _HTTP_PREFIX_RE.match(s) and s.lower().startswith("www."):
        s = "https://" + s

    if s.lower().startswith("http://"):
        s = "https://" + s[len("http://"):]

    parts = urlsplit(s)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()

    pairs = parse_qsl(parts.query, keep_blank_values=True)
    pairs = [(k, v) for k, v in pairs if not k.lower().startswith("utm_")]
    query = urlencode(pairs)

    fragment = ""

    canonical = urlunsplit((scheme, netloc, parts.path, query, fragment))

    canonical = _strip_outer_junk(canonical)
    while canonical and canonical[-1] in _TRAILING_PUNCT + "/":
        canonical = canonical[:-1]
    return _strip_outer_junk(canonical)


# ---------------------------------------------------------------------------
# Junk-link classification
# ---------------------------------------------------------------------------

_JUNK_FILE_EXTENSIONS = (".zip", ".tar.gz", ".exe", ".dmg")


def is_junk_link(link: str, skip_patterns: list[str]) -> bool:
    """Return True iff `link` should be classified as not-a-citation.

    A link is junk if any of the following holds:
      - It contains any pattern from `skip_patterns` as a
        case-insensitive substring.
      - It ends in a known non-citation file extension.
    """
    lc = link.lower()
    for pattern in skip_patterns:
        if pattern.lower() in lc:
            return True
    for ext in _JUNK_FILE_EXTENSIONS:
        if lc.endswith(ext):
            return True
    return False


# ---------------------------------------------------------------------------
# Skip-list loader
# ---------------------------------------------------------------------------

def load_skip_list(path) -> list[str]:
    """Read citation_skip_list.txt and return the active patterns.

    File format:
      - One pattern per line.
      - Lines beginning with `#` are comments (ignored).
      - Blank / whitespace-only lines are ignored.
      - Whitespace around each pattern is stripped.
    """
    patterns = []
    text = Path(path).read_text(encoding="utf-8")
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        patterns.append(s)
    return patterns
