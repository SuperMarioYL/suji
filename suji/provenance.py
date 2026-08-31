"""Per-fact source provenance — the core primitive.

Owns the :class:`SourceRef` data model, the content fingerprint, and the
old/new source diff. A fact is only useful if it remembers *where it came
from*; this module is the single source of truth for what "where" means and
for how a source is shown to have mutated.

Invariants (from mvp_plan.md §2):

* Every captured fact carries a ``source_fingerprint`` — the sha256 of the
  source document's content at capture time.
* ``SourceRef.doc_url_or_id`` is the cascade addressing key. A 公众号
  article is addressed by URL; a 钉钉 doc by doc id; a local WPS file by
  path.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import difflib


class SourceKind(str, Enum):
    """How a source document is addressed for cascade re-checking."""

    URL = "url"      # 公众号 article / web page — re-fetched over HTTP
    FILE = "file"    # local WPS / 钉钉 cache file — re-read from disk / watchdog
    DOC_ID = "doc_id"  # 钉钉 / WPS cloud doc by id — unverifiable in v0.1 (no API)


@dataclass(frozen=True)
class SourceRef:
    """A pointer to where a captured fact came from.

    ``doc_url_or_id`` is the cascade key: for a URL it is the article URL,
    for a local file it is the absolute path, for a cloud doc it is the
    provider's doc id. ``source_kind`` tells the cascade how to re-fetch it.
    """

    app_bundle: str
    doc_url_or_id: str
    doc_title: str
    source_kind: SourceKind
    capture_context: str = ""
    # Assigned by the store on insert; ``None`` for an unsaved ref.
    id: Optional[int] = None


def _normalize(text: str) -> str:
    """Collapse incidental whitespace for fingerprint stability.

    A capture of the same article a few seconds apart may differ only in
    incidental whitespace introduced by the AX tree walk — trailing spaces
    per line, doubled spaces, extra blank lines, CRLF vs LF. Collapsing
    those keeps the fingerprint from false-flipping on every re-check,
    while still detecting real content edits (a number changing).
    Paragraph structure (a blank line) is preserved.
    """
    if text is None:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse runs of spaces / tabs / nbsp to a single space.
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    # Strip trailing/leading spaces per line (common AX re-walk jitter),
    # preserving blank lines (paragraph structure is meaningful).
    text = "\n".join(line.strip(" \t\u00a0") for line in text.split("\n"))
    # Collapse 3+ consecutive newlines to 2.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fingerprint(text: str) -> str:
    """SHA-256 of the normalized source content.

    This is the value stored on every fact at capture time and on the source
    record as the "last known current" fingerprint. Cascade compares them.
    """
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


def infer_kind(doc_url_or_id: str) -> SourceKind:
    """Infer the source kind from the addressing key.

    ``http(s)://`` → URL, an absolute path (``/...`` or ``~/...``) → FILE,
    anything else is treated as a cloud doc id.
    """
    if not doc_url_or_id:
        return SourceKind.DOC_ID
    key = doc_url_or_id.strip()
    if key.startswith(("http://", "https://")):
        return SourceKind.URL
    if key.startswith(("/", "~", ".")):
        return SourceKind.FILE
    # A bare path on macOS may lack a leading slash if captured from a
    # relative context; accept a path-like token with an extension.
    if re.search(r"[\\/]\S+\.(docx|doc|wps|txt|md|pdf)$", key, re.IGNORECASE):
        return SourceKind.FILE
    return SourceKind.DOC_ID


def make_source_ref(
    app_bundle: str,
    doc_url_or_id: str,
    doc_title: str,
    capture_context: str = "",
) -> SourceRef:
    """Build a :class:`SourceRef`, inferring the kind from the address."""
    return SourceRef(
        app_bundle=app_bundle,
        doc_url_or_id=doc_url_or_id,
        doc_title=doc_title,
        source_kind=infer_kind(doc_url_or_id),
        capture_context=capture_context,
    )


def source_diff(old: str, new: str, context_lines: int = 2) -> str:
    """A compact unified diff of the old vs new source content.

    Returned as a single string; empty when the two are identical (after
    normalization). Stored on each fact marked stale so ``suji stale`` can
    show exactly what changed under the captured fact.
    """
    old_n = _normalize(old).splitlines() if old else []
    new_n = _normalize(new).splitlines() if new else []
    diff = difflib.unified_diff(
        old_n,
        new_n,
        fromfile="来源(抓取时)",
        tofile="来源(当前)",
        lineterm="",
        n=context_lines,
    )
    return "\n".join(diff)


@dataclass
class ExtractedFact:
    """A fact extracted from a source, before it is persisted.

    The LLM (or rule-based fallback) only fills ``text``; the capture
    pipeline attaches the ``source_fingerprint`` of the source content it
    was read from, so the fact is bound to the source state at capture time.
    """

    text: str
    source_fingerprint: str = ""
    # Optional free-form note the extractor may attach (e.g. a category).
    note: str = ""


@dataclass
class Fact:
    """A persisted fact with its source provenance."""

    id: Optional[int]
    text: str
    source_id: int
    captured_at: str  # ISO-8601 UTC
    source_fingerprint: str
    status: str = "fresh"  # "fresh" | "stale"
    diff: str = ""  # populated by cascade when status flips to "stale"
    note: str = ""


@dataclass
class CascadeResult:
    """Outcome of re-checking one source."""

    source_id: int
    mutated: bool = False
    stale_count: int = 0
    diff: str = ""
    new_fingerprint: str = ""
    error: str = ""
