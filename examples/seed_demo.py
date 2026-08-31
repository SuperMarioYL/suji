#!/usr/bin/env python3
"""Programmatic example: capture a local-file source → ask → edit → cascade.

This is "what using SuJi looks like" from Python, without a Mac or Ollama:
it captures facts from a local 公众号-article file with the rule-based
extractor (``SUJI_NO_LLM=1``), so the whole capture → ask → stale flow runs
headless. The same calls work on macOS with ``MacAXCapturer`` + a local
Qwen3 once Accessibility + Ollama are set up.

Modes (the demo GIF drives both):
  python examples/seed_demo.py --reset   # write article v1, capture facts
  python examples/seed_demo.py --edit     # rewrite the article (source mutates)
"""

from __future__ import annotations

import os
import sys

ARTICLE = "/tmp/suji_demo_article.md"
DB = "/tmp/suji_demo.db"

V1 = (
    "溯记示例公众号文章\n"
    "Q3 营收 4.2 亿，同比增长 12%。\n"
    "用户规模达到 5000 万。\n"
    "本季度毛利率为 38%。\n"
)
V2 = V1.replace("4.2 亿", "4.3 亿").replace("增长 12%", "增长 15%")


def main() -> int:
    os.environ.setdefault("SUJI_NO_LLM", "1")
    os.environ.setdefault("SUJI_DB", DB)
    # Allow `python examples/seed_demo.py` from a source checkout.
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    edit_only = "--edit" in sys.argv
    if edit_only:
        with open(ARTICLE, "w", encoding="utf-8") as fh:
            fh.write(V2)
        print(f"edited source → {ARTICLE}（4.2 亿 → 4.3 亿）")
        return 0

    # --reset (or default): write the article fresh and capture its facts.
    with open(ARTICLE, "w", encoding="utf-8") as fh:
        fh.write(V1)

    from suji.capture import CapturedText, ingest_capture
    from suji.llm import RuleBasedExtractor
    from suji.store import SuJiStore

    class _FileCapturer:
        def capture(self) -> CapturedText:
            with open(ARTICLE, "r", encoding="utf-8") as fh:
                text = fh.read()
            return CapturedText(
                text=text,
                app_bundle="com.apple.Safari",
                doc_url_or_id=ARTICLE,
                doc_title="Q3 财报速递（示例）",
                capture_context="demo",
            )

    store = SuJiStore()
    try:
        n = ingest_capture(_FileCapturer(), RuleBasedExtractor(), store)
        print(f"captured {n} facts from {ARTICLE} (db={DB})")
        for f in store.search_facts("4.2"):
            print(f"  · {f.text}  [{f.status}]")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
