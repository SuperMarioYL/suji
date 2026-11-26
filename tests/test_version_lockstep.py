"""One mechanical guard for the hand-maintained version surfaces.

A release edits five independent version surfaces and, before this test, nothing
read any of them: a stale one could ship unnoticed (the cross-project patches log
records exactly that mistake on sibling repos). ``VERSION`` in particular is read
by no code at all — it exists purely as a declared surface.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import suji

REPO = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (REPO / relative).read_text(encoding="utf-8")


def test_version_file_is_a_bare_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", _read("VERSION").strip())


def test_every_version_surface_agrees():
    version = _read("VERSION").strip()

    # Python package + CLI surface.
    assert suji.__version__ == version

    # Language manifest (PEP 621).
    assert tomllib.loads(_read("pyproject.toml"))["project"]["version"] == version

    # Product site content marker.
    assert json.loads(_read("web/site.json"))["meta"]["implementation_version"] == version

    # The recorded demo carries the version of the run that produced it.
    assert json.loads(_read("docs/demo-results.json"))["version"] == version


def test_readme_and_site_quote_the_current_version():
    version = _read("VERSION").strip()
    assert f"v{version}" in _read("README.md")
    assert f"v{version}" in _read("README.en.md")
    assert f"v{version}" in json.dumps(json.loads(_read("web/site.json")), ensure_ascii=False)


def test_user_agent_carries_the_current_version():
    """The re-fetch User-Agent is a version surface too.

    It sat at ``SuJi/0.1`` through v0.3.0 because nothing read it: every source
    re-check announced the wrong client version.
    """
    from suji.cascade import _UA

    assert f"SuJi/{_read('VERSION').strip()}" in _UA
