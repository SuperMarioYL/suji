"""Store tests — the thread-safety and migration contracts of the SQLite store.

The store is shared between two threads at runtime: the menu-bar capture loop
(main thread) and the watchdog file-watcher callback (the observer thread, see
``suji.menu_bar.SuJiApp._start_file_watcher``). On the shipped code the
observer thread died with

    sqlite3.ProgrammingError: SQLite objects created in a thread can only be
    used in that same thread

so an edited local document never cascaded to stale. The tests below pin both
halves of the fix: the store is usable from the watcher thread, and a database
created by an older version gains the newer column instead of failing.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from suji.cascade import Cascade, start_file_watcher
from suji.capture import CapturedText, ingest_capture
from suji.llm import RuleBasedExtractor
from suji.provenance import fingerprint, make_source_ref
from suji.store import SuJiStore

_ARTICLE_V1 = "Q3 营收 4.2 亿，同比增长 12%。\n用户规模达到 5000 万。\n"
_ARTICLE_V2 = "Q3 营收 4.3 亿，同比增长 15%。\n用户规模达到 5000 万。\n"

# The v0.2.0 sources/facts schema, verbatim — used to prove the migration.
_LEGACY_SCHEMA = """
CREATE TABLE sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    app_bundle      TEXT NOT NULL,
    doc_url_or_id   TEXT NOT NULL UNIQUE,
    doc_title       TEXT NOT NULL DEFAULT '',
    source_kind     TEXT NOT NULL,
    capture_context TEXT NOT NULL DEFAULT '',
    fingerprint     TEXT NOT NULL DEFAULT '',
    last_content    TEXT NOT NULL DEFAULT '',
    last_checked_at TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);
CREATE TABLE facts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id          INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    text               TEXT NOT NULL,
    captured_at        TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'fresh',
    diff               TEXT NOT NULL DEFAULT '',
    note               TEXT NOT NULL DEFAULT ''
);
"""


# --------------------------------------------------------------------------- #
# Cross-thread use — the file-watcher branch
# --------------------------------------------------------------------------- #
def test_store_is_usable_from_the_file_watcher_thread(tmp_path):
    """The watcher callback shares the store with the capture loop.

    menu_bar's on_change runs on the watchdog observer thread and calls
    store.list_sources() + Cascade.recheck_source(); on the shipped code that
    raised sqlite3.ProgrammingError and left the fact fresh forever.
    """
    path = tmp_path / "article.txt"
    path.write_text(_ARTICLE_V1, encoding="utf-8")

    store = SuJiStore(str(tmp_path / "suji.db"))
    try:
        source_id = store.upsert_source(
            make_source_ref("com.kingsoft.writer", str(path), "article"), _ARTICLE_V1
        )
        store.add_fact(source_id, "Q3 营收 4.2 亿", fingerprint(_ARTICLE_V1))
        path.write_text(_ARTICLE_V2, encoding="utf-8")

        errors: list[BaseException] = []

        def on_change() -> None:
            try:
                cascade = Cascade(store)
                for src in store.list_sources():
                    if src.doc_url_or_id == str(path):
                        cascade.recheck_source(src.id)  # type: ignore[arg-type]
            except BaseException as exc:  # noqa: BLE001 - the defect is any raise
                errors.append(exc)

        worker = threading.Thread(target=on_change)
        worker.start()
        worker.join(timeout=30)

        assert errors == []
        assert store.list_facts(source_id=source_id)[0].status == "stale"
    finally:
        store.close()


def test_start_file_watcher_cascades_a_real_file_edit(tmp_path):
    """End-to-end: the real watchdog observer must cascade a local file edit.

    This is the branch mvp_plan.md's m2_ship_cascade acceptance names
    ("本地 WPS 文件被编辑（watchdog 监听）→ 该文件下事实 stale").
    """
    pytest.importorskip("watchdog")  # a declared core dep; absent in bare envs
    # watchdog reports the resolved path (macOS /var is a symlink to
    # /private/var), so the source key must be resolved too.
    path = tmp_path.resolve() / "report.txt"
    path.write_text(_ARTICLE_V1, encoding="utf-8")

    store = SuJiStore(str(tmp_path / "suji.db"))
    try:
        source_id = store.upsert_source(
            make_source_ref("com.kingsoft.writer", str(path), "report"), _ARTICLE_V1
        )
        store.add_fact(source_id, "Q3 营收 4.2 亿", fingerprint(_ARTICLE_V1))

        errors: list[BaseException] = []

        def on_change(changed: str) -> None:
            try:
                cascade = Cascade(store)
                for src in store.list_sources():
                    if src.doc_url_or_id == changed:
                        cascade.recheck_source(src.id)  # type: ignore[arg-type]
            except BaseException as exc:  # noqa: BLE001 - the defect is any raise
                errors.append(exc)

        observer = start_file_watcher([str(path)], on_change)
        try:
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                if store.list_facts(source_id=source_id)[0].status == "stale":
                    break
                path.write_text(_ARTICLE_V2, encoding="utf-8")
                time.sleep(0.25)
        finally:
            observer.stop()  # type: ignore[attr-defined]
            observer.join()  # type: ignore[attr-defined]

        assert errors == []
        assert store.list_facts(source_id=source_id)[0].status == "stale"
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# Migration — a database created before the capture-fingerprint column
# --------------------------------------------------------------------------- #
def test_store_migrates_a_database_created_by_an_older_version(tmp_path):
    """An existing ~/.suji/suji.db must gain the new column, keeping its rows.

    The schema is CREATE TABLE IF NOT EXISTS, so without a migration the new
    column would be missing on every database written by v0.2.0 or earlier.
    """
    db = tmp_path / "legacy.db"
    legacy = sqlite3.connect(str(db))
    legacy.executescript(_LEGACY_SCHEMA)
    legacy.execute(
        "INSERT INTO sources (app_bundle, doc_url_or_id, doc_title, source_kind,"
        " capture_context, fingerprint, last_content, last_checked_at, created_at)"
        " VALUES ('com.kingsoft.writer', '/tmp/legacy.txt', 'legacy', 'file',"
        " 'ax', 'fp1', 'old content', '2026-09-15T00:00:00+00:00',"
        " '2026-09-15T00:00:00+00:00')"
    )
    legacy.commit()
    legacy.close()

    store = SuJiStore(str(db))
    try:
        columns = {row["name"] for row in store._conn.execute("PRAGMA table_info(sources)")}
        assert "capture_fingerprint" in columns

        # The pre-existing row survived, and the new column round-trips.
        srcs = store.list_sources()
        assert [s.doc_url_or_id for s in srcs] == ["/tmp/legacy.txt"]
        assert store.source_capture_fingerprint(srcs[0].id) == ""  # type: ignore[arg-type]
        store.record_capture(srcs[0].id, "cap-fp")  # type: ignore[arg-type]
        assert store.source_capture_fingerprint(srcs[0].id) == "cap-fp"  # type: ignore[arg-type]
    finally:
        store.close()


def test_capture_fingerprint_is_separate_from_the_source_fingerprint(tmp_path):
    """The window-text fingerprint must not overwrite the source's content hash."""
    store = SuJiStore(str(tmp_path / "suji.db"))
    try:
        ref = make_source_ref("com.apple.Safari", "https://mp.weixin.qq.com/s/x", "x")
        source_id = store.upsert_source(ref, "source content")
        store.record_capture(source_id, "window-fingerprint")
        assert store.source_fingerprint(source_id) == fingerprint("source content")
        assert store.source_capture_fingerprint(source_id) == "window-fingerprint"
    finally:
        store.close()


