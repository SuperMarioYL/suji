[简体中文](README.md) | **English**

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/hero-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/hero-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/hero-dark.svg">
  <img src="assets/presentation/hero-light.svg" width="960" alt="SuJi 溯记 — Keep the fact and the source it came from.">
</picture>

**SuJi stores extracted facts, source addresses and capture-time fingerprints in SQLite, supports keyword lookup, and marks old facts stale when a recheck detects source changes.**

`Python 3.12+ · optional macOS capture` · [MIT](LICENSE) · [GitHub](https://github.com/SuperMarioYL/suji) · [Website](https://suji.lei6393.com)

## Why it helps

A number can remain in your notes after its source changes. Keeping facts with their origin lets you inspect the file or page they came from and what changed later. fresh/stale describes source-version state, not whether a statement has been proven true or false.

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/process-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/process-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/process-dark.svg">
  <img src="assets/presentation/process-light.svg" width="960" alt="Recheck one edited source">
</picture>

## Architecture

A Capturer supplies text and source labels, a FactExtractor produces statements, and ingest_capture stores sources and facts. provenance normalizes content before SHA-256 hashing. Cascade rereads file, URL or doc_id sources, marks old-fingerprint facts stale and stores a diff. The optional macOS menu bar provides Accessibility capture; the CLI can run headlessly.

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/architecture-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/architecture-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/architecture-dark.svg">
  <img src="assets/presentation/architecture-light.svg" width="960" alt="Source text, fingerprints and stored facts">
</picture>

## Install

Requires Python 3.12+. The basic example uses local files, rule extraction and SQLite; it needs no Mac, Ollama or Accessibility permission.

```bash
git clone https://github.com/SuperMarioYL/suji.git
cd suji
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Quickstart

```bash
python examples/presentation_demo.py
```

Constructed text contains two facts: twelve tasks and version 1.0. After changing the task count to fifteen, the real FileFetcher rereads the source and Cascade marks both old facts stale, retaining a diff with 12/15. The unchanged version fact also becomes stale because the current rule operates on the whole-source fingerprint.

## Usage

```bash
# Query and recheck an existing database
export SUJI_DB="$PWD/suji.db"
suji ask "任务"
suji sources
suji stale
suji stale --list-only

# Optional macOS entrypoint; requires system Accessibility permission
python -m pip install -e ".[macos]"
SUJI_NO_LLM=1 suji-bar
```

ask is a database keyword search, not model question answering. stale refetches stored sources and may use the network for URLs; --list-only displays facts already marked stale. The menu bar reads accessible focused-window text rather than screenshots/OCR.

## Capabilities and integrations

| Source/output | Current boundary |
|---|---|
| Local text files | FileFetcher reads UTF-8 text |
| URL | HTTP retrieval of text/HTML |
| Cloud document ID | Currently unverifiable |
| macOS AX | Accessible-text capture with optional dependencies and permission |
| SQLite / CLI | Facts, sources, fingerprints, diffs and search |

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/integrations-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/integrations-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/integrations-dark.svg">
  <img src="assets/presentation/integrations-light.svg" width="960" alt="Source access and local review">
</picture>

## Configuration and limits

| Environment variable | Default |
|---|---|
| SUJI_DB | ~/.suji/suji.db |
| SUJI_NO_LLM | Set 1 to explicitly select rules |
| SUJI_LLM_BASE_URL | http://localhost:11434/v1 |
| SUJI_LLM_MODEL | qwen3 |

The model endpoint defaults to local but can be configured elsewhere; network isolation is not enforced. Without NO_LLM, the model extractor is selected. A failed request does not automatically become a successful rule extraction.

URL rechecks retrieve raw HTML, which may differ from AX text; comprehensive body normalization is absent. Unreadable sources return unverifiable and are skipped, so a previous fresh label is not a newly successful verification. File watching covers sources present at startup; explicitly run stale for new sources.

## Recorded demo

Real v0.1.0 file-capture interface, rule extraction, SQLite and rechecking. No screen is read, URL fetched or model called; temporary files are cleaned up.

[Inputs, commands and complete output](docs/demo-results.json)

[Retained terminal recording](assets/demo.gif) · [Recording script](docs/demo.tape). The replayable record above describes this example.

## Roadmap

- [x] Source fingerprints, fact storage and keyword lookup.
- [x] File/URL rechecks, stale markers and diffs.
- [x] Optional macOS AX/menu-bar entrypoint.
- [ ] Cloud-document APIs, content normalization and more source adapters.
- [ ] Cross-platform capture and team synchronization.

No deployed hosted-team or compliance-export plan is provided.

## Development and license

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

The local interface example is examples/presentation_demo.py; source and recheck rules live in suji/provenance.py and suji/cascade.py.

[MIT](LICENSE) · [Issues](https://github.com/SuperMarioYL/suji/issues)
