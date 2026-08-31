"""Tests for the cascade — m2's source-mutation invalidation.

Headless: no live Ollama, no macOS APIs. The URL-source re-fetch is faked
(a controllable fetcher returns the "current" content); the file-source
path uses a real temp file mutated between re-checks, exercising the
genuine :class:`~suji.cascade.FileFetcher`. The end-to-end test mirrors
the demo GIF flow: capture → ask → edit source → ``stale``.
"""

from __future__ import annotations

import os

import pytest

from suji.cascade import Cascade, DocIdFetcher, FileFetcher, make_fetcher
from suji.capture import CapturedText, ingest_capture
from suji.llm import RuleBasedExtractor
from suji.provenance import SourceKind, fingerprint, make_source_ref, source_diff
from suji.store import SuJiStore


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class _FakeFetcher:
    """Returns a fixed "current" content for any source (URL re-fetch stand-in)."""

    def __init__(self, content: str):
        self._content = content

    def fetch(self, source):
        return self._content


class _SequenceFetcher:
    """Returns successive contents to model an article being edited mid-test."""

    def __init__(self, contents):
        self._contents = list(contents)
        self._i = 0

    def fetch(self, source):
        if self._i >= len(self._contents):
            return self._contents[-1]
        out = self._contents[self._i]
        self._i += 1
        return out


class _FileCapturer:
    """A capturer that reads a local file's text (FILE source)."""

    def __init__(self, path: str, app_bundle: str = "com.kingsoft.writer"):
        self._path = path
        self._app = app_bundle

    def capture(self) -> CapturedText:
        with open(self._path, "r", encoding="utf-8") as fh:
            text = fh.read()
        return CapturedText(
            text=text,
            app_bundle=self._app,
            doc_url_or_id=self._path,
            doc_title=os.path.basename(self._path),
            capture_context="ax",
        )


_ARTICLE_V1 = (
    "Q3 营收 4.2 亿，同比增长 12%。\n"
    "用户规模达到 5000 万。\n"
)
_ARTICLE_V2 = (
    "Q3 营收 4.3 亿，同比增长 15%。\n"  # number revised
    "用户规模达到 5000 万。\n"
)


# --------------------------------------------------------------------------- #
# No mutation → facts stay fresh
# --------------------------------------------------------------------------- #
def test_recheck_no_mutation_keeps_facts_fresh(tmp_path):
    store = SuJiStore(str(tmp_path / "suji.db"))
    try:
        # Seed a URL source + a fact captured from C1.
        ref = make_source_ref(
            "com.apple.Safari", "https://mp.weixin.qq.com/s/abc", "Q3 财报"
        )
        source_id = store.upsert_source(ref, _ARTICLE_V1)
        store.add_fact(source_id, "Q3 营收 4.2 亿", fingerprint(_ARTICLE_V1))

        cascade = Cascade(store, fetcher_for=lambda _s: _FakeFetcher(_ARTICLE_V1))
        result = cascade.recheck_source(source_id)

        assert not result.mutated
        assert result.stale_count == 0
        assert store.list_facts(source_id=source_id)[0].status == "fresh"
        assert store.list_stale() == []
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# URL mutation → cascade-stale + diff
# --------------------------------------------------------------------------- #
def test_recheck_url_mutation_marks_facts_stale_with_diff(tmp_path):
    store = SuJiStore(str(tmp_path / "suji.db"))
    try:
        ref = make_source_ref(
            "com.apple.Safari", "https://mp.weixin.qq.com/s/abc", "Q3 财报"
        )
        source_id = store.upsert_source(ref, _ARTICLE_V1)
        store.add_fact(source_id, "Q3 营收 4.2 亿", fingerprint(_ARTICLE_V1))
        store.add_fact(source_id, "用户规模 5000 万", fingerprint(_ARTICLE_V1))

        # The article was edited externally → re-fetch now yields V2.
        cascade = Cascade(store, fetcher_for=lambda _s: _FakeFetcher(_ARTICLE_V2))
        result = cascade.recheck_source(source_id)

        assert result.mutated is True
        assert result.stale_count == 2
        assert result.new_fingerprint == fingerprint(_ARTICLE_V2)
        # Both facts captured from V1 are now stale, carrying the diff.
        stales = store.list_stale()
        assert len(stales) == 2
        assert all(s.diff for s in stales)
        assert "4.2 亿" in stales[0].diff
        assert "4.3 亿" in stales[0].diff
        # The source's current fingerprint advanced to V2.
        assert store.source_fingerprint(source_id) == fingerprint(_ARTICLE_V2)
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# Idempotency: a second re-check of the now-stable content adds nothing
# --------------------------------------------------------------------------- #
def test_recheck_is_idempotent_after_mutation(tmp_path):
    store = SuJiStore(str(tmp_path / "suji.db"))
    try:
        ref = make_source_ref("com.apple.Safari", "https://mp.weixin.qq.com/s/x", "x")
        source_id = store.upsert_source(ref, _ARTICLE_V1)
        store.add_fact(source_id, "Q3 营收 4.2 亿", fingerprint(_ARTICLE_V1))

        seq = _SequenceFetcher([_ARTICLE_V2, _ARTICLE_V2])  # edited, then stable
        cascade = Cascade(store, fetcher_for=lambda _s: seq)

        first = cascade.recheck_source(source_id)
        assert first.mutated and first.stale_count == 1

        second = cascade.recheck_source(source_id)
        assert not second.mutated
        assert second.stale_count == 0  # already stale, no double-mark
        assert len(store.list_stale()) == 1
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# File source: real FileFetcher reads a mutated temp file (WPS-style)
# --------------------------------------------------------------------------- #
def test_recheck_file_source_marks_stale_on_real_file_edit(tmp_path):
    path = tmp_path / "report.docx"
    path.write_text(_ARTICLE_V1, encoding="utf-8")

    store = SuJiStore(str(tmp_path / "suji.db"))
    try:
        ref = make_source_ref("com.kingsoft.writer", str(path), "report")
        assert ref.source_kind is SourceKind.FILE
        source_id = store.upsert_source(ref, _ARTICLE_V1)
        store.add_fact(source_id, "Q3 营收 4.2 亿", fingerprint(_ARTICLE_V1))

        # The genuine FileFetcher re-reads the file from disk.
        cascade = Cascade(store)  # default fetcher_for → make_fetcher → FileFetcher
        # No edit yet → fresh.
        assert not cascade.recheck_source(source_id).mutated

        # Now the WPS file is edited externally.
        path.write_text(_ARTICLE_V2, encoding="utf-8")
        result = cascade.recheck_source(source_id)

        assert result.mutated is True
        assert result.stale_count == 1
        stale = store.list_stale()[0]
        assert stale.status == "stale"
        assert "4.2 亿" in stale.diff and "4.3 亿" in stale.diff
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# Doc-id source (钉钉 cloud doc) → unverifiable, facts untouched
# --------------------------------------------------------------------------- #
def test_recheck_doc_id_source_is_unverifiable_and_leaves_facts_fresh(tmp_path):
    store = SuJiStore(str(tmp_path / "suji.db"))
    try:
        ref = make_source_ref(
            "com.alibaba.DingTalk", "dingtalk-doc-98765432", "项目纪要"
        )
        assert ref.source_kind is SourceKind.DOC_ID
        source_id = store.upsert_source(ref, _ARTICLE_V1)
        store.add_fact(source_id, "Q3 营收 4.2 亿", fingerprint(_ARTICLE_V1))

        cascade = Cascade(store)  # DocIdFetcher for DOC_ID sources
        result = cascade.recheck_source(source_id)

        assert result.error == "unverifiable"
        assert not result.mutated
        assert store.list_facts(source_id=source_id)[0].status == "fresh"
    finally:
        store.close()


