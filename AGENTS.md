# Agent notes

- **Story:** `tree-reason` is the owned product. Flat lexical/vector (`bm25`, `fts`, `vsearch`) and external peers (`qmd`, `cognee`) live under **`evals/`** for benchmarking, not the product surface.
- Domain glossary: [`CONTEXT.md`](CONTEXT.md). Planned work: [`ROADMAP.md`](ROADMAP.md). Contributing: [`CONTRIBUTING.md`](CONTRIBUTING.md).
- Strategy docs: [`docs/tree-reason.md`](docs/tree-reason.md).
- CLI: `lint`, `generate-index`, `query` (owned strategies only). No `build` — tree-reason is cold; eval sidecars are built by `evals.bench` / `evals.index`.
- Product helpers: `fastindex.utils` (`lint`, `generate`, `links`). Sidecar I/O: `evals.index`.
- Bench is `python -m evals.bench` — writes **local** `evals/results/` (gitignored; do not commit runs).
- Wiki maintenance (`ingest` / `reflect` / Error Book) is deferred — see [`ROADMAP.md`](ROADMAP.md).
- Do not reimplement QMD or Cognee — external baselines only.
- `.fastindex/` is gitignored; regenerable for warm eval baselines only.
- Prefer span-level gold (`path` + line range) over file-only metrics.
- Record **retrieval quality** + **ops** + setup on every bench row; rank by `span_recall`, then `latency_ms` within track + model.
- Multi-hop gold (`evals/fixtures/queries/multi_hop.jsonl`): flat baselines and **tree-reason** are expected to miss full hop assembly (tree has no wikilink hops). That is a known drawback, not a regression.
- Demo path: `examples/sample-bundle` → shared wiki at `evals/fixtures/sample-bundle`.

```bash
uv sync --extra dev
uv sync --extra evals   # baselines + prepare downloads (included in dev)
uv sync --extra kg      # optional: local Cognee baseline
uv run pytest
uv run fastindex lint examples/sample-bundle
uv run fastindex query examples/sample-bundle "…" --strategy tree-reason
uv run python -m evals.bench
uv run python -m evals.bench --strategies tree-reason,bm25,fts \
  --fixtures evals/fixtures/queries/multi_hop.jsonl
uv run python -m evals.analyze --misses
# Optional peers:
# uv run python -m evals.bench --strategies tree-reason,bm25,fts,vsearch,qmd
# uv run python -m evals.bench --strategies tree-reason,cognee --fixtures evals/fixtures/queries/multi_hop.jsonl
uv run python -m evals.prepare list
uv run python -m evals.prepare peers
```
