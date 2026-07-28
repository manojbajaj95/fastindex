# fastindex

Browse large corpora of wikis and documents — [OKF](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) markdown trees — with **tree-reason**: an LLM relevance-gated walk inspired by [PageIndex](https://github.com/VectifyAI/PageIndex).

Instead of embedding every chunk and hoping top‑k catches the right page, tree-reason walks the directory tree like a careful reader: at each folder it asks “is this relevant?”, opens only promising children, then bubbles **evidence spans** (`path` + line range) — not a generated answer.

![tree-reason walk](docs/assets/tree-reason.gif)

## Why

Flat search (BM25, embeddings) is fast at lookup and weak at *compositional* finding in a structured wiki. Knowledge-graph systems compile entities and edges; hybrid tools like QMD blend lexical + vector + rerank. **tree-reason** bets on the opposite: keep the human-readable tree, and spend model calls only on navigation and span selection.

This repo ships that walker as the product, plus an optional **`evals/`** harness so you can compare it fairly against flat baselines and external peers on the same gold spans.

## How tree-reason works

1. Start at the bundle root.
2. **Dir gate** — LLM sees `index.md` preview + child dirs/concepts; returns which children to open (or prune).
3. **Section gate** — for each opened page, LLM returns line ranges that evidence the query.
4. Stop on wall-time / model-call budgets (`truncated=true` with partial spans).

**Honest limit:** hierarchy walk ≠ multi-hop. Cross-page wikilinks are not followed yet, so bridge queries can miss far hops — see [Roadmap](#roadmap).

Detail: [docs/tree-reason.md](docs/tree-reason.md).

## Comparison

| Approach | Idea | In this repo |
|----------|------|----------------|
| **tree-reason** | Relevance-gated tree walk → spans | **Product** (`fastindex query`) |
| **QMD** | External hybrid BM25 + vector + RRF + LLM rerank | Peer adapter in `evals/` (requires `qmd` CLI) |
| **Cognee (KG)** | External local knowledge graph | Peer adapter in `evals/` (`uv sync --extra kg`) |
| Flat BM25 / FTS / vsearch | One-shot lexical or embedding top‑k | Baselines in `evals/` |

We do **not** reimplement QMD or Cognee — thin adapters only. On our multi-hop gold, flat one-shot lookup typically fails full hop assembly; tree-reason is a strong *local* finder and shares that multi-hop gap until link-follow lands. Run the harness yourself and rank by `span_recall`, then `latency_ms`.

## Quick start

```bash
uv sync --extra dev
uv run pytest
uv run fastindex lint examples/sample-bundle
uv run fastindex query examples/sample-bundle \
  "What columns are on the orders BigQuery table?" \
  --strategy tree-reason -v
```

Needs an LLM via [LiteLLM](https://docs.litellm.ai/). Copy `.env.example` → `.env`: set `FASTINDEX_MODEL` and the matching provider key (e.g. `OPENAI_API_KEY`). Switch providers by changing the model id / key in ENV.

### CLI

| Command | Purpose |
|---------|---------|
| `fastindex lint` | OKF profile + dangling links |
| `fastindex generate-index` | Regenerate `index.md` from frontmatter |
| `fastindex query` | tree-reason → evidence spans |

No build step — tree-reason is cold / vectorless.

### Compare with baselines (optional)

```bash
uv sync --extra evals   # included in dev
uv run python -m evals.bench
uv run python -m evals.bench --strategies tree-reason,bm25,fts,vsearch,qmd
uv run python -m evals.analyze --misses
```

Bench writes **local** JSON under `evals/results/` (gitignored). Warm sidecars for bm25/vsearch are built by the harness when needed — not by the product CLI.

## Layout

```
src/fastindex/          # product: bundle, tree-reason, CLI, utils
evals/                  # bench harness, baselines, gold fixtures
examples/sample-bundle  # demo wiki
docs/tree-reason.md     # algorithm + knobs
```

## Roadmap

- **Now** — harden tree-reason (gates, budgets, fail-closed JSON); polish docs/examples/evals.
- **Next** — fold link-follow into tree-reason (`search → read → follow → re-search`); multi-hop gold stays a known miss until then.
- **Later** — wiki maintenance loop, denser corpora, PyPI release.
- **Non-goals** — reimplementing QMD, Cognee, or PageIndex; productizing flat baselines.

## License

[MIT](LICENSE). Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