def test_make_fetcher_dispatch():
    assert make_fetcher(make_source_ref("a", "https://x.com/y", "t")).__class__.__name__ == "UrlFetcher"
    assert make_fetcher(make_source_ref("a", "/tmp/f.docx", "t")).__class__.__name__ == "FileFetcher"
    assert make_fetcher(make_source_ref("a", "doc-1", "t")).__class__.__name__ == "DocIdFetcher"


def test_doc_id_fetcher_returns_none():
    assert DocIdFetcher().fetch(make_source_ref("a", "doc-1", "t")) is None


# --------------------------------------------------------------------------- #
# recheck_all sweeps every source
# --------------------------------------------------------------------------- #
def test_recheck_all_rechecks_every_source(tmp_path):
    store = SuJiStore(str(tmp_path / "suji.db"))
    try:
        s1 = store.upsert_source(
            make_source_ref("com.apple.Safari", "https://mp.weixin.qq.com/s/a", "a"),
            _ARTICLE_V1,
        )
        s2 = store.upsert_source(
            make_source_ref("com.apple.Safari", "https://mp.weixin.qq.com/s/b", "b"),
            _ARTICLE_V1,
        )
        store.add_fact(s1, "Q3 营收 4.2 亿", fingerprint(_ARTICLE_V1))
        store.add_fact(s2, "用户规模 5000 万", fingerprint(_ARTICLE_V1))

        cascade = Cascade(store, fetcher_for=lambda _s: _FakeFetcher(_ARTICLE_V2))
        results = cascade.recheck_all()

        assert len(results) == 2
        assert all(r.mutated for r in results)
        assert sum(r.stale_count for r in results) == 2
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# End-to-end m2 demo flow: capture → ask → edit source → stale
# --------------------------------------------------------------------------- #
def test_end_to_end_capture_ask_edit_stale(tmp_path):
    path = tmp_path / "article.wps"
    path.write_text(_ARTICLE_V1, encoding="utf-8")

    store = SuJiStore(str(tmp_path / "suji.db"))
    try:
        # 1. capture the article (m1) — file capturer + rule-based extractor
        n = ingest_capture(_FileCapturer(str(path)), RuleBasedExtractor(), store)
        assert n >= 1

        # 2. ask — provenance traces back to the file path
        hits = store.search_facts("4.2")
        assert hits and hits[0].status == "fresh"
        source = store.get_source(hits[0].source_id)
        assert source.doc_url_or_id == str(path)

        # 3. the source is edited externally
        path.write_text(_ARTICLE_V2, encoding="utf-8")

        # 4. stale — cascade re-reads the file, marks the prior fact stale
        cascade = Cascade(store)
        result = cascade.recheck_source(hits[0].source_id)
        assert result.mutated

        stales = store.list_stale()
        assert stales
        assert all("4.3 亿" in s.diff for s in stales)

        # And `ask` now reports the fact as stale (status surfaced to user).
        again = store.search_facts("4.2")[0]
        assert again.status == "stale"
    finally:
        store.close()
