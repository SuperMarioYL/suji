"""Cascade-invalidation on source mutation — the m2 moat.

The capture-time fingerprint stored on each fact is compared against the
source's *current* content fingerprint. When a 公众号 article is edited or
a local WPS / 钉钉-cache file changes, the source's fingerprint moves and
every fact captured from the prior content is marked stale — with the
old/new diff attached so ``suji stale`` can show exactly what shifted.

This is the verb ambient-context lacks: not just ambient capture, but
*per-fact* staleness when the origin mutates. The cascade, not the user,
writes ``status`` — honoring the mvp_plan.md §2 invariant.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, runtime_checkable

from . import __version__
from .provenance import (
    CascadeResult,
    SourceKind,
    SourceRef,
    fingerprint,
    source_diff,
)
from .store import SuJiStore

_FETCH_TIMEOUT = 12.0  # seconds — URL re-fetch must not hang the loop
_UA = (
    f"Mozilla/5.0 (compatible; SuJi/{__version__}; "
    "+https://github.com/SuperMarioYL/suji)"
)


@runtime_checkable
class SourceFetcher(Protocol):
    """Re-read a source's current content for re-fingerprinting."""

    def fetch(self, source: SourceRef) -> Optional[str]: ...


class UrlFetcher:
    """Re-fetch a URL source (公众号 article / web page) over HTTP."""

    def __init__(self, timeout: float = _FETCH_TIMEOUT, user_agent: str = _UA):
        self._timeout = timeout
        self._ua = user_agent

    def fetch(self, source: SourceRef) -> Optional[str]:
        req = urllib.request.Request(
            source.doc_url_or_id, headers={"User-Agent": self._ua}
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                raw = resp.read()
        except (urllib.error.URLError, OSError, ValueError):
            return None
        # 公众号 articles are UTF-8 HTML; the fingerprint is over the raw
        # fetched bytes-as-text so a re-fetch of the same content is stable.
        charset = "utf-8"
        try:
            return raw.decode(charset, errors="replace")
        except Exception:
            return None


class FileFetcher:
    """Re-read a local file source (WPS document / 钉钉 cache file)."""

    def __init__(self, encoding: str = "utf-8"):
        self._encoding = encoding

    def fetch(self, source: SourceRef) -> Optional[str]:
        path = source.doc_url_or_id
        try:
            with open(path, "rb") as fh:
                return fh.read().decode(self._encoding, errors="replace")
        except (OSError, ValueError):
            return None


class DocIdFetcher:
    """Cloud-doc-by-id fetcher — unverifiable in v0.1.

    钉钉 / WPS cloud docs need their vendor's API + auth to re-fetch;
    that deep integration is m3+ (out of scope for v0.1). Returning None
    signals "could not re-verify" so the cascade skips rather than
    falsely marking facts stale.
    """

    def fetch(self, source: SourceRef) -> Optional[str]:
        return None


def make_fetcher(source: SourceRef) -> SourceFetcher:
    """Pick the fetcher for a source kind."""
    if source.source_kind is SourceKind.URL:
        return UrlFetcher()
    if source.source_kind is SourceKind.FILE:
        return FileFetcher()
    return DocIdFetcher()


def read_source_content(fetcher: SourceFetcher, source: SourceRef) -> Optional[str]:
    """The source's current content, or None when it cannot be read.

    A blank result is not content: an editor's truncate-then-write save leaves
    a 0-byte file for a moment (the watchdog fires inside that window) and an
    empty response body carries nothing to compare. Fingerprinting it would
    stamp the source empty — destroying the revision the diff is built from —
    and cascade every fact stale, so both readers (capture and re-check) treat
    it exactly like an unreadable source.
    """
    content = fetcher.fetch(source)
    if content is None or not content.strip():
        return None
    return content


@dataclass(frozen=True)
class RecheckSummary:
    """What a re-check actually verified.

    ``verified`` counts the sources whose content was read this run. A run
    with ``verified == 0`` compared nothing, so "no stale facts" from it is an
    absence of evidence, not a passing check.
    """

    sources: int
    verified: int
    mutated: int

    @property
    def unverifiable(self) -> int:
        return self.sources - self.verified


def summarize_recheck(results: list[CascadeResult]) -> RecheckSummary:
    """Classify a re-check run, so every reporter agrees on what it verified."""
    return RecheckSummary(
        sources=len(results),
        verified=sum(1 for r in results if not r.error),
        mutated=sum(1 for r in results if r.mutated),
    )


class Cascade:
    """Re-verify source fingerprints and cascade-stale derived facts.

    Holds a reference to the store; each :meth:`recheck_source` is
    independent so it can be driven by a periodic loop (URL sources) or by
    a file watcher (local WPS / 钉钉-cache sources).
    """

    def __init__(
        self,
        store: SuJiStore,
        *,
        fetcher_for: Optional[Callable[[SourceRef], SourceFetcher]] = None,
    ):
        self._store = store
        self._fetcher_for = fetcher_for or make_fetcher

    def recheck_source(self, source_id: int) -> CascadeResult:
        source = self._store.get_source(source_id)
        if source is None:
            return CascadeResult(source_id=source_id, error="source not found")
        fetcher = self._fetcher_for(source)
        content = read_source_content(fetcher, source)
        if content is None:
            # Could not re-fetch (offline / cloud doc / file gone / blank read)
            # — do not mark anything stale; the prior facts stay fresh pending
            # the next successful re-check.
            return CascadeResult(
                source_id=source_id,
                error="unverifiable",
                new_fingerprint=self._store.source_fingerprint(source_id),
            )

        new_fp = fingerprint(content)
        current_fp = self._store.source_fingerprint(source_id)
        mutated = new_fp != current_fp

        if mutated:
            # The source mutated since the last check. Diff the last-known
            # content against the new content and advance the stored
            # fingerprint before staling the out-of-date facts.
            old_content = self._store.source_last_content(source_id)
            diff = source_diff(old_content, content)
            self._store.update_source_fingerprint(source_id, new_fp, content)
        else:
            # No mutation since the last check — refresh the check timestamp
            # only. Facts captured from prior content are still swept below:
            # a re-capture between checks advances the stored fingerprint
            # past a mutation, and those facts must not stay fresh forever.
            diff = ""
            self._store.mark_source_checked(source_id)

        # Stale every fresh fact whose capture-time fingerprint no longer
        # matches the current content — both when the mutation was detected
        # here and when it happened between checks (the heal path, where no
        # old content survives to diff, so the fact carries an empty diff).
        stale_count = 0
        for fact in self._store.list_facts(source_id=source_id):
            if fact.source_fingerprint != new_fp and fact.status != "stale":
                self._store.mark_fact_stale(fact.id, diff=diff)
                stale_count += 1
        return CascadeResult(
            source_id=source_id,
            mutated=mutated,
            stale_count=stale_count,
            diff=diff,
            new_fingerprint=new_fp,
        )

    def recheck_all(self) -> list[CascadeResult]:
        """Re-check every known source (the periodic-loop entry point)."""
        results: list[CascadeResult] = []
        for source in self._store.list_sources():
            results.append(self.recheck_source(source.id))  # type: ignore[arg-type]
        return results


def start_file_watcher(
    paths: list[str],
    on_change: Callable[[str], None],
) -> "object":
    """Watch local file sources with watchdog; fire ``on_change(path)`` on edit.

    ``path`` is the key the caller passed in, not the path watchdog reports:
    watchdog resolves symlinks (macOS ``/tmp`` is ``/private/tmp``), so a caller
    that looks a source up by its stored key verbatim would otherwise never
    match and the edit would be dropped silently.

    Returns the started Observer (call ``.stop()`` / ``.join()`` to end).
    ``watchdog`` is a core dep and installs cross-platform; the watcher only
    fires for paths that exist at start time.
    """
    from watchdog.events import FileSystemEventHandler  # type: ignore
    from watchdog.observers import Observer  # type: ignore

    import os

    by_realpath = {os.path.realpath(p): p for p in paths}

    class _Handler(FileSystemEventHandler):
        def _emit(self, event) -> None:
            if event.is_directory:
                return
            key = by_realpath.get(os.path.realpath(event.src_path))
            if key is not None:
                on_change(key)

        def on_modified(self, event):  # type: ignore[override]
            self._emit(event)

        def on_created(self, event):  # type: ignore[override]
            self._emit(event)

    observer = Observer()
    seen: set[str] = set()
    for path in paths:
        watch = os.path.dirname(os.path.realpath(path)) or "."
        if watch in seen:
            continue
        seen.add(watch)
        observer.schedule(_Handler(), watch, recursive=False)
    observer.start()

    # The caller resolves path→source_id via the store and calls
    # cascade.recheck_source; _Handler already delivers the caller's own key.
    return observer
