"""Fact extraction via a local Qwen3 (Ollama, OpenAI-compatible).

The capture pipeline reads focused-window text, then asks a local model to
extract discrete, self-contained facts ("Q3 营收 4.2 亿", "林晚左手有疤").
The model runs on the user's own Ollama — no screen text ever leaves the
device.

Two extractors share the :class:`FactExtractor` protocol:

* :class:`LocalQwenExtractor` — the real m1 path. Talks to a local Ollama
  exposing an OpenAI-compatible endpoint (``/v1``) with model ``qwen3``.
  The ``openai`` client is dependency-injected so tests can mock it without
  a live server.
* :class:`RuleBasedExtractor` — a deterministic fallback that keeps
  sentences carrying numbers / units / definitions. Used when no model is
  reachable (the test suite, the CI-rendered demo, and the "no Ollama yet"
  install state) so the pipeline is exercisable without a live Qwen3.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from .provenance import ExtractedFact

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "qwen3"
DEFAULT_API_KEY = "ollama"  # Ollama ignores the key; any non-empty value works

_EXTRACT_PROMPT = """你是一个严谨的事实抽取器。从下面的来源文本中，抽取离散、自足的事实陈述（数字、规格、命名事实、可引用判断）。每条事实应当能脱离上下文被理解。

只返回 JSON 对象：{"facts": ["<事实1>", "<事实2>", ...]}。不要解释、不要前后的散文、不要 markdown 代码围栏。

来源文本：
"""


class FactExtractor(Protocol):
    """Anything that turns source text into a list of discrete facts."""

    def extract(self, text: str) -> list[ExtractedFact]: ...


@dataclass
class LocalQwenExtractor:
    """Extract facts from a local Qwen3 via an OpenAI-compatible client.

    The ``client`` is injected (or lazily constructed from ``base_url``);
    tests pass a fake client whose ``chat.completions.create`` returns an
    object with ``choices[0].message.content`` holding the JSON string.
    """

    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    api_key: str = DEFAULT_API_KEY
    client: Optional[Any] = None  # openai.OpenAI-compatible; injected in tests

    def _get_client(self) -> Any:
        if self.client is not None:
            return self.client
        # Imported lazily so the test suite (which injects a fake) and the
        # headless CLI never hard-require the openai package at import time.
        from openai import OpenAI  # type: ignore

        self.client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        return self.client

    def extract(self, text: str) -> list[ExtractedFact]:
        if not text or not text.strip():
            return []
        client = self._get_client()
        resp = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _EXTRACT_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0.0,
        )
        content = resp.choices[0].message.content or ""
        return _parse_facts(content)


def _parse_facts(content: str) -> list[ExtractedFact]:
    """Parse the model's JSON {"facts": [...]} response defensively.

    Models occasionally wrap JSON in ```json fences or trailing prose;
    we locate the first {...} block and parse that.
    """
    content = content.strip()
    if not content:
        return []
    # Strip markdown code fences if present.
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, re.DOTALL)
    payload = fence.group(1) if fence else content
    if "{" not in payload:
        # Fall back to line-by-line if the model ignored the schema.
        lines = [ln.strip("- •* \t") for ln in payload.splitlines() if ln.strip()]
        return [ExtractedFact(text=ln) for ln in lines if ln]
    start = payload.index("{")
    end = payload.rindex("}")
    try:
        data = json.loads(payload[start : end + 1])
    except json.JSONDecodeError:
        return []
    raw = data.get("facts") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[ExtractedFact] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(ExtractedFact(text=item.strip()))
        elif isinstance(item, dict) and str(item.get("text", "")).strip():
            out.append(ExtractedFact(text=str(item["text"]).strip()))
    return out


# Heuristic "factiness" markers for the rule-based fallback. A sentence
# that carries a number / unit / definition is treated as a discrete fact.
_FACT_PATTERN = re.compile(
    r"\d[\d,\.]*\s*(亿|万|千|百|%|％|元|倍|个|人|台|款|年|月|日|小时|分钟|秒)"
    r"|[\dA-Z]{2,}\s*[:：]"
    r"|“.+?”"
    r"|是|为|等于|共计|达到|增长|下降|营收|利润|用户|规模|占比",
)


@dataclass
class RuleBasedExtractor:
    """Deterministic fact extractor — the no-model fallback.

    Splits on sentence terminators (CJK + ASCII) and keeps sentences that
    look fact-bearing. Good enough to exercise capture → store → ask →
    cascade deterministically in tests and the CI demo without Ollama; the
    real extraction quality comes from :class:`LocalQwenExtractor`.
    """

    def extract(self, text: str) -> list[ExtractedFact]:
        if not text or not text.strip():
            return []
        # Split on CJK + ASCII sentence enders, keeping content.
        parts = re.split(r"(?<=[。！？!?；;])", text)
        out: list[ExtractedFact] = []
        seen: set[str] = set()
        for p in parts:
            p = p.strip().strip("·•*- \t").strip()
            if not p or len(p) < 3:
                continue
            if not _FACT_PATTERN.search(p):
                continue
            key = p.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(ExtractedFact(text=p))
        # If nothing matched the heuristic, the whole text is one fact.
        if not out and text.strip():
            out.append(ExtractedFact(text=text.strip()))
        return out


def make_extractor(
    *,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    no_llm: Optional[bool] = None,
    client: Optional[Any] = None,
) -> FactExtractor:
    """Pick the extractor from config / env.

    ``SUJI_NO_LLM=1`` (or ``no_llm=True``) forces the rule-based fallback —
    this is what the test suite and the CI-rendered demo use so they never
    need a live Ollama. Otherwise a :class:`LocalQwenExtractor` is built
    against ``SUJI_LLM_BASE_URL`` (default local Ollama).
    """
    forced_off = no_llm if no_llm is not None else os.environ.get("SUJI_NO_LLM") == "1"
    if forced_off:
        return RuleBasedExtractor()
    base = base_url or os.environ.get("SUJI_LLM_BASE_URL") or DEFAULT_BASE_URL
    mdl = model or os.environ.get("SUJI_LLM_MODEL") or DEFAULT_MODEL
    return LocalQwenExtractor(model=mdl, base_url=base, client=client)