def test_ingest_capture_records_both_fingerprints(tmp_path):
    """A capture records the window fingerprint AND the source's content."""
    path = tmp_path / "article.txt"
    path.write_text(_ARTICLE_V1, encoding="utf-8")

    class _FileCapturer:
        def capture(self) -> CapturedText:
            return CapturedText(
                text=path.read_text(encoding="utf-8"),
                app_bundle="com.kingsoft.writer",
                doc_url_or_id=str(path),
                doc_title="article",
                capture_context="ax",
            )

    store = SuJiStore(str(tmp_path / "suji.db"))
    try:
        assert ingest_capture(_FileCapturer(), RuleBasedExtractor(), store) >= 1
        src = store.list_sources()[0]
        # For a text file the source's content IS the captured text.
        assert store.source_fingerprint(src.id) == fingerprint(_ARTICLE_V1)  # type: ignore[arg-type]
        assert store.source_capture_fingerprint(src.id) == fingerprint(_ARTICLE_V1)  # type: ignore[arg-type]
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# The watcher must hand back the caller's key, not watchdog's resolved path
# --------------------------------------------------------------------------- #
def test_file_watcher_reports_the_stored_key_for_a_symlinked_path(tmp_path):
    """An edit behind a symlinked directory must cascade, not vanish.

    watchdog reports resolved paths (on macOS /tmp is /private/tmp and any
    symlinked folder is reported as its target), while the caller looks its
    sources up by the stored key verbatim; without the translation the exact
    match never fires and the fact stays fresh forever.
    """
    pytest.importorskip("watchdog")

    real = tmp_path / "real"
    real.mkdir()
    target = real / "report.txt"
    target.write_text(_ARTICLE_V1, encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    key = str(link / "report.txt")

    store = SuJiStore(str(tmp_path / "suji.db"))
    try:
        source_id = store.upsert_source(
            make_source_ref("com.kingsoft.writer", key, "report"), _ARTICLE_V1
        )
        store.add_fact(source_id, "Q3 营收 4.2 亿", fingerprint(_ARTICLE_V1))

        seen: list[str] = []

        def on_change(changed: str) -> None:
            seen.append(changed)
            for src in store.list_sources():
                if src.doc_url_or_id == changed:  # menu_bar.py's exact lookup
                    Cascade(store).recheck_source(src.id)  # type: ignore[arg-type]

        observer = start_file_watcher([key], on_change)
        try:
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                if store.list_facts(source_id=source_id)[0].status == "stale":
                    break
                target.write_text(_ARTICLE_V2, encoding="utf-8")
                time.sleep(0.25)
        finally:
            observer.stop()  # type: ignore[attr-defined]
            observer.join()  # type: ignore[attr-defined]

        assert seen and seen[0] == key
        assert store.list_facts(source_id=source_id)[0].status == "stale"
    finally:
        store.close()
