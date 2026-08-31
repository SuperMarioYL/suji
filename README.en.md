<div align="right"><sub><b>English</b>&nbsp;&nbsp;⇄&nbsp;&nbsp;<a href="./README.md">简体中文</a></sub></div>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./assets/hero-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="./assets/hero-light.svg">
  <img src="./assets/hero-light.svg" width="880" alt="SuJi — ambient screen-memory">
</picture>

<p align="center"><sub>A local-Qwen ambient memory that tags each captured fact by source, and cascade-invalidates it when the source mutates.</sub></p>

<p align="center">
  <a href="./LICENSE"><img src="https://img.shields.io/github/license/SuperMarioYL/suji" alt="MIT"></a>
  <img src="https://img.shields.io/github/v/release/SuperMarioYL/suji" alt="release">
  <img src="https://img.shields.io/github/actions/workflow/status/SuperMarioYL/suji/ci.yml?branch=main&label=CI" alt="CI">
  <img src="https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white" alt="python">
</p>

> **Can't recall where you saw that number — SuJi remembers which 公众号 article or DingTalk doc it came from, and flags it stale the moment the source is edited.**

<h2><img src="https://api.iconify.design/tabler:topology-star-3.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Architecture</h2>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./assets/atlas-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="./assets/atlas-light.svg">
  <img src="./assets/atlas-light.svg" width="880" alt="architecture: AX capture → local Qwen3 → SQLite provenance store → cascade; CLI read-back">
</picture>

A single-process menu-bar app plus a CLI — no microservices, no cloud. The menu bar reads the **plain text** of your focused window via the macOS Accessibility API (no screenshots, no OCR), a local Qwen3 via Ollama extracts discrete facts, and each is stored in SQLite with a per-source fingerprint. The CLI exposes `ask` (provenance lookup), `stale` (cascade invalidation), and `sources` (source listing).

<h2><img src="https://api.iconify.design/tabler:bulb.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Why this exists</h2>

Chinese prosumers read across 微信公众号, browsers, DingTalk docs and WPS every day, copying numbers, specs and quotes into a 备忘录 — then weeks later cannot trace which 公众号 article a figure came from, and worse, that article was silently corrected while the note still cites the old number. SuJi tags every captured fact with its source app + a document fingerprint, and when that document mutates, the facts derived from the prior content are **cascade-flagged stale** with an old/new diff.

`ambient-context` (Show HN, 62 upvotes) proved the no-screenshot AX-capture shape is wanted, but it ships text to the cloud, stores flat daily markdown with no per-fact provenance, and never notices when a source changes. SuJi moves that shape onto the Chinese user's own device — local Qwen render, data never leaving the Mac — and adds the source-level cascade it lacks.

| | ambient-context | SuJi |
|---|:---:|:---:|
| No-screenshot AX capture | ✓ | ✓ |
| Data stays on device | ✗ (cloud) | ✓ (local Qwen3) |
| Per-fact source fingerprint | ✗ (flat daily md) | ✓ |
| Cascade on source mutation | ✗ | ✓ |
| CN-app sources (公众号/DingTalk/WPS) | — | ✓ (file sources stable in v0.1) |

<h2><img src="https://api.iconify.design/tabler:rocket.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Install & Quickstart</h2>

```bash
# 1. Pull a local Qwen3 once (reused thereafter)
ollama pull qwen3

# 2. Install SuJi (menu-bar deps are in the [macos] extra; the CLI runs anywhere)
pip install -e ".[macos]"

# 3. Grant Accessibility (System Settings → Privacy & Security → Accessibility), then launch
suji-bar          # click "开始记忆" in the menu bar to capture focused-window text
```

