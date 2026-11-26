"""Focused-window text capture via the macOS Accessibility (AX) API.

No screenshots, no OCR — the AX tree of the focused window is walked and the
text values of text-bearing elements are concatenated. This is the m1
capture verb validated (in shape) by ambient-context; SuJi keeps it on-device.

The pyobjc import is guarded: on a non-Darwin host (or without the
``macos`` extra installed) importing this module is fine, but
:class:`MacAXCapturer` cannot be constructed — :class:`CaptureUnavailable`
is raised. Tests and the headless CLI inject a :class:`Capturer` fake, so
the capture layer is exercised without any macOS API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol, runtime_checkable

from .cascade import SourceFetcher, make_fetcher, read_source_content
from .llm import FactExtractor
from .provenance import (
    ExtractedFact,
    SourceKind,
    SourceRef,
    fingerprint,
    make_source_ref,
)
from .store import SuJiStore

# The macOS AX bindings are imported lazily and defensively; the module must
# remain importable on Linux (CI / demo render) and without the macos extra.
_AX_AVAILABLE = False
_AX = {}
try:  # pragma: no cover - exercised only on a real mac with the extra
    from ApplicationServices import (  # type: ignore
        AXUIElementCopyAttributeValue,
        AXUIElementCreateSystemWide,
        AXUIElementGetPid,
        kAXChildrenAttribute,
        kAXFocusedApplicationAttribute,
        kAXFocusedWindowAttribute,
        kAXRoleAttribute,
        kAXTitleAttribute,
        kAXValueAttribute,
    )

    _AX_AVAILABLE = True
except Exception:  # ImportError / framework unavailable off-Darwin
    _AX_AVAILABLE = False


class CaptureUnavailable(RuntimeError):
    """Raised when the macOS AX capturer is used without its platform deps."""


@dataclass
class CapturedText:
    """What a capture produced: the text + where it came from.

    ``app_bundle`` is the source-app tag (bundle id when resolvable, else
    the app's localized title). ``doc_url_or_id`` is the cascade key — a
    URL for a 公众号 article, a path for a local file, a synthetic id
    (``<app>:<window-title>``) when nothing better is exposed.
    """

    text: str
    app_bundle: str
    doc_url_or_id: str
    doc_title: str
    capture_context: str = ""


@runtime_checkable
class Capturer(Protocol):
    """Anything that can produce a :class:`CapturedText` on demand."""

    def capture(self) -> CapturedText: ...


def _looks_like_url(value: str) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def _is_text_role(role: Optional[str]) -> bool:
    if not isinstance(role, str):
        return False
    return role in {
        "AXTextArea",
        "AXTextField",
        "AXStaticText",
        "AXTextGroup",
        "AXText",
    }


class MacAXCapturer:
    """Capture the focused window's text via the macOS Accessibility API.

    Construction is cheap; :meth:`capture` does the real work. Requires the
    ``macos`` extra (``pyobjc-framework-ApplicationServices``) AND a granted
    Accessibility permission. Raises :class:`CaptureUnavailable` otherwise.
    """

    def __init__(self, *, max_chars: int = 24_000, max_depth: int = 14):
        if not _AX_AVAILABLE:
            raise CaptureUnavailable(
                "macOS Accessibility bindings unavailable — install the "
                "'macos' extra (pyobjc-framework-ApplicationServices) on macOS "
                "and grant Accessibility permission."
            )
        self._max_chars = max_chars
        self._max_depth = max_depth

    # -- AX plumbing ------------------------------------------------
    @staticmethod
    def _attr(elem, attr: str):
        """Read one AX attribute; return None if absent/unsupported."""
        err, value = AXUIElementCopyAttributeValue(elem, attr, None)
        if err != 0 or value is None:
            return None
        return value

    def _focused_app(self):
        system = AXUIElementCreateSystemWide()
        return self._attr(system, kAXFocusedApplicationAttribute)

    def _focused_window(self, app):
        if app is None:
            return None
        return self._attr(app, kAXFocusedWindowAttribute)

    def _walk_text(self, elem, depth: int, out: list[str]) -> None:
        """Recursively collect text values from text-bearing elements."""
        if elem is None or depth <= 0 or sum(len(s) for s in out) >= self._max_chars:
            return
        role = self._attr(elem, kAXRoleAttribute)
        if _is_text_role(role):
            value = self._attr(elem, kAXValueAttribute)
            if isinstance(value, str) and value.strip():
                out.append(value)
        children = self._attr(elem, kAXChildrenAttribute)
        if isinstance(children, list):
            for child in children:
                self._walk_text(child, depth - 1, out)

    def _find_url(self, app) -> Optional[str]:
        """Best-effort URL harvest for browser apps.

        公众号 articles live at a URL; for Safari/Chrome/Edge the address
        bar is an AXTextField whose value is the URL. Deep per-app parsing
        is m3+; for m1 we look for any text value that looks like a URL.
        """
        found: list[str] = []

        def scan(elem, depth: int) -> None:
            if elem is None or depth <= 0 or found:
                return
            role = self._attr(elem, kAXRoleAttribute)
            if role in {"AXTextField", "AXStaticText"}:
                value = self._attr(elem, kAXValueAttribute)
                if _looks_like_url(value):
                    found.append(value)  # type: ignore[arg-type]
                    return
            children = self._attr(elem, kAXChildrenAttribute)
            if isinstance(children, list):
                for child in children:
                    scan(child, depth - 1)

        win = self._focused_window(app)
        scan(win, 8)
        scan(app, 6)
        return found[0] if found else None

    @staticmethod
    def _bundle_id_for_app(app) -> str:
        """Resolve the focused app's bundle id, falling back to its title.

        Bundle id needs AppKit (NSRunningApplication by PID); AppKit is not
        in the v0.1 dep set, so it is used opportunistically — otherwise the
        app's AX title tags the source app (the field stays a non-empty tag).
        """
        pid = AXUIElementGetPid(app) if app is not None else 0
        if pid:
            try:  # opportunistic; AppKit is not a declared dependency
                from AppKit import NSRunningApplication  # type: ignore

                running = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)  # noqa: E501
                bid = running.bundleIdentifier() if running else None
                if bid:
                    return str(bid)
            except Exception:
                pass
        return "mac.focused-app"

    # -- public API -------------------------------------------------
    def capture(self) -> CapturedText:
        app = self._focused_app()
        if app is None:
            raise CaptureUnavailable(
                "No focused application — grant Accessibility permission "
                "(System Settings → Privacy & Security → Accessibility)."
            )
        app_tag = self._bundle_id_for_app(app)
        win = self._focused_window(app)
        win_title = self._attr(win, kAXTitleAttribute) if win is not None else ""
        win_title = win_title if isinstance(win_title, str) else ""

        text_parts: list[str] = []
        self._walk_text(win if win is not None else app, self._max_depth, text_parts)
        text = "\n".join(p for p in text_parts if p)

        url = self._find_url(app)
        if url:
            doc_url_or_id, doc_title = url, win_title or url
        else:
            # No URL exposed — address the source by the window title (a
            # DOC_ID source; cascade will treat it as unverifiable, which is
            # honest for apps whose content isn't URL-addressable in v0.1).
            doc_title = win_title or app_tag
            doc_url_or_id = f"{app_tag}:{doc_title}" if win_title else app_tag

        return CapturedText(
            text=text,
            app_bundle=app_tag,
            doc_url_or_id=doc_url_or_id,
            doc_title=doc_title,
            capture_context="ax",
        )


def capture_pipeline(capturer: Capturer) -> CapturedText:
    """Run one capture (used by the menu-bar loop + a one-shot CLI hook)."""
    return capturer.capture()


def source_content_for(
    ref: SourceRef,
    captured_text: str,
    capture_fingerprint: str,
    store: SuJiStore,
    fetcher_for: Optional[Callable[[SourceRef], SourceFetcher]] = None,
) -> str:
    """The source's own content for this capture.

    The cascade re-reads a source through its fetcher, so the fingerprint the
    source is stored under must be computed over that same representation: a
    公众号 page's fetched content, or a local file's content — NOT the window
    text the AX walk produced. They are different representations of the same
    document and never fingerprint equal, so mixing them made every re-check
    report a mutation. Non-fetchable sources (钉钉 / WPS cloud doc ids, which v0.1
    cannot re-read) keep the captured text as their content.

    The source is only read when the window text changed since the last
    capture; the 5-second loop otherwise reuses the content already stored.

    When the read yields nothing (offline, unreadable, 0-byte), the revision
    already recorded for this source is reused: the window text is the other
    representation the re-check compares against, so stamping it as the
    source's content would make the next successful re-check report the whole
    document as rewritten. Only a source with no recorded revision at all
    falls back to the window text, because nothing better exists for it.
    """
    if ref.source_kind is SourceKind.DOC_ID:
        return captured_text

    existing = store.get_source_by_doc(ref.doc_url_or_id)
    if existing is not None and (
        store.source_capture_fingerprint(existing.id) == capture_fingerprint  # type: ignore[arg-type]
    ):
        return store.source_last_content(existing.id) or captured_text  # type: ignore[arg-type]

    fetcher = (fetcher_for or make_fetcher)(ref)
    content = read_source_content(fetcher, ref)
    if content is not None:
        return content
    if existing is not None:
        known = store.source_last_content(existing.id)  # type: ignore[arg-type]
        if known.strip():
            return known
    return captured_text


def ingest_capture(
    capturer: Capturer,
    extractor: FactExtractor,
    store: SuJiStore,
    *,
    fetcher_for: Optional[Callable[[SourceRef], SourceFetcher]] = None,
) -> int:
    """Run one capture → extract → persist cycle; return the fact count.

    This is the m1 happy path, factored out of the menu-bar app so the test
    suite and ``examples/seed_demo.py`` can drive it with a fake capturer +
    the rule-based extractor, without rumps or a live Qwen3.

    Each fact is stored with ``source_fingerprint`` = the source's own content
    fingerprint at capture time (the cascade invariant). Its text keeps the
    captured wording; de-duplication compares the canonical form, so the
    re-capture of the same window cannot store the same fact twice.

    ``fetcher_for`` overrides how a source is read back (defaults to the same
    ``make_fetcher`` dispatch the cascade uses); tests inject a fake.
    """
    captured = capturer.capture()
    text = captured.text or ""
    if not text.strip():
        return 0
    ref = make_source_ref(
        app_bundle=captured.app_bundle,
        doc_url_or_id=captured.doc_url_or_id,
        doc_title=captured.doc_title,
        capture_context=captured.capture_context,
    )
    capture_fp = fingerprint(text)
    source_id = store.upsert_source(
        ref, source_content_for(ref, text, capture_fp, store, fetcher_for)
    )
    store.record_capture(source_id, capture_fp)
    fp = store.source_fingerprint(source_id)

    count = 0
    for ef in extractor.extract(text):
        if not ef.text.strip():
            continue
        if store.has_fact(source_id, ef.text, fp):
            continue
        store.add_fact(
            source_id=source_id,
            text=ef.text,
            source_fingerprint=fp,
            note=ef.note,
        )
        count += 1
    return count
