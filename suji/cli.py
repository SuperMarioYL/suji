"""SuJi CLI — ``ask | stale | sources``.

* ``suji ask "<query>"`` — search captured facts and return each with its
  source provenance (the m1 star-earning moment: "it remembered where I
  saw this").
* ``suji stale`` — re-verify every source's fingerprint, cascade-mark
  derived facts stale, and list them with the old/new source diff (the m2
  moat verb).
* ``suji sources`` — list every remembered source with fact counts.

The capture verb lives in the menu-bar app (mvp_plan.md §4); the CLI is the
read-back + cascade surface, and it runs headless on any platform against
the SQLite store.
"""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .cascade import Cascade, summarize_recheck
from .provenance import CascadeResult
from .store import SuJiStore, default_db_path

app = typer.Typer(
    add_completion=False,
    help="溯记 — local ambient screen-memory with cascade-invalidation.",
    no_args_is_help=True,
)
console = Console()


def _version(version: bool = typer.Option(  # noqa: B008
    False, "--version", help="Show version and exit."
)):
    if version:
        console.print(f"suji {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(  # noqa: B008
        False, "--version", help="Show version and exit.", callback=_version, is_eager=True
    ),
    db: Optional[str] = typer.Option(  # noqa: B008
        None, "--db", envvar="SUJI_DB", help="Override the SQLite store path."
    ),
):
    """溯记 — local ambient screen-memory with cascade-invalidation."""
    # --db is stashed on the context for subcommands via env/option; typer
    # passes it through, and store.default_db_path honors SUJI_DB too.
    if db:
        import os

        os.environ["SUJI_DB"] = db


@app.command()
def ask(
    query: str = typer.Argument(..., help="What to look up, e.g. \"Q3 营收\"."),
):
    """Ask a captured fact and get its source provenance back.

    ``suji ask "Q3 营收"`` → the matching fact(s) + which 公众号 / 钉钉 /
    WPS document they were read from + when.
    """
    store = SuJiStore()
    try:
        facts = store.search_facts(query)
        if not facts:
            console.print(f"[yellow]未找到匹配「{query}」的事实。[/yellow]")
            console.print(
                "[dim]先在菜单栏点「开始记忆」抓取，或用 examples/seed_demo.py "
                "灌入示例数据。[/dim]"
            )
            return
        table = Table(title=f"溯源：{escape(query)}", show_lines=True)
        table.add_column("事实", style="cyan", ratio=2)
        table.add_column("来源应用", style="magenta")
        table.add_column("文档 / 链接", style="blue")
        table.add_column("抓取时间", style="dim")
        table.add_column("状态")
        for fact in facts:
            source = store.get_source(fact.source_id)
            status = "[red]失效[/red]" if fact.status == "stale" else "[green]有效[/green]"
            doc = source.doc_url_or_id if source else "—"
            app_bundle = source.app_bundle if source else "—"
            # Captured text is shown verbatim: rich would otherwise eat a
            # "[red]"-shaped token as a style tag.
            table.add_row(
                escape(fact.text), escape(app_bundle), escape(doc),
                _trim_ts(fact.captured_at), status,
            )
        console.print(table)
        # Surface the source link for the first match (the 公众号 URL).
        first_source = store.get_source(facts[0].source_id)
        if first_source and first_source.source_kind.value == "url":
            console.print(f"[blue]来源：{escape(first_source.doc_url_or_id)}[/blue]")
    finally:
        store.close()


@app.command()
def stale(
    only_list: bool = typer.Option(  # noqa: B008
        False,
        "--list-only",
        help="Skip re-verification and just list already-stale facts.",
    ),
):
    """Re-verify sources and list cascade-stale facts with their source diff.

    ``suji stale`` re-fetches every source's current content, recomputes its
    fingerprint, and marks every fact captured from prior content as stale —
    attaching the old/new diff. This is the cascade verb: when a 公众号
    article is edited or a local WPS file changes, its derived facts don't
    silently mislead.
    """
    store = SuJiStore()
    try:
        results: Optional[list[CascadeResult]] = None
        if not only_list:
            results = Cascade(store).recheck_all()
            _print_recheck(results, store)
        stales = store.list_stale()
        if not stales:
            console.print(_clean_report(results))
            return
        table = Table(title="失效事实（来源已变更）", show_lines=True)
        table.add_column("事实", style="red", ratio=2)
        table.add_column("来源", style="blue")
        table.add_column("新旧来源 diff", style="dim", ratio=2)
        for fact in stales:
            source = store.get_source(fact.source_id)
            doc = source.doc_url_or_id if source else "—"
            table.add_row(
                escape(fact.text), escape(doc), escape(fact.diff or "（无 diff）")
            )
        console.print(table)
    finally:
        store.close()


