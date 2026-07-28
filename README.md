# fastindex

Browse large corpora of wikis and documents ([OKF](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) markdown trees) with tree-reason: an LLM relevance-gated walk inspired by [PageIndex](https://github.com/VectifyAI/PageIndex).

Instead of embedding every chunk and hoping top-k catches the right page, tree-reason walks the directory tree like a careful reader. At each folder it asks "is this relevant?", opens only promising children, then returns evidence spans (`path` plus line range). It does not generate an answer.

![tree-reason walk](docs/assets/tree-reason.gif)

## Why

Flat search (BM25, embeddings) is fast at lookup and weak at compositional finding in a structured wiki. Knowledge-graph systems compile entities and edges; hybrid tools like QMD blend lexical search, vectors, and rerank. tree-reason keeps the human-readable tree and spends model calls only on navigation and span selection.

This repo ships that walker as the product, plus an optional `evals/` harness so you can compare it against flat baselines and external peers on the same gold spans.

## How tree-reason works

1. Start at the bundle root.
2. Dir gate: the LLM sees an `index.md` preview plus child dirs/concepts, then returns which children to open or prune.
3. Section gate: for each opened page, the LLM returns line ranges that evidence the query.
4. Stop on wall-time or model-call budgets (`truncated=true` with partial spans).

Hierarchy walk is not multi-hop. Cross-page wikilinks are not followed yet, so bridge queries can miss far hops. See [Roadmap](#roadmap).

More detail: [docs/tree-reason.md](docs/tree-reason.md).

## Comparison

| Approach | Idea | In this repo |
|----------|------|----------------|
| tree-reason | Relevance-gated tree walk to spans | Product (`fastindex query`) |
| QMD | External hybrid BM25 + vector + RRF + LLM rerank | Peer adapter in `evals/` (requires `qmd` CLI) |
| Cognee (KG) | External local knowledge graph | Peer adapter in `evals/` (`uv sync --extra kg`) |
| Flat BM25 / FTS / vsearch | One-shot lexical or embedding top-k | Baselines in `evals/` |

We do not reimplement QMD or Cognee; the adapters are thin. On our multi-hop gold, flat one-shot lookup typically fails full hop assembly. tree-reason is a strong local finder and shares that multi-hop gap until link-follow lands. Run the harness yourself and rank by `span_recall`, then `latency_ms`.

## Quick start

```bash
uv sync --extra dev
uv run pytest
uv run fastindex lint examples/sample-bundle
uv run fastindex query examples/sample-bundle \
  "What columns are on the orders BigQuery table?" \
  --strategy tree-reason -v
```

You need an LLM via [LiteLLM](https://docs.litellm.ai/). Copy `.env.example` to `.env`, set `FASTINDEX_MODEL` and the matching provider key (for example `OPENAI_API_KEY`). Switch providers by changing the model id and key in the environment.

### CLI

| Command | Purpose |
|---------|---------|
| `fastindex lint` | OKF profile + dangling links |
| `fastindex generate-index` | Regenerate `index.md` from frontmatter |
| `fastindex query` | tree-reason to evidence spans |

There is no build step. tree-reason is cold and vectorless.

### Compare with baselines (optional)

```bash
uv sync --extra evals   # included in dev
uv run python -m evals.bench
uv run python -m evals.bench --strategies tree-reason,bm25,fts,vsearch,qmd
uv run python -m evals.analyze --misses
```

The bench writes local JSON under `evals/results/` (gitignored). Warm sidecars for bm25/vsearch are built by the harness when needed, not by the product CLI.

## Layout

```
src/fastindex/          # product: bundle, tree-reason, CLI, utils
evals/                  # bench harness, baselines, gold fixtures
examples/sample-bundle  # demo wiki
docs/tree-reason.md     # algorithm + knobs
```

## Roadmap

- [ ] Reranker; harden tree-reason
- [ ] Fold link-follow into tree-reason
- [ ] Run evals on third-party benchmarks

## License

[MIT](LICENSE). Contributions welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
