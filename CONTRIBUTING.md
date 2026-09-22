# Contributing

Thanks for helping with **fastindex**. The product is **tree-reason**; measurement baselines live under **`evals/`**.

## Setup

```bash
uv sync --extra dev
uv run pytest
uv run ruff check src tests evals
```

Optional peers:

```bash
uv sync --extra evals   # already in dev
uv sync --extra kg      # Cognee baseline
```

## Conventions

- **Conventional Commits** — `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:` (imperative, ≤72 char subject).
- **Product vs evals** — `evals` may import `fastindex`; product must never import `evals`.
- **Do not reimplement** Cognee — keep external peers behind thin adapters.
- Prefer span-level gold (`path` + line range) over file-only metrics.
- Keep changes focused; update README / docs when behavior changes.

## Commands worth knowing

```bash
uv run fastindex lint examples/sample-bundle
uv run fastindex query examples/sample-bundle "…" -v
uv run python -m evals.bench
uv run python -m evals.analyze --misses
```

Open a PR against `main` with a short summary and how you tested.