def _clean_report(results: Optional[list[CascadeResult]]) -> str:
    """The "nothing stale" line, honest about whether anything was checked.

    "No stale facts" is a passing check only when at least one source was
    actually read. With no sources, or only unreadable ones, nothing was
    compared — the green line would manufacture a pass from zero observations.
    """
    if results is None:  # --list-only: no re-check was requested
        return "[green]无失效事实。[/green]"
    summary = summarize_recheck(results)
    if summary.sources == 0:
        return "[yellow]尚未记忆任何来源，无内容可校验。[/yellow]"
    if summary.verified == 0:
        return (
            f"[yellow]所有来源都无法读取（共 {summary.sources} 个），"
            "本轮未校验任何来源，失效状态未知。[/yellow]"
        )
    skipped = (
        f"（{summary.unverifiable} 个来源无法读取）" if summary.unverifiable else ""
    )
    return f"[green]{summary.verified} 个来源已校验，无失效事实。{skipped}[/green]"


@app.command()
def sources():
    """List every remembered source with fact counts and freshness."""
    store = SuJiStore()
    try:
        srcs = store.list_sources()
        if not srcs:
            console.print("[yellow]尚未记忆任何来源。[/yellow]")
            return
        table = Table(title="已记来源", show_lines=True)
        table.add_column("应用", style="magenta")
        table.add_column("文档 / 链接", style="blue", ratio=2)
        table.add_column("标题")
        table.add_column("类型")
        table.add_column("有效", style="green")
        table.add_column("失效", style="red")
        table.add_column("最近校验", style="dim")
        for src in srcs:
            fresh, stales = store.count_facts(src.id)  # type: ignore[arg-type]
            table.add_row(
                escape(src.app_bundle),
                escape(src.doc_url_or_id),
                escape(src.doc_title or "—"),
                src.source_kind.value,
                str(fresh),
                str(stales),
                _trim_ts(_source_checked(store, src.id)) or "—",  # type: ignore[arg-type]
            )
        console.print(table)
    finally:
        store.close()


def _source_checked(store: SuJiStore, source_id: int) -> str:
    row = store._conn.execute(
        "SELECT last_checked_at FROM sources WHERE id = ?", (source_id,)
    ).fetchone()
    return row["last_checked_at"] if row else ""


def _print_recheck(results: list[CascadeResult], store: SuJiStore) -> None:
    body_lines: list[str] = []
    for r in results:
        source = store.get_source(r.source_id)
        doc = source.doc_url_or_id if source else f"id={r.source_id}"
        doc = escape(doc)
        if r.error:
            body_lines.append(f"[dim]· 跳过 {doc}（{escape(r.error)}）[/dim]")
        elif r.mutated:
            body_lines.append(
                f"[red]· {doc} 来源已变更 → {r.stale_count} 条事实标记失效[/red]"
            )
        elif r.stale_count:
            # No mutation since the last check, but facts captured from
            # older content were healed to stale (the source was re-captured
            # after an edit, advancing past a missed cascade window).
            body_lines.append(
                f"[yellow]· {doc} 来源未变，但补标 {r.stale_count} 条"
                "失效事实（上次校验后来源曾变更）[/yellow]"
            )
        else:
            body_lines.append(f"[green]· {doc} 来源未变[/green]")
    console.print(Panel("\n".join(body_lines), title="来源再校验"))


def _trim_ts(ts: str) -> str:
    """Render an ISO-8601 timestamp compactly (drop subseconds / offset)."""
    if not ts:
        return ""
    # 2026-08-31T07:16:02.924887+00:00 → 2026-08-31 07:16
    return ts.replace("T", " ")[:16]


def main() -> None:
    app()


if __name__ == "__main__":
    main()
