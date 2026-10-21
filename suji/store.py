"""SQLite store — the single source of truth for facts and sources.

Owns persistence of :class:`~suji.provenance.Fact` and
:class:`~suji.provenance.SourceRef`, plus the source's current fingerprint
(the "last known current" hash that cascade compares against each fact's
capture-time fingerprint).

Schema (two tables):

* ``sources`` — one row per addressed document. ``doc_url_or_id`` is unique;
  ``fingerprint`` is the *current* content hash of the source as its fetcher
  reads it, ``last_content`` the content it was computed from (kept so cascade
  can diff old vs new on mutation), and ``capture_fingerprint`` the hash of the
  captured window text at the last capture (the capture-side "is there anything
  new" marker — a different representation, so it is never mixed with
  ``fingerprint``).
* ``facts`` — one row per discrete captured fact. ``source_fingerprint`` is
  the content hash *at capture time*; ``status`` is only ever written by the
  cascade (fresh → stale), never by the user.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional

from .provenance import Fact, SourceKind, SourceRef, fingerprint, normalize_text


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_db_path() -> str:
    """Resolve the store path.

    ``SUJI_DB`` overrides (used by tests + the demo); otherwise
    ``~/.suji/suji.db``. The parent dir is created on demand.
    """
    env = os.environ.get("SUJI_DB")
    if env:
        return env
    base = os.path.expanduser("~/.suji")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "suji.db")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    app_bundle         TEXT NOT NULL,
    doc_url_or_id      TEXT NOT NULL UNIQUE,
    doc_title          TEXT NOT NULL DEFAULT '',
    source_kind        TEXT NOT NULL,
    capture_context    TEXT NOT NULL DEFAULT '',
    fingerprint        TEXT NOT NULL DEFAULT '',
    last_content       TEXT NOT NULL DEFAULT '',
    last_checked_at    TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL,
    capture_fingerprint TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS facts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id          INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    text               TEXT NOT NULL,
    captured_at        TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'fresh',
    diff               TEXT NOT NULL DEFAULT '',
    note               TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_facts_source ON facts(source_id);
CREATE INDEX IF NOT EXISTS idx_facts_status ON facts(status);
"""

# Columns added after a database may already exist. CREATE TABLE IF NOT EXISTS
# never alters an existing table, so each is added explicitly on open.
_ADDED_SOURCE_COLUMNS = {
    "capture_fingerprint": "TEXT NOT NULL DEFAULT ''",
}


class _SharedConnection:
    """The store's single sqlite connection, serialised across threads.

    The store is shared by the menu-bar capture loop (main thread) and the
    watchdog file-watcher callback (the observer thread — see
    ``suji.menu_bar.SuJiApp._start_file_watcher``). sqlite3 refuses a
    connection created on another thread unless ``check_same_thread=False``,
    and one connection's statements must not interleave, so every call is
    taken under one re-entrant lock. Keeping the lock here rather than at each
    call site leaves the rest of the store unchanged.
    """

    def __init__(self, conn: sqlite3.Connection, lock: "threading.RLock"):
        self._conn = conn
        self._lock = lock

    def execute(self, sql: str, params=()):
        with self._lock:
            return self._conn.execute(sql, params)

    def executescript(self, script: str):
        with self._lock:
            return self._conn.executescript(script)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "_SharedConnection":
        return self

    def __exit__(self, *exc) -> bool:
        with self._lock:
            self._conn.commit()
        return False



