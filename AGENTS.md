# Agent notes

- **Story:** `tree-reason` and experimental `tree-decision` are owned tree strategies. Flat lexical/vector (`bm25`, `fts`, `vsearch`) and external peers (`pi`, `cognee`) live under **`evals/`** for benchmarking, not the product surface.
- Domain glossary: [`CONTEXT.md`](CONTEXT.md). Research questions: README [Limits and open questions](README.md#limits-and-open-questions). Contributing: [`CONTRIBUTING.md`](CONTRIBUTING.md).
- Strategy docs: [`docs/tree-reason.md`](docs/tree-reason.md); decision-model results: [`docs/tree-decision-evaluation.md`](docs/tree-decision-evaluation.md).
- CLI: `prepare`, `lint`, `generate-index`, `query` (owned strategies only). No vector `build` — all tree strategies are vectorless; eval sidecars are built by `evals.bench` / `evals.index`.
- Product helpers: `fastindex.utils` (`prepare`, `lint`, `generate`, `links`). Sidecar I/O: `evals.index`.
- Bench is `python -m evals.bench` — writes **local** `evals/results/` (gitignored; do not commit runs).
- Wiki maintenance (`ingest` / `reflect` / Error Book) is deferred.
- Do not reimplement Cognee — external baselines stay behind thin adapters.
- `.fastindex/` is gitignored; regenerable for warm eval baselines only.
- Prefer span-level gold (`path` + line range) over file-only metrics.
- Record **retrieval quality** + **ops** + setup on every bench row; rank by `span_recall`, then `latency_ms` within track + model.
- Multi-hop gold (`evals/fixtures/queries/multi_hop.jsonl`): flat baselines and tree strategies are expected to miss full hop assembly (the trees have no wikilink hops). That is a known drawback, not a regression.
- Demo path: `examples/sample-bundle` → shared wiki at `evals/fixtures/sample-bundle`.

```bash
uv sync --extra dev
uv sync --extra evals   # baselines + prepare downloads (included in dev)
uv sync --extra kg      # optional: local Cognee baseline
uv run pytest
uv run fastindex prepare examples/sample-bundle  # fills missing index.md files using a model
uv run fastindex lint examples/sample-bundle
uv run fastindex query examples/sample-bundle "…" --strategy tree-reason
uv run fastindex query examples/sample-bundle "…" --strategy tree-decision \
  --decision-model typesafe/jev-latest  # requires TYPESAFE_API_KEY
uv run python -m evals.bench
uv run python -m evals.bench --strategies tree-reason,bm25,fts \
  --fixtures evals/fixtures/queries/multi_hop.jsonl
uv run python -m evals.analyze --misses
# Optional peers:
# uv run python -m evals.bench --strategies tree-reason,bm25,fts,vsearch,pi
# uv run python -m evals.bench --strategies tree-reason,cognee --fixtures evals/fixtures/queries/multi_hop.jsonl
uv run python -m evals.prepare list
uv run python -m evals.prepare peers
```
