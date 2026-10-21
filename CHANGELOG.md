# Changelog

SuJi (溯记) — local ambient screen-memory with per-fact source provenance and
cascade-invalidation when a source mutates. Versions follow the tagged releases;
each entry lists what actually changed in the code.

## 0.3.0 — 2026-10-22

Fixes to the capture → provenance → cascade path, plus a mechanical version guard.

- **Real-time file cascade works again.** The watchdog observer thread shares the
  SQLite store with the menu-bar capture loop, and the store's connection was
  created with the default `check_same_thread=True`, so the first edit of a watched
  local file raised `sqlite3.ProgrammingError` and the derived facts stayed fresh
  forever. The store now owns one serialized connection that both threads can use.
- **A re-check compares like with like.** A source's fingerprint used to be the hash
  of the captured *window text*, while the re-check re-read the source in its own
  representation (a page's raw HTML, a file's bytes). The two never matched, so the
  first `suji stale` of any URL source marked every fact stale and printed raw HTML
  as the "old vs new" diff. A source's content is now recorded from the source's own
  fetcher at capture time, so the comparison is over the same representation.
- **Re-captures no longer duplicate facts on whitespace jitter.** The dedup key
  compared the raw extracted text while the fingerprint compared the normalized text,
  so an AX re-walk with doubled or trailing spaces stored the same fact again and
  `suji ask` showed duplicates. De-duplication now compares canonical forms.
- **Existing databases migrate.** The new `sources.capture_fingerprint` column is
  added on open, so a database written by 0.2.0 or earlier keeps working.
- Added a test that fails if the `VERSION`, `pyproject.toml`, `suji.__version__`,
  `web/site.json` and `docs/demo-results.json` version surfaces ever disagree.

## 0.2.0 — 2026-09-15

- `upsert_source` returns the true source id on the `ON CONFLICT` path, so
  re-capturing a known document no longer fails with a FOREIGN KEY error.
- Re-captures skip facts already stored for the same (source, capture-time
  fingerprint, text), so the 5-second loop no longer floods `ask` with duplicates.
- `recheck_source` also sweeps fresh facts whose capture-time fingerprint no longer
  matches after a re-capture advanced the stored fingerprint, and the no-mutation
  path refreshes `last_checked_at`.
- The menu bar creates exactly one capture timer (`_tick` is no longer decorated
  with `@rumps.timer`, which `App.run` starts unconditionally).

## 0.1.0 — 2026-08-31

- First release: Accessibility-API text capture, local Qwen3 fact extraction,
  per-fact source provenance with capture-time fingerprints in SQLite, `ask` /
  `sources` / `stale` CLI, and cascade-invalidation on source mutation.
