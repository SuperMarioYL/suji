"""Menu-bar app construction tests (macOS + rumps only).

Headless hosts (the ubuntu CI) cannot import rumps — AppKit is macOS-only —
so this module skips there. On a Mac it locks in the v0.2.0 timer fix: the
rumps source pins that the ``rumps.timer`` decorator appends a Timer to the
module-level ``*timers`` registry and ``App.run()`` starts every registered
timer unconditionally. v0.1.0 decorated ``_tick`` with ``@rumps.timer`` AND
created a second manual ``rumps.Timer`` on 开始记忆, so captures fired at
double rate while memorizing was on. The fix keeps ``_tick`` a plain method
started only by the toggle.
"""

from __future__ import annotations

import pytest

rumps = pytest.importorskip("rumps")  # noqa: F841 — skips headless hosts

from suji.capture import CapturedText
from suji.llm import RuleBasedExtractor
from suji.menu_bar import _build_app
from suji.store import SuJiStore


class _FakeCapturer:
    """A capturer stand-in; the app class only stores it."""

    def capture(self) -> CapturedText:
        return CapturedText(
            text="Q3 营收 4.2 亿。",
            app_bundle="test.app",
            doc_url_or_id="/tmp/suji-test.txt",
            doc_title="test",
            capture_context="test",
        )


def test_build_app_registers_no_auto_start_timer(tmp_path):
    """Building the app class must not register an auto-start timer.

    On v0.1.0 the ``@rumps.timer(_CAPTURE_INTERVAL)`` decorator on ``_tick``
    grew this registry on every import, and ``App.run()`` then started that
    timer alongside the manual one — double-rate captures.
    """
    timers_before = len(getattr(rumps.timer, "*timers", []))
    store = SuJiStore(str(tmp_path / "suji.db"))
    try:
        app_cls = _build_app(_FakeCapturer(), RuleBasedExtractor(), store)
        assert len(getattr(rumps.timer, "*timers", [])) == timers_before
        # _tick stays a plain, callable method (started only by the toggle).
        assert callable(app_cls._tick)
    finally:
        store.close()
