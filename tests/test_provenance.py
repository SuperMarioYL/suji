"""Tests for provenance + extraction + the m1 ingest path.

Headless: no live Ollama, no macOS APIs. The local-Qwen extractor is
exercised against an injected fake OpenAI-compatible client (its
``chat.completions.create`` returns a canned JSON string), proving the
qwen3 wiring is correct without a running model. The full capture →
extract → store → ``ask`` path is driven by a fake capturer + the
rule-based fallback.
"""

from __future__ import annotations

import json

import pytest

from suji.capture import CapturedText, ingest_capture
from suji.llm import LocalQwenExtractor, RuleBasedExtractor, _parse_facts, make_extractor
from suji.provenance import (
    SourceKind,
    fingerprint,
    infer_kind,
    make_source_ref,
    source_diff,
)
from suji.store import SuJiStore


# --------------------------------------------------------------------------- #
# Fingerprint
# --------------------------------------------------------------------------- #
def test_fingerprint_is_deterministic():
    text = "Q3 营收 4.2 亿，同比增长 12%。"
    assert fingerprint(text) == fingerprint(text)


def test_fingerprint_detects_content_change():
    a = "Q3 营收 4.2 亿"
    b = "Q3 营收 4.3 亿"
    assert fingerprint(a) != fingerprint(b)


def test_fingerprint_ignores_incidental_whitespace():
    # An AX re-walk of the same article may reflow whitespace — doubled
    # spaces, trailing spaces per line, extra blank lines, CRLF. The
    # fingerprint must not false-flip on that, or cascade would mark
    # everything stale on every re-check. Paragraph structure is preserved.
    a = "Q3  萻收 4.2 亿。\r\n\r\n\r\n\r\n用户增长 12%。   "
    b = "Q3 萻收 4.2 亿。\n\n用户增长 12%。"
    assert fingerprint(a) == fingerprint(b)


def test_fingerprint_empty_text_is_stable():
    assert fingerprint("") == fingerprint("")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# SourceRef / kind inference
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "doc,kind",
    [
        ("https://mp.weixin.qq.com/s/abc123", SourceKind.URL),
        ("http://example.com/post", SourceKind.URL),
        ("/Users/me/Documents/report.docx", SourceKind.FILE),
        ("~/Documents/notes.wps", SourceKind.FILE),
        ("./relative/report.docx", SourceKind.FILE),
        ("dingtalk-doc-98765432", SourceKind.DOC_ID),
    ],
)
def test_infer_kind(doc, kind):
    assert infer_kind(doc) is kind


def test_make_source_ref_carries_kind_and_context():
    ref = make_source_ref(
        "com.tencent.xinWeChat",
        "https://mp.weixin.qq.com/s/abc",
        "Q3 财报",
        capture_context="ax",
    )
    assert ref.source_kind is SourceKind.URL
    assert ref.app_bundle == "com.tencent.xinWeChat"
    assert ref.capture_context == "ax"


# --------------------------------------------------------------------------- #
# Source diff
# --------------------------------------------------------------------------- #
def test_source_diff_empty_for_identical_content():
    assert source_diff("同一内容", "同一内容") == ""


def test_source_diff_marks_the_changed_line():
    old = "Q3 营收 4.2 亿\n用户增长 12%"
    new = "Q3 营收 4.3 亿\n用户增长 12%"
    diff = source_diff(old, new)
    assert diff  # non-empty
    assert "4.2 亿" in diff
    assert "4.3 亿" in diff
    assert diff.startswith("---")  # unified diff header


# --------------------------------------------------------------------------- #
# Rule-based extractor (the no-model fallback)
# --------------------------------------------------------------------------- #
def test_rule_based_extractor_keeps_fact_bearing_sentences():
    text = (
        "本公众号介绍公司财报。\n"  # not fact-bearing
        "Q3 营收 4.2 亿，同比增长 12%。\n"  # fact
        "用户规模达到 5000 万。\n"  # fact
        "欢迎关注我们。\n"  # not fact-bearing
    )
    facts = RuleBasedExtractor().extract(text)
    texts = [f.text for f in facts]
    assert any("4.2 亿" in t for t in texts)
    assert any("5000 万" in t for t in texts)
    assert not any("欢迎关注" in t for t in texts)


def test_rule_based_extractor_falls_back_to_whole_text_when_no_sentence_matches():
    # A single opaque line with no sentence terminator + no unit — still
    # returned as one fact so nothing is silently lost.
    facts = RuleBasedExtractor().extract("某条信息")
    assert len(facts) == 1
    assert facts[0].text == "某条信息"


# --------------------------------------------------------------------------- #
# Local Qwen extractor — mocked OpenAI-compatible client (no live Ollama)
# --------------------------------------------------------------------------- #
class _FakeOpenAIClient:
    """Minimal stand-in for openai.OpenAI: records the call, returns JSON."""

    def __init__(self, content: str):
        self._content = content
        self.last_kwargs: dict = {}

        client = self

        class _Completions:
            def create(self_inner, **kwargs):
                client.last_kwargs = kwargs

                class Msg:
                    pass

                class Choice:
                    pass

                msg = Msg()
                msg.content = client._content  # type: ignore[attr-defined]
                choice = Choice()
                choice.message = msg  # type: ignore[attr-defined]
                resp = object.__new__(_Resp)
                resp.choices = [choice]  # type: ignore[attr-defined]
                return resp

        class _Chat:
            completions = _Completions()

        class _Resp:
            pass

        self.chat = _Chat()


