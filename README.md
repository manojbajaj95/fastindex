# fastindex

Fastindex is a research experiment in model-guided source retrieval. It prepares a
small, readable directory index, then walks that tree at query time to return file and
line spans. The aim is to reduce the search work an agent has to do before answering a
codebase question.

It is not a search engine replacement yet. The current result is a tradeoff: a hybrid
Jev-route-then-answer experiment cut answer cost by 88% against a plain coding agent,
but its answer score was 0.870 rather than 0.953.

![A Fastindex tree walk](docs/assets/tree-reason.gif)

## Codebase QA result

We used all 48
[Codebase QA](https://github.com/manojbajaj95/agent-learning-bench/tree/main/tasks/codebase-qa)
questions against Flask at commit `85c5d93`.

### End-to-end answers

The hybrid uses TypeSafe Jev to route to whole files, adds two BM25 fallback results,
then makes one `gpt-5.6-luna` call to answer from that context. The wider variant lowers
Jev's branch thresholds from `0.12` / `0.20` to `0.04` / `0.05`.

| System | Answer score | Model calls/query | Input tokens/query | Cost/query |
|---|---:|---:|---:|---:|
| Plain Pi agent | **0.953** | 8.10 | 47,219 | $0.04696 |
| Hybrid, default routing | 0.828 | **3.46** | **22,328** | **$0.00444** |
| Hybrid, wider routing | 0.870 | 3.96 | 28,977 | $0.00562 |

Wider routing improved full-file gold coverage from 0.705 to 0.799 and increased the
mean candidate set from 2.60 to 3.85 files. It recovered two additional full-score
answers, reaching 36 of 48, but did not match the plain agent. Its 88% cost reduction is
promising enough to continue the experiment, not evidence that the hybrid is a drop-in
agent replacement.

The answer score uses the benchmark's 1–5 rubric normalized to 0–1. The hybrid harness
used the same judge model and criterion as Codebase QA, but ran directly rather than
inside Harbor. Judge calls cost another $0.00043 per question and are evaluation
overhead, so they are excluded from system cost. All three answer results are single
runs. Pi tokens include cache reads; TypeSafe and answer-model usage come from their
respective APIs.

### Retrieval-only controls

The retrieval fixture contains 99 hand-curated gold spans across 30 files. Every method
returned at most eight ranked spans and generated no answer.

| Retriever | Span recall@8 | Span precision@8 | Span F1@8 | File recall@8 | Time/query | Input tokens/query | Cost/query |
|---|---:|---:|---:|---:|---:|---:|---:|
| Fastindex `tree-reason` | 0.693 | 0.679 | **0.639** | 0.729 | 13.70 s | 11,019 | $0.00306 |
| Pure TypeSafe Jev (`tree-decision`) | 0.391 | 0.585 | 0.439 | 0.668 | 3.68 s | 7,156 | $0.00030 |
| Plain Pi agent | **0.932** | 0.464 | 0.593 | **1.000** | 19.40 s | 51,196 | $0.00656 |
| BM25 | 0.521 | 0.138 | 0.209 | 0.557 | **0.090 s** | 0 | $0 |
| FTS | 0.370 | 0.070 | 0.115 | 0.370 | 0.127 s | 0 | $0 |

Span recall as the result budget grows:

| Retriever | Recall@2 | Recall@4 | Recall@8 |
|---|---:|---:|---:|
| Fastindex `tree-reason` | 0.519 | 0.622 | 0.693 |
| Pure TypeSafe Jev (`tree-decision`) | 0.370 | 0.391 | 0.391 |
| Plain Pi agent | **0.679** | **0.866** | **0.932** |
| BM25 | 0.274 | 0.373 | 0.521 |
| FTS | 0.118 | 0.170 | 0.370 |

In these runs, `tree-reason` was 29% faster than Pi, used 78% fewer input tokens, and
cost 53% less per query. Pi still found substantially more of the gold evidence.
Tree-reason's higher span precision made its span F1 slightly better, but that does not
make up for the recall gap when missing evidence is expensive.

Pure TypeSafe Jev made `tree-decision` 3.7 times faster and 10.2 times cheaper than
`tree-reason`, but its span recall fell from 0.693 to 0.391. Its 0.668 file recall shows
that it often reached a relevant file but chose the wrong fixed 80-line section. This is
the main failure mode to investigate before using the cheaper decision model as an agent
retrieval tool.

`tree-reason` and Pi used `gpt-5.6-luna`; `tree-decision` used the official TypeSafe Jev
API, which resolved `jev-latest` to Jev 1.13.0. Tree-reason, BM25, and FTS are means over
three runs per question; TypeSafe Jev and Pi each have one run. Pi ran in a fresh session
with only `read`, `grep`, `find`, and `ls`, against the original source tree without
generated indexes. Pi input tokens include cache reads. BM25 and FTS searched source
files, not the generated summaries. Times are local wall-clock measurements and costs
are the values recorded by the harness at run time.

The table excludes one-time preparation. The completed artifact has 52 `index.md` files,
59,951 bytes of index text, and covers 234 source files. Preparation was resumed after
interrupted attempts, so its cumulative cost was not captured. The final resume alone
took 832 seconds and 117 model calls; the true setup cost is higher. The raw runs remain
under the gitignored `evals/results/` directory for later analysis.

## How it works

`fastindex prepare` writes an `index.md` in every directory, working from the leaves to
the root. Each index describes its immediate files and child directories. Parent indexes
summarize completed child indexes, producing a human-readable routing tree for ordinary
UTF-8 source repositories as well as Markdown knowledge bundles.

At query time, `tree-reason` starts at the root and asks a model which immediate children
may contain evidence. It opens selected branches in parallel, selects line ranges from
relevant files, and returns the source spans. It does not answer the question.

The preparation pass reads the corpus and calls the configured model. A query reads only
visited indexes and selected file previews. Ambiguous queries can still open many branches,
and a routing decision can prune the right branch. There is no worst-case logarithmic
guarantee.

Fastindex also includes experimental `tree-decision`, which replaces generated routing
choices with typed decisions from TypeSafe System One using Jev. See the
[strategy documentation](docs/tree-reason.md#decision-model-variant) and
[matched evaluation](docs/tree-decision-evaluation.md).

## Try it

```bash
uv sync --extra dev
export FASTINDEX_MODEL=gpt-5.6-luna

uv run fastindex prepare PATH_TO_REPOSITORY
uv run fastindex query PATH_TO_REPOSITORY \
  "Where is request context isolation implemented?" --top-k 8 -v
```

Set the provider key required by the model; [`.env.example`](.env.example) lists the
supported environment variables. Fastindex uses [LiteLLM](https://docs.litellm.ai/) for
preparation and `tree-reason` calls. Source text is sent to that provider.

`prepare` keeps existing nonempty `index.md` files. Pass `--force` to regenerate them
after source changes; this overwrites hand-written indexes. It skips hidden files,
symlinks, binaries, and common generated or dependency directories.

| Command | Purpose |
|---|---|
| `fastindex prepare` | Generate directory indexes from the bottom up |
| `fastindex query` | Return ranked evidence spans from an owned tree strategy |
| `fastindex lint` | Check an OKF Markdown bundle and its links |
| `fastindex generate-index` | Build indexes from concept frontmatter without model summaries |

## Reproduce the evaluation

The gold fixture is tracked at
[`evals/fixtures/queries/codebase_qa.jsonl`](evals/fixtures/queries/codebase_qa.jsonl).
The Flask corpus comes from a sibling `agent-learning-bench` checkout and is copied into
a gitignored directory before preparation.

```bash
uv sync --extra dev
uv run python -m evals.prepare stage-codebase-qa

FASTINDEX_PREPARE_MODEL=gpt-5.6-luna FASTINDEX_MAX_TOKENS=4096 \
  uv run fastindex prepare evals/fixtures/external/codebase-qa-flask

FASTINDEX_TREE_REASON_MODEL=gpt-5.6-luna uv run python -m evals.bench \
  --bundle evals/fixtures/external/codebase-qa-flask \
  --fixtures evals/fixtures/queries/codebase_qa.jsonl \
  --strategies tree-reason,bm25,fts \
  --top-k 8 --cutoffs 2,4,8 --repeats 3

FASTINDEX_DECISION_MODEL=typesafe/jev-latest uv run python -m evals.bench \
  --bundle evals/fixtures/external/codebase-qa-flask \
  --fixtures evals/fixtures/queries/codebase_qa.jsonl \
  --strategies tree-decision --top-k 8 --cutoffs 2,4,8 \
  --wall-budget 180 --call-budget 32 \
  --out evals/results/codebase-qa-tree-decision-typesafe-r1.json

FASTINDEX_MAX_TOKENS=2048 uv run python -m evals.hybrid \
  --decision-min-probability 0.04 \
  --decision-relative-probability 0.05 \
  --out evals/results/codebase-qa-hybrid-wide-r1.json
```

The TypeSafe retrieval run requires `TYPESAFE_API_KEY`. The hybrid additionally requires
`OPENAI_API_KEY` and the sibling Codebase QA checkout for hidden reference answers.

Run the plain-agent baseline against the original source tree so it cannot read the
generated indexes:

```bash
FASTINDEX_PI_MODEL=openai/gpt-5.6-luna uv run python -m evals.bench \
  --bundle ../agent-learning-bench/tasks/codebase-qa/environment/data/repo \
  --fixtures evals/fixtures/queries/codebase_qa.jsonl \
  --strategies pi --top-k 8 --cutoffs 2,4,8 --wall-budget 180 \
  --out evals/results/codebase-qa-pi.json

uv run python -m evals.analyze evals/results/codebase-qa-pi.json --misses
```

Pi must be installed and available on `PATH`. The harness starts a new Pi session for
each question, disables extensions, skills, prompt templates, context files, and write
tools, then parses its ranked JSON spans. The evaluation package also contains BM25, FTS,
vector, reranking, and optional knowledge-graph baselines.

## Limits and open questions

- The result covers one Python repository. Pure Jev, both hybrids, and Pi each have only
  one run, so routing, answering, and judging variance are unknown.
- Whole-file context avoids brittle fixed sections but creates a cost tail. The wider
  run used up to eight files and a 200,000-character context cap.
- Preparation has a real up-front cost. We do not yet have a complete setup measurement
  or a defensible break-even point.
- The current walk does not follow symbol references or links after retrieval, so it can
  miss multi-hop evidence.
- Returned spans are emitted in traversal order. A separate ranking step may improve the
  top-k tradeoff.
- The next controlled experiment is repeated hybrid runs, followed by an adaptive
  fallback that expands routing only when the first answer lacks grounded evidence.

## License

[MIT](LICENSE). Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
