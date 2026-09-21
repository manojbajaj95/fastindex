# fastindex

**Research preview.** fastindex explores whether a model can find source context by walking a prepared directory tree. It is an experiment in retrieval quality and cost, not a demonstrated replacement for lexical or vector search.

![tree-reason walk](docs/assets/tree-reason.gif)

## The experiment

`prepare` writes an `index.md` in each directory, from the leaves upward. Each index lists its immediate directories and searchable files with a short description. A parent entry summarizes its completed child index. The indexes are readable Markdown inspired by [OKF](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md); repositories with non-OKF source files can use them too.

At query time, `tree-reason` reads the root index, asks a model which immediate children might hold evidence, and opens selected branches. Several branches can run concurrently. For selected files, it returns source text with paths and line numbers. It does not generate an answer or follow cross-page links. The approach is inspired by [PageIndex](https://github.com/VectifyAI/PageIndex).

Experimental `tree-jev`, `tree-laya`, and `tree-watt` use typed choices to select branches and page sections. Jev and Laya use [classifier.dev](https://classifier.dev/); Watt uses [WattAI](https://wattai.dev/#api). Their hosted free routes need no API key at present. Index preparation still uses an LLM. Run `uv run fastindex query examples/sample-bundle "your question" --strategy tree-jev` or compare them with `uv run python -m evals.bench --strategies tree-reason,tree-jev,tree-laya,tree-watt`. See [strategy details](docs/tree-reason.md#decision-model-variants) and [matched evaluations](docs/tree-decision-evaluation.md).

The preparation pass reads the corpus and makes model calls. `tree-reason` queries read only visited indexes and selected files; decision-model queries also read bounded descendant index titles for routing. Ambiguous queries can open many branches. There is no worst-case logarithmic guarantee. A model can prune a relevant branch or select plausible but non-gold evidence.

## Early observation

On the bundled nine-directory, ten-query sample wiki, one local run with `gpt-5.6-luna` gave:

| Generated indexes | Index text | Mean span recall | Mean query latency | Total query input tokens | Total estimated query cost |
|---|---:|---:|---:|---:|---:|
| Earlier verbose summaries | 16.2 KB | 0.95 | 12.1 s | 32,489 | $0.0112 |
| Short table-of-contents entries | 3.7 KB | 0.90 | 15.9 s | 17,347 | $0.0082 |

These are single runs on a small, hand-built fixture. The short indexes missed both gold spans on a query connecting an espresso-tonic recipe to its order SKU. The numbers show a prompt-size tradeoff, not a general quality, speed, or cost advantage. Larger corpora and repeated runs are needed.

## Try it

```bash
uv sync --extra dev
uv run pytest
uv run fastindex lint examples/sample-bundle
uv run fastindex query examples/sample-bundle \
  "What columns are on the orders BigQuery table?" -v
```

Set `FASTINDEX_MODEL` and its provider key for `prepare` and `tree-reason`; see [`.env.example`](.env.example). Those commands use [LiteLLM](https://docs.litellm.ai/). Decision-model queries use hosted APIs without keys under the current free terms. Preparation sends source text to the configured model provider; queries send index summaries and selected file previews. To prepare a new repository or wiki:

```bash
uv run fastindex prepare PATH
uv run fastindex query PATH "your question"
```

`prepare` preserves existing nonempty `index.md` files. Use `--force` to regenerate them after source changes; it overwrites hand-written indexes. `FASTINDEX_PREPARE_MODEL` can select a different preparation model, and `FASTINDEX_TREE_REASON_MODEL` can select a different query model. Preparation ignores `log.md`, common generated and dependency directories, symlinks, hidden files, and binary files. `fastindex lint` checks OKF Markdown bundles; arbitrary source repositories need not pass it.

### Commands

| Command | Purpose |
|---|---|
| `fastindex prepare` | Write model-generated `index.md` files bottom-up |
| `fastindex query` | Retrieve evidence spans; `--parallelism` limits concurrent model calls |
| `fastindex lint` | Check an OKF Markdown bundle and its links |
| `fastindex generate-index` | Generate indexes from concept frontmatter without model summaries |

### Run the evaluation harness

```bash
uv sync --extra evals
uv run python -m evals.bench
uv run python -m evals.bench --strategies tree-reason,bm25,fts
uv run python -m evals.analyze --misses
```

BM25, FTS, vector search, and external QMD/Cognee adapters live under `evals/` for comparison. The harness records span-level quality, latency, model usage, and setup; its results go to gitignored `evals/results/`. See [the algorithm and knobs](docs/tree-reason.md) and [the domain glossary](CONTEXT.md).

## Open questions

- Does directory routing keep recall on larger repositories and less curated trees?
- How should evidence from several branches be ranked before the `top_k` cutoff?
- When does parallel traversal lower latency enough to justify its extra calls?
- Can a typed decision model match LLM span recall on larger corpora and multi-page questions?
- How should cross-page links be followed for multi-hop queries?

## License

[MIT](LICENSE). Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
