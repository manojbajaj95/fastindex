# Domain glossary for fastindex

Terms used in code, docs, and architecture reviews. Prefer these names over synonyms.

## Product surface

| Term | Meaning |
|------|---------|
| **tree-reason** | Owned retrieval strategy: LLM relevance-gated walk over an OKF tree → evidence **spans**. |
| **OKF / bundle** | Markdown knowledge-bundle corpus (concepts, optional `index.md`, links). |
| **span** | Evidence hit `{path, start_line, end_line[, text]}`. Prefer span-level gold over file-only. |
| **strategy** | Named retrieve implementation behind the `Strategy` protocol. |
| **owned strategy** | Product registry only: currently `tree-reason`. |
| **CLI** | `prepare`, `lint`, `generate-index`, `query` — product commands only (no vector `build`). |
| **utils** | Product helpers under `fastindex.utils`: prepare, lint, generate-index, markdown links. |

## Evaluation surface (`evals/`)

| Term | Meaning |
|------|---------|
| **evals** | Lab package for harness, gold scoring, baseline adapters, fixtures, and results. Depends on product; product never imports evals. |
| **baseline** | Flat lexical/vector adapter used only for measurement (`bm25`, `fts`, `vsearch`, `rerank`). |
| **peer** | Thin external adapter (`qmd`, `cognee`); never reimplemented. |
| **gold** | Annotated evidence spans in query JSONL under `evals/fixtures/queries/`. |
| **span_recall** | Primary ranking metric (fraction of gold spans overlapped); then `latency_ms` within track + model. |
| **ops** | Latency, tokens, cost, model_calls, hops, truncated, tree extras. |
| **track** | Condition: cold/warm; constraint tracks vectorless/open. |
| **fixtures** | Gold corpus + queries under `evals/fixtures/`. |
| **examples** | Demo path (`examples/sample-bundle` → shared lab wiki). Same content as eval gold today. |
| **evals.index** | `.fastindex/` sidecar builder/loader for warm baselines (not product). |

## Machine state

| Term | Meaning |
|------|---------|
| **.fastindex/** | Regenerable sidecars (hash, BM25 docs, embeddings); gitignored. Used by evals warm baselines only. |
| **build (evals)** | Sidecar write via `evals.index.build_index` / auto in `evals.bench` — not a CLI command. |
