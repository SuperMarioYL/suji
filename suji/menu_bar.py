"""The macOS menu-bar resident (rumps) — capture loop + cascade watcher.

A menu-bar app that, when "开始记忆" is on, captures the focused window's
text every few seconds via the AX API, extracts discrete facts with the
local Qwen3, and stores each with per-source provenance. It also runs the
file watcher so local WPS / 钉钉-cache sources cascade-stale in real time.

``rumps`` is imported lazily inside :func:`main`; importing this module
without the ``macos`` extra is harmless (the class body references rumps
only via ``rumps.App`` at class-creation, so the import is deferred to
``main``).
"""

from __future__ import annotations

from typing import Optional

from .capture import Capturer, MacAXCapturer, ingest_capture
from .cascade import Cascade, start_file_watcher
from .llm import FactExtractor, make_extractor
from .store import SuJiStore

_CAPTURE_INTERVAL = 5  # seconds between captures when "开始记忆" is on


def _build_app(capturer: Capturer, extractor: FactExtractor, store: SuJiStore):
    """Construct the rumps app. rumps is imported here so this module is
    importable without the macos extra (tests / headless CLI)."""
    import rumps  # type: ignore

    class SuJiApp(rumps.App):  # type: ignore[misc]
        def __init__(self):  # type: ignore[override]
            super().__init__(
                "溯记",
                icon=None,
                menu=["开始记忆", "立即抓取", None, "校验失效", None, "Quit"],
            )
            self._capturer = capturer
            self._extractor = extractor
            self._store = store
            self._timer: Optional[object] = None
            self._watcher: Optional[object] = None
            self._start_file_watcher()

        # -- capture loop ----------------------------------------------
        def _start_file_watcher(self) -> None:
            """Watch local-file sources so edits cascade-stale in real time."""
            file_paths = []
            for src in self._store.list_sources():
                if src.source_kind.value == "file":
                    file_paths.append(src.doc_url_or_id)
            if not file_paths:
                return

            def on_change(path: str) -> None:
                # Resolve the (possibly multiple) source(s) for this path
                # and re-check each. The cascade re-reads the file itself.
                cascade = Cascade(self._store)
                for src in self._store.list_sources():
                    if src.doc_url_or_id == path:
                        cascade.recheck_source(src.id)  # type: ignore[arg-type]

            try:
                self._watcher = start_file_watcher(file_paths, on_change)
            except Exception:
                # Watcher is a real-time enhancement; if it can't start
                # (e.g. paths missing), the CLI ``suji stale`` still works.
                self._watcher = None

        @rumps.timer(_CAPTURE_INTERVAL)  # type: ignore[attr-defined]
        def _tick(self, _sender):  # type: ignore[override]
            """Periodic capture — only acts when '开始记忆' is on."""
            if self._timer is None:
                return
            try:
                n = ingest_capture(self._capturer, self._extractor, self._store)
                if n:
                    rumps.notification(  # type: ignore[attr-defined]
                        "溯记", f"记下 {n} 条事实", ""
                    )
            except Exception as exc:  # capture may fail without permission
                rumps.notification("溯记", "抓取失败", str(exc))  # type: ignore[attr-defined]

        @rumps.clicked("开始记忆")  # type: ignore[attr-defined]
        def toggle_memorize(self, item):  # type: ignore[override]
            if self._timer is None:
                self._timer = rumps.Timer(self._tick, _CAPTURE_INTERVAL)  # type: ignore[attr-defined]
                self._timer.start()
                item.state = True
                rumps.notification("溯记", "开始记忆", "聚焦窗口文本将每几秒抓取一次")  # type: ignore[attr-defined]
            else:
                self._timer.stop()
                self._timer = None
                item.state = False

        @rumps.clicked("立即抓取")  # type: ignore[attr-defined]
        def capture_now(self, _item):  # type: ignore[override]
            try:
                n = ingest_capture(self._capturer, self._extractor, self._store)
                msg = f"记下 {n} 条事实" if n else "未抓到可记文本"
                rumps.notification("溯记", "立即抓取", msg)  # type: ignore[attr-defined]
            except Exception as exc:
                rumps.notification("溯记", "抓取失败", str(exc))  # type: ignore[attr-defined]

        @rumps.clicked("校验失效")  # type: ignore[attr-defined]
        def recheck_now(self, _item):  # type: ignore[override]
            cascade = Cascade(self._store)
            results = cascade.recheck_all()
            mutated = sum(1 for r in results if r.mutated)
            stales = len(self._store.list_stale())
            msg = f"{mutated} 个来源变更，{stales} 条事实失效" if stales else "无失效事实"
            rumps.notification("溯记", "来源再校验", msg)  # type: ignore[attr-defined]

    return SuJiApp


def main() -> None:
    """Launch the menu-bar app (macOS + macos extra + Accessibility perm)."""
    import os

    capturer = MacAXCapturer()
    extractor = make_extractor(
        no_llm=os.environ.get("SUJI_NO_LLM") == "1"
    )
    store = SuJiStore()
    SuJiApp = _build_app(capturer, extractor, store)
    SuJiApp().run()


if __name__ == "__main__":
    main()