def test_local_qwen_extractor_parses_json_facts_via_mocked_client():
    payload = json.dumps({"facts": ["Q3 营收 4.2 亿", "用户增长 12%"]})
    fake = _FakeOpenAIClient(payload)
    extractor = LocalQwenExtractor(model="qwen3", client=fake)

    facts = extractor.extract("公众号正文（内容不重要，client 是 mock）")

    assert [f.text for f in facts] == ["Q3 营收 4.2 亿", "用户增长 12%"]
    # The extractor must target the local model + hand the source text over.
    assert fake.last_kwargs["model"] == "qwen3"
    assert "公众号正文" in fake.last_kwargs["messages"][1]["content"]
    assert fake.last_kwargs["temperature"] == 0.0


def test_local_qwen_extractor_strips_code_fences():
    fenced = '```json\n{"facts": ["林晚左手有疤"]}\n```'
    facts = _parse_facts(fenced)
    assert [f.text for f in facts] == ["林晚左手有疤"]


def test_local_qwen_extractor_tolerates_trailing_prose():
    payload = '前缀文字 {"facts": ["规格为 12V"]} 后缀文字'
    facts = _parse_facts(payload)
    assert [f.text for f in facts] == ["规格为 12V"]


def test_local_qwen_extractor_empty_text_returns_nothing():
    fake = _FakeOpenAIClient('{"facts": []}')
    assert LocalQwenExtractor(client=fake).extract("") == []


def test_make_extractor_honors_no_llm_env(monkeypatch):
    monkeypatch.setenv("SUJI_NO_LLM", "1")
    assert isinstance(make_extractor(), RuleBasedExtractor)
    monkeypatch.delenv("SUJI_NO_LLM", raising=False)
    ext = make_extractor(no_llm=False, client=_FakeOpenAIClient('{"facts":[]}'))
    assert isinstance(ext, LocalQwenExtractor)


# --------------------------------------------------------------------------- #
# m1 ingest path: fake capture → extract → store → ask (provenance trace)
# --------------------------------------------------------------------------- #
class _FakeCapturer:
    """A capturer stand-in: yields a fixed 公众号 article body."""

    def __init__(self, text: str, url: str = "https://mp.weixin.qq.com/s/abc"):
        self._text = text
        self._url = url

    def capture(self) -> CapturedText:
        return CapturedText(
            text=self._text,
            app_bundle="com.apple.Safari",
            doc_url_or_id=self._url,
            doc_title="Q3 财报速递",
            capture_context="ax",
        )


_ARTICLE = (
    "Q3 营收 4.2 亿，同比增长 12%。\n"
    "用户规模达到 5000 万。\n"
    "本季度毛利率为 38%。\n"
    "欢迎关注本公众号获取更多内容。\n"
)


def test_ingest_capture_stores_facts_with_source_fingerprint(tmp_path):
    store = SuJiStore(str(tmp_path / "suji.db"))
    try:
        n = ingest_capture(_FakeCapturer(_ARTICLE), RuleBasedExtractor(), store)
        assert n >= 3  # at least the three fact-bearing sentences

        # `ask` traces back to the source URL — the m1 star-earning moment.
        hits = store.search_facts("4.2")
        assert hits, "ask should find the Q3 营收 fact"
        fact = hits[0]
        source = store.get_source(fact.source_id)

        assert source.app_bundle == "com.apple.Safari"
        assert source.doc_url_or_id == "https://mp.weixin.qq.com/s/abc"
        assert source.doc_title == "Q3 财报速递"
        assert source.source_kind is SourceKind.URL

        # Every fact carries the capture-time fingerprint of the source.
        assert fact.source_fingerprint == fingerprint(_ARTICLE)
        assert fact.source_fingerprint == store.source_fingerprint(source.id)  # type: ignore[arg-type]
        assert fact.status == "fresh"
    finally:
        store.close()


def test_ingest_capture_no_text_stores_nothing(tmp_path):
    store = SuJiStore(str(tmp_path / "suji.db"))
    try:
        n = ingest_capture(_FakeCapturer(""), RuleBasedExtractor(), store)
        assert n == 0
        assert store.list_sources() == []
    finally:
        store.close()


def test_ask_is_case_insensitive_and_substring(tmp_path):
    store = SuJiStore(str(tmp_path / "suji.db"))
    try:
        ingest_capture(_FakeCapturer(_ARTICLE), RuleBasedExtractor(), store)
        # CJK + lowercase ascii substring both match.
        assert store.search_facts("5000")
        assert store.search_facts("毛利率")
        assert store.search_facts("不存在的数字") == []
    finally:
        store.close()
