# Changelog

SuJi (溯记) — local ambient screen-memory with per-fact source provenance and
cascade-invalidation when a source mutates. Versions follow the tagged releases;
each entry lists what actually changed in the code.

## 0.4.0 — 2026-11-27

Fixes to the cascade's error paths and to what the read-back surfaces report.

- **An unreadable source no longer poisons the fingerprint.** A capture taken while the
  source could not be fetched (offline, unreadable) used to store the captured *window
  text* as the source's content, even though a re-check reads the source's own
  representation. The first successful re-check of an unchanged page then reported the
  whole document as rewritten and marked every fact stale. The capture now keeps the
  revision already recorded for that source; only a source with no revision at all falls
  back to the window text.
- **A blank read is "unverifiable", not a mutation.** A 0-byte file (an editor's
  truncate-then-write save, with the watchdog firing inside that window) or an empty
  response body used to be fingerprinted as content: the source's remembered revision was
  overwritten with the empty string, every fact went stale and the old/new diff base was
  lost. `cascade.read_source_content` now defines "could not read" (absent or blank) once
  for both the capture path and the re-check.
- **`suji stale` says what it verified.** With no sources, or with only sources that cannot
  be read (cloud-doc ids, offline), it printed the green "无失效事实。" line at exit 0 —
  a pass manufactured from zero comparisons. It now reports the verified count, states
  when no source could be read, and `summarize_recheck` (also used by the menu bar's
  校验失效 notification) owns that classification.
- **Captured text survives rendering.** Fact text, source ids and diffs are escaped before
  rich renders them, so a fact containing a bracketed style-like token (e.g. `[red]`) is no
  longer swallowed by the table.
- **The file watcher hands back the stored source key.** watchdog reports resolved paths
  (macOS `/tmp` is `/private/tmp`, symlinked folders report their target), while the
  menu-bar callback matched the stored key verbatim, so an edit behind a symlink was
  dropped silently and the fact stayed fresh.
- The re-fetch User-Agent is derived from `suji.__version__` (it had been frozen at
  `SuJi/0.1`) and is now part of the version lockstep test.

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
