# Roadmap

Public plan for **fastindex** / **tree-reason**. Experimental software for browsing large OKF wiki and document trees.

## Now

- Keep **tree-reason** solid: relevance gate → descend → bubble spans; budgets; fail-closed on bad JSON.
- Polish the OSS surface (docs, examples, evals harness for outsiders).
- Local find/miss on `evals/fixtures/queries/sample.jsonl`.

## Next — multi-hop inside tree-reason

Fold **search → read → follow outbound markdown links → re-search** into tree-reason (not a separate strategy id). Inspired by [LLM-Wiki](https://arxiv.org/abs/2605.25480) §3.2.

Until then, `evals/fixtures/queries/multi_hop.jsonl` is expected to show incomplete hop assembly for both flat baselines and tree-only walk — a known drawback, not a regression.

## Later

- Wiki maintenance loop (ingest / Error Book / reflect) when reflection curves are in scope
- Denser OKF multi-hop corpus / larger Phase-2 wiki
- External OKF converters via `evals.prepare`
- PyPI packaging and release automation
- Demo GIF of a live tree-reason walk (`docs/assets/tree-reason.gif`)

## Non-goals

- Reimplementing QMD, Cognee, or Vectify PageIndex
- Productizing flat BM25/FTS/vsearch (they stay under `evals/` as baselines)