> No Mac, or no Ollama yet? The CLI `ask / stale / sources` verbs run headless on any platform — see [Demo](#demo).

<details><summary>After install, your first capture</summary>

Open a 公众号 article (or a local WPS doc), click "开始记忆" in the menu bar → the background loop captures focused-window text every few seconds → the local qwen3 extracts facts into the store. Then:

```bash
suji ask "Q3 营收"      # returns the fact + the article URL + capture time
suji sources            # lists remembered sources
```
</details>

<h2><img src="https://api.iconify.design/tabler:terminal-2.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Usage</h2>

```bash
# Provenance lookup — recall where you saw it, traced back to the source
suji ask "Q3 营收"
# fact             source app       doc / link            captured        status
# Q3 营收 4.2 亿   com.apple.Safari  https://mp.weixin...  2026-08-31 07:34 fresh
# source: https://mp.weixin.qq.com/s/abc

# Re-verify sources and cascade-invalidate — after the article was edited
suji stale
# · https://mp.weixin.qq.com/s/abc source changed → 1 fact marked stale
# Q3 营收 4.2 亿   source: ...abc   source diff (4.2 亿 → 4.3 亿)

# List every remembered source with fresh/stale counts
suji sources
```

More in [`examples/seed_demo.py`](./examples/seed_demo.py) — the full 10-minute programmatic path: capture a local-file source → edit → `suji stale`.

<h2><img src="https://api.iconify.design/tabler:photo.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Demo</h2>

Capture a fact from a 公众号 article → `ask` for provenance → edit the article → `suji stale` cascade-marks it stale with the source diff:

![demo](assets/demo.gif)

(CI renders [`assets/demo.gif`](./assets/demo.gif) from [`docs/demo.tape`](./docs/demo.tape) via [vhs](https://github.com/charmbracelet/vhs); re-render locally with `vhs docs/demo.tape`.)

<h2><img src="https://api.iconify.design/tabler:adjustments.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Configuration</h2>

| Env var | Type | Default | Meaning |
|---|---|---|---|
| `SUJI_DB` | path | `~/.suji/suji.db` | SQLite store path (tests / demo can override) |
| `SUJI_NO_LLM` | `1`/unset | unset | Set `1` to use the rule-based extractor without Ollama (tests / CI / demo) |
| `SUJI_LLM_BASE_URL` | url | `http://localhost:11434/v1` | Ollama's OpenAI-compatible endpoint |
| `SUJI_LLM_MODEL` | str | `qwen3` | Local model name |

The menu-bar capture interval is `_CAPTURE_INTERVAL` at the top of [`suji/menu_bar.py`](./suji/menu_bar.py) (default 5 seconds).

<h2><img src="https://api.iconify.design/tabler:map-2.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Roadmap</h2>

- [x] **m1 — capture + provenance**: menu-bar captures focused-window text via the AX API, local qwen3 extracts discrete facts, each stored with a source fingerprint, `suji ask` traces back to the source
- [x] **m2 — cascade**: source fingerprint re-verification (periodic URL re-fetch / watchdog on local WPS·DingTalk cache files), mismatch cascade-marks facts stale + old/new source diff
- [ ] **m3 — one-click install**: one-click Ollama bridge, 10-minute install, extend to full 微信/DingTalk/WPS deep text parsing (normalize the AX-text vs HTTP-HTML representation gap for URL sources)
- [ ] Windows / Linux port (AX API is macOS-specific; UI Automation is the counterpart path)
- [ ] Team-shared provenance + compliance-audit export (see [Paid](#paid))

<h2><img src="https://api.iconify.design/tabler:cash.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Paid</h2>

v0.1 is fully free open source, runs locally, with no paywall — this layer is untouched. But a commercial audience can't ship naked: a **team sync + compliance trace** path for on-prem / 信创 deployment is the monetization vector, targeting SMB investment-research / consulting teams (5–20 people, heavy DingTalk-doc / Feishu orgs) who need the two things v0.1 doesn't have:

- **On-prem deployment** — installed into their DingTalk / Feishu tenant (Docker image + SQLite→Postgres)
- **Team fact sync** — per-source provenance shared across people
- **Compliance-audit export** — `suji stale` source diff exported to PDF, satisfying the finance / consulting "traceable source" hard requirement

Price points (educated guess): team plan **¥499/seat/year**; 信创 on-prem site **¥20k–50k/year/site**. Billing via WeChat Pay / corporate transfer (no Stripe CN); international via Lemon Squeezy. The v0.1 free trial is exactly the seed for the paid segment — paid demand is validated inside the free trial, the paywall lands in v0.2+, never inside v0.1 scope.

<h2><img src="https://api.iconify.design/tabler:license.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> License & Contributing</h2>

MIT — see [LICENSE](./LICENSE). File an issue or PR at `github.com/SuperMarioYL/suji/issues`.

<p align="center"><sub><a href="./LICENSE">MIT</a> © 2026 SuperMarioYL</sub></p>
