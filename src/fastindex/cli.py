"""fastindex CLI: prepare, query, and bundle utilities."""

from __future__ import annotations

import json
import time
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Annotated

import typer

from fastindex.bundle import load_lazy_bundle
from fastindex.strategies import StrategyConfig, get_strategy, list_strategies
from fastindex.utils.generate import generate_indexes, generate_section_synopses
from fastindex.utils.lint import lint_bundle
from fastindex.utils.prepare import prepare

BundlePath = Annotated[
    Path,
    typer.Argument(
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="OKF bundle directory",
    ),
]

app = typer.Typer(
    name="fastindex",
    help="Browse OKF wiki trees with tree-reason. Baselines live under evals/.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"fastindex {pkg_version('fastindex')}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = None,
) -> None:
    """Browse OKF wiki trees with tree-reason."""


@app.command()
def lint(
    bundle: BundlePath,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Exit non-zero on warnings too."),
    ] = False,
) -> None:
    """Lint bundle against the OKF lab profile."""
    issues = lint_bundle(bundle)
    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]
    for i in issues:
        typer.echo(f"{i.level.upper()}: {i.path}: {i.message}")
    typer.echo(f"{len(errors)} error(s), {len(warnings)} warning(s)")
    if errors or (strict and warnings):
        raise typer.Exit(1)


@app.command("generate-index")
def generate_index(
    bundle: BundlePath,
    synopses: Annotated[
        bool,
        typer.Option("--synopses", help="Also write .fastindex/section_synopses.md"),
    ] = False,
) -> None:
    """Regenerate index.md files from concept frontmatter."""
    written = generate_indexes(bundle, write=True)
    typer.echo(f"Wrote {len(written)} index.md file(s)")
    if synopses:
        generate_section_synopses(bundle, write=True)
        typer.echo("Wrote .fastindex/section_synopses.md")


@app.command("prepare")
def prepare_bundle(
    bundle: BundlePath,
    force: Annotated[
        bool,
        typer.Option("--force", help="Regenerate existing index.md files too."),
    ] = False,
) -> None:
    """Create model-written OKF index.md files throughout a repository or wiki."""
    try:
        stats = prepare(bundle, force=force)
    except Exception as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    typer.echo(
        f"Wrote {stats.written} index.md file(s); preserved {stats.preserved}; "
        f"summarized {stats.files_summarized} file(s); skipped {stats.files_skipped}; "
        f"model calls {stats.usage.calls}; input tokens {stats.usage.input_tokens}; "
        f"output tokens {stats.usage.output_tokens}."
    )


@app.command()
def query(
    bundle: BundlePath,
    query_text: Annotated[str, typer.Argument(help="Natural-language query")],
    strategy: Annotated[
        str,
        typer.Option(
            "--strategy",
            "-s",
            help="Owned retrieval strategy (default: tree-reason). Baselines: evals harness.",
        ),
    ] = "tree-reason",
    top_k: Annotated[int, typer.Option("--top-k", help="Max spans to return")] = 2,
    wall_budget: Annotated[
        float,
        typer.Option("--wall-budget", help="Wall-time budget in seconds"),
    ] = 60.0,
    call_budget: Annotated[
        int,
        typer.Option("--call-budget", help="Max model calls"),
    ] = 32,
    parallelism: Annotated[
        int,
        typer.Option("--parallelism", min=1, help="Maximum concurrent model calls"),
    ] = 4,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Print full JSON (spans + stats)"),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Print run stats to stderr"),
    ] = False,
) -> None:
    """Run a single query with tree-reason and print matching spans."""
    known = list_strategies()
    if strategy not in known:
        typer.echo(
            f"Unknown strategy {strategy!r}. Owned: {', '.join(known)}. "
            "Baselines (bm25, fts, …) run via: uv run python -m evals.bench",
            err=True,
        )
        raise typer.Exit(1)

    strat = get_strategy(strategy)
    cfg = StrategyConfig(
        top_k=top_k,
        wall_time_budget_s=wall_budget,
        model_call_budget=call_budget,
        parallelism=parallelism,
    )
    try:
        t0 = time.perf_counter()
        b = load_lazy_bundle(bundle)
        result = strat.retrieve(query_text, b, cfg)
        latency_ms = (time.perf_counter() - t0) * 1000
    except Exception as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e

    if as_json:
        typer.echo(json.dumps(result.to_dict(), indent=2))
        return

    if verbose:
        typer.echo(
            f"strategy={strategy} track={result.stats.track} "
            f"latency_ms={latency_ms:.1f} model_calls={result.stats.model_calls} "
            f"tokens_in={result.stats.input_tokens} tokens_out={result.stats.output_tokens} "
            f"truncated={result.stats.truncated}",
            err=True,
        )

    if not result.spans:
        typer.echo("No matches.")
        return

    for i, span in enumerate(result.spans, 1):
        if i > 1:
            typer.echo("")
        typer.echo(f"{span.path}:{span.start_line}-{span.end_line}")
        text = span.text.strip("\n")
        if text:
            typer.echo(text)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
