"""SuJi (溯记) — local ambient screen-memory with cascade-invalidation.

A macOS menu-bar tool that captures focused-window text across
微信公众号 / 浏览器 / 钉钉 / WPS via the Accessibility API (no screenshots,
no OCR), extracts discrete facts with a local Qwen3 (Ollama), tags each fact
with per-source provenance + a capture-time content fingerprint, and
cascade-invalidates derived facts when the originating document mutates.

The data never leaves the device: the only model invoked is a local Qwen3
running on the user's own Ollama.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
