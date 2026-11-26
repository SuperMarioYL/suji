"""CLI reporting contracts — what ``stale`` and ``ask`` actually claim and show.

``suji stale`` is the read-back surface of the cascade, so its "nothing stale"
line must say whether anything was re-read at all, and the captured text it
prints must survive rendering unchanged.
"""

from __future__ import annotations

from typer.testing import CliRunner

from suji.cli import app
from suji.provenance import make_source_ref
from suji.store import SuJiStore

runner = CliRunner()


def _seed(db, doc_url_or_id: str, content: str, facts=()):
    store = SuJiStore(str(db))
    try:
        source_id = store.upsert_source(
            make_source_ref("com.example.app", doc_url_or_id, "source"), content
        )
        for text in facts:
            store.add_fact(source_id, text, "fp")
    finally:
        store.close()


def _run(monkeypatch, db, *args) -> str:
    monkeypatch.setenv("SUJI_DB", str(db))
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result.output


def test_stale_on_an_empty_store_does_not_claim_a_clean_check(tmp_path, monkeypatch):
    """Zero sources means zero comparisons, not a passing check."""
    output = _run(monkeypatch, tmp_path / "empty.db", "stale")

    assert "尚未记忆任何来源" in output
    assert "无失效事实" not in output


def test_stale_reports_when_no_source_could_be_verified(tmp_path, monkeypatch):
    """A cloud-doc source cannot be re-read: that is not "no stale facts"."""
    db = tmp_path / "cloud.db"
    _seed(db, "dingtalk-doc-98765432", "Q3 营收 4.2 亿")

    output = _run(monkeypatch, db, "stale")

    assert "无法读取" in output
    assert "未校验任何来源" in output
    assert "无失效事实" not in output


def test_stale_reports_the_verified_count(tmp_path, monkeypatch):
    """A readable, unchanged source is one verified source."""
    db = tmp_path / "file.db"
    source = tmp_path / "article.txt"
    source.write_text("Q3 营收 4.2 亿，同比增长 12%。\n", encoding="utf-8")
    _seed(db, str(source), source.read_text(encoding="utf-8"))

    output = _run(monkeypatch, db, "stale")

    assert "1 个来源已校验" in output


def test_ask_keeps_captured_text_verbatim(tmp_path, monkeypatch):
    """A captured fact is printed exactly as stored, bracketed tokens included."""
    db = tmp_path / "facts.db"
    _seed(db, "https://mp.weixin.qq.com/s/abc", "内容", facts=["[red] 12 个任务"])

    output = _run(monkeypatch, db, "ask", "12")

    assert "[red] 12 个任务" in output
