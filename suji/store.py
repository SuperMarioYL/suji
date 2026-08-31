"""SQLite store — the single source of truth for facts and sources.

Owns persistence of :class:`~suji.provenance.Fact` and
:class:`~suji.provenance.SourceRef`, plus the source's current fingerprint
(the "last known current" hash that cascade compares against each fact's
capture-time fingerprint).

Schema (two tables):

* ``sources`` — one row per addressed document. ``doc_url_or_id`` is unique;
  ``fingerprint`` is the *current* content hash, ``last_content`` the content
  it was computed from (kept so cascade can diff old vs new on mutation).
* ``facts`` — one row per discrete captured fact. ``source_fingerprint`` is
  the content hash *at capture time*; ``status`` is only ever written by the
  cascade (fresh → stale), never by the user.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from .provenance import Fact, SourceKind, SourceRef, fingerprint


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

CREATE TABLE IF NOT EXISTS facts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id          INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    text               TEXT NOT NULL,
    captured_at       TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'fresh',
    diff               TEXT NOT NULL DEFAULT '',
    note               TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_facts_source ON facts(source_id);
CREATE INDEX IF NOT EXISTS idx_facts_status ON facts(status);
"""


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
        self._conn = sqlite3.connect(self.path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)

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

        The capture pipeline calls this with the just-captured document
        content; the fingerprint + content are stored as the source's
        current state. Returns the source id.
        """
        fp = fingerprint(content)
        now = _now_iso()
        with self._conn:
            cur = self._conn.execute(
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
            return int(cur.lastrowid)

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