class SuJiStore:
    """The fact + source store.

    A thin, focused wrapper around SQLite. Mutations are explicit; ``status``
    is only ever flipped by :meth:`mark_fact_stale` (called from the cascade),
    honoring the "status is written by cascade, not the user" invariant.
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path or default_db_path()
        # isolation_level=None → autocommit; we use explicit transactions
        # for the cascade's multi-row stale update.
        raw = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        raw.row_factory = sqlite3.Row
        self._conn = _SharedConnection(raw, threading.RLock())
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created.

        A user's ``~/.suji/suji.db`` was created by an earlier version and the
        schema above is CREATE TABLE IF NOT EXISTS, so it keeps its old columns;
        new ones are added here instead of failing every later query.
        """
        existing = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(sources)")
        }
        for column, spec in _ADDED_SOURCE_COLUMNS.items():
            if column not in existing:
                self._conn.execute(
                    f"ALTER TABLE sources ADD COLUMN {column} {spec}"
                )

    # ----- lifecycle -----
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SuJiStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ----- sources -----
    def upsert_source(self, ref: SourceRef, content: str) -> int:
        """Create the source if new, else refresh its metadata.

        ``content`` is the SOURCE's own content — what the source's fetcher
        would read back (a 公众号 page's fetched bytes, a local file's text) —
        not the captured window text, which is a different representation of
        the same document and is recorded separately by :meth:`record_capture`.
        The fingerprint + content are stored as the source's current state.
        Returns the source id.

        The id is resolved by ``doc_url_or_id`` after the upsert: on the
        ON CONFLICT (update) path SQLite does not refresh the connection's
        last-insert rowid, so ``cur.lastrowid`` would return the rowid of
        whatever row was last INSERTed (a fact row from the previous
        capture) — re-capturing a known document would then attach facts
        to a nonexistent or wrong source id.
        """
        fp = fingerprint(content)
        now = _now_iso()
        with self._conn:
            self._conn.execute(
                """INSERT INTO sources
                       (app_bundle, doc_url_or_id, doc_title, source_kind,
                        capture_context, fingerprint, last_content,
                        last_checked_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(doc_url_or_id) DO UPDATE SET
                       app_bundle=excluded.app_bundle,
                       doc_title=excluded.doc_title,
                       source_kind=excluded.source_kind,
                       capture_context=excluded.capture_context,
                       fingerprint=excluded.fingerprint,
                       last_content=excluded.last_content,
                       last_checked_at=excluded.last_checked_at""",
                (ref.app_bundle, ref.doc_url_or_id, ref.doc_title,
                 ref.source_kind.value, ref.capture_context, fp, content, now, now),
            )
            row = self._conn.execute(
                "SELECT id FROM sources WHERE doc_url_or_id = ?",
                (ref.doc_url_or_id,),
            ).fetchone()
            return int(row["id"])

    def add_source_ref(self, ref: SourceRef) -> int:
        """Insert a source ref with no content yet (fingerprint empty).

        Used when seeding a source whose content is captured separately.
        """
        now = _now_iso()
        with self._conn:
            cur = self._conn.execute(
                """INSERT INTO sources
                       (app_bundle, doc_url_or_id, doc_title, source_kind,
                        capture_context, fingerprint, last_content,
                        last_checked_at, created_at)
                   VALUES (?, ?, ?, ?, ?, '', '', '', ?)
                   ON CONFLICT(doc_url_or_id) DO NOTHING""",
                (ref.app_bundle, ref.doc_url_or_id, ref.doc_title,
                 ref.source_kind.value, ref.capture_context, now),
            )
            row = self._conn.execute(
                "SELECT id FROM sources WHERE doc_url_or_id = ?",
                (ref.doc_url_or_id,),
            ).fetchone()
            return int(row["id"])

    def get_source(self, source_id: int) -> Optional[SourceRef]:
        row = self._conn.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        return _row_to_source(row) if row else None

    def get_source_by_doc(self, doc_url_or_id: str) -> Optional[SourceRef]:
        row = self._conn.execute(
            "SELECT * FROM sources WHERE doc_url_or_id = ?",
            (doc_url_or_id,),
        ).fetchone()
        return _row_to_source(row) if row else None

    def list_sources(self) -> list[SourceRef]:
        rows = self._conn.execute(
            "SELECT * FROM sources ORDER BY created_at DESC"
        ).fetchall()
        return [_row_to_source(r) for r in rows]

    def update_source_fingerprint(
        self, source_id: int, new_fingerprint: str, new_content: str
    ) -> None:
        """Set the source's current fingerprint + content (used by cascade).

        Does not touch facts; the cascade marks facts stale separately so
        the two mutations are independent and testable.
        """
        with self._conn:
            self._conn.execute(
                """UPDATE sources
                   SET fingerprint = ?, last_content = ?, last_checked_at = ?
                   WHERE id = ?""",
                (new_fingerprint, new_content, _now_iso(), source_id),
            )

    def source_fingerprint(self, source_id: int) -> str:
        row = self._conn.execute(
            "SELECT fingerprint FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        return row["fingerprint"] if row else ""

    def source_last_content(self, source_id: int) -> str:
        row = self._conn.execute(
            "SELECT last_content FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        return row["last_content"] if row else ""

    def source_capture_fingerprint(self, source_id: int) -> str:
        """Fingerprint of the window text read at the last capture.

        Kept apart from :meth:`source_fingerprint` (the source's own content):
        the two are different representations of the same document, so the
        capture loop compares the window text against this to decide whether
        there is anything new to ingest.
        """
        row = self._conn.execute(
            "SELECT capture_fingerprint FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        return row["capture_fingerprint"] if row else ""

    def record_capture(self, source_id: int, capture_fingerprint: str) -> None:
        """Record the fingerprint of the window text this capture read."""
        with self._conn:
            self._conn.execute(
                "UPDATE sources SET capture_fingerprint = ? WHERE id = ?",
                (capture_fingerprint, source_id),
            )

    def mark_source_checked(self, source_id: int) -> None:
        """Refresh ``last_checked_at`` after a successful re-check.

        Called by the cascade on the no-mutation path so the ``suji
        sources`` "最近校验" column reflects rechecks, not just captures.
        """
        with self._conn:
            self._conn.execute(
                "UPDATE sources SET last_checked_at = ? WHERE id = ?",
                (_now_iso(), source_id),
            )

    # ----- facts -----
    def add_fact(
        self,
        source_id: int,
        text: str,
        source_fingerprint: str,
        captured_at: Optional[str] = None,
        note: str = "",
    ) -> int:
        """Persist one extracted fact bound to a source + its capture-time fp."""
        with self._conn:
            cur = self._conn.execute(
                """INSERT INTO facts
                       (source_id, text, captured_at, source_fingerprint,
                        status, diff, note)
                   VALUES (?, ?, ?, ?, 'fresh', '', ?)""",
                (source_id, text, captured_at or _now_iso(),
                 source_fingerprint, note),
            )
            return int(cur.lastrowid)

    def has_fact(
        self, source_id: int, text: str, source_fingerprint: str
    ) -> bool:
        """True when this exact fact is already stored.

        The dedup key for the capture loop: the periodic re-capture of an
        unchanged document re-extracts the same facts with the same
        capture-time fingerprint, and re-adding them would flood ``ask``
        with duplicates. A fact extracted from *changed* content carries a
        different fingerprint and is never considered a duplicate.

        Comparison is on the canonical form
        (``provenance.normalize_text``), not the raw string: the fingerprint
        already treats an AX re-walk's incidental whitespace jitter as the same
        document, so the dedup key must agree with it. The stored text keeps the
        captured wording.
        """
        key = normalize_text(text)
        rows = self._conn.execute(
            "SELECT text FROM facts "
            "WHERE source_id = ? AND source_fingerprint = ?",
            (source_id, source_fingerprint),
        ).fetchall()
        return any(normalize_text(row["text"]) == key for row in rows)

    def get_fact(self, fact_id: int) -> Optional[Fact]:
        row = self._conn.execute(
            "SELECT * FROM facts WHERE id = ?", (fact_id,)
        ).fetchone()
        return _row_to_fact(row) if row else None

    def list_facts(
        self, source_id: Optional[int] = None, status: Optional[str] = None
    ) -> list[Fact]:
        sql = "SELECT * FROM facts"
        clauses: list[str] = []
        params: list = []
        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(source_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY captured_at DESC"
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_fact(r) for r in rows]

    def search_facts(self, query: str) -> list[Fact]:
        """Substring search over fact text.

        v0.1 deliberately uses no embeddings / vector store (out of scope);
        a case-insensitive substring match is the honest, deterministic
        retrieval for the ``ask`` verb.
        """
        q = query.strip()
        if not q:
            return []
        rows = self._conn.execute(
            "SELECT * FROM facts WHERE text LIKE ? ESCAPE '\\' "
            "ORDER BY captured_at DESC",
            (f"%{_escape_like(q)}%",),
        ).fetchall()
        return [_row_to_fact(r) for r in rows]

    def mark_fact_stale(self, fact_id: int, diff: str = "") -> None:
        """Flip a fact to stale and record the source diff.

        Cascade-only entry point — this is the only place ``status`` is
        written to ``stale``.
        """
        with self._conn:
            self._conn.execute(
                "UPDATE facts SET status = 'stale', diff = ? WHERE id = ?",
                (diff, fact_id),
            )

    def list_stale(self) -> list[Fact]:
        return self.list_facts(status="stale")

    def count_facts(self, source_id: int) -> tuple[int, int]:
        """Return (fresh_count, stale_count) for a source."""
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM facts WHERE source_id = ? "
            "GROUP BY status",
            (source_id,),
        ).fetchall()
        counts = {r["status"]: r["n"] for r in rows}
        return (counts.get("fresh", 0), counts.get("stale", 0))


def _row_to_source(row: sqlite3.Row) -> SourceRef:
    return SourceRef(
        app_bundle=row["app_bundle"],
        doc_url_or_id=row["doc_url_or_id"],
        doc_title=row["doc_title"],
        source_kind=SourceKind(row["source_kind"]),
        capture_context=row["capture_context"],
        id=row["id"],
    )


def _row_to_fact(row: sqlite3.Row) -> Fact:
    return Fact(
        id=row["id"],
        text=row["text"],
        source_id=row["source_id"],
        captured_at=row["captured_at"],
        source_fingerprint=row["source_fingerprint"],
        status=row["status"],
        diff=row["diff"],
        note=row["note"],
    )


def _escape_like(s: str) -> str:
    # SQLite LIKE escapes: \ is the ESCAPE char we declared.
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
