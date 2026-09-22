# fastindex

Fastindex is a research experiment in model-guided source retrieval. It prepares a
small, readable directory index, then walks that tree at query time to return file and
line spans. The aim is to reduce the search work an agent has to do before answering a
codebase question.

Fastindex stops after finding context; answer generation is a separate downstream step.
On Codebase QA, the strongest measured hybrid retrieved 0.905 of the curated gold
evidence for $0.00068 per question. In the downstream answer experiment, retrieval was
only 8.6% of system cost. The single LLM answer call cost 10.6 times more than finding
the context.

![A Fastindex tree walk](docs/assets/tree-reason.gif)

## Codebase QA retrieval result

We used all 48
[Codebase QA](https://github.com/manojbajaj95/agent-learning-bench/tree/main/tasks/codebase-qa)
questions against Flask at commit `85c5d93`.

Fastindex is being evaluated as a retriever: did it find the files containing the
curated gold lines, how long did that take, and what did it cost? Generated-answer
scoring is retained only as a downstream check that the retrieved context is useful. It
also measures answer-model and judge behavior, so it is not the primary retrieval
metric. Improving the answer stage is deferred.

### Cost of finding context

The best retrieval-only pipeline uses a Jev tree walk, eight unique BM25 files, eight
unique FTS files, and one batched Jev Noul rerank. It cost about $0.033 to retrieve
context for all 48 questions.

| Retriever | Gold coverage | File recall | Time/query | Cost/query |
|---|---:|---:|---:|---:|
| Plain Pi retrieval agent | **0.932** | **1.000** | 19.40 s | $0.00656 |
| Jev + BM25 + FTS -> Jev Noul | 0.905 | 0.899 | **4.19 s** | **$0.00068** |

The hybrid was 9.6 times cheaper and 4.6 times faster than Pi retrieval, with lower
recall. This is not a perfectly like-for-like output: Pi returned up to eight precise
spans, while the hybrid selected whole files subject to a 200,000-character context
cap. The comparison answers the operational question—how cheaply can we put relevant
code in front of an answering model—not which output is more precise.

In the four-file downstream run, the cost separated as follows:

| Stage | Cost/query | Time/query | Share of system cost |
|---|---:|---:|---:|
| Find and rerank context | **$0.00068** | 4.36 s | 8.6% |
| Generate one answer with `gpt-5.6-luna` | $0.00723 | 8.84 s | **91.4%** |
| Retrieval + answer | $0.00792 | 13.20 s | 100% |

Finding context is therefore already cheap. The major remaining cost is reading that
context and generating the answer, which is a later optimization target.

### Hybrid retrieval ablations

These runs convert selected candidates to whole files, then measure coverage of the
curated gold spans after the 200,000-character context cap. `RRF` is reciprocal-rank
fusion. The all-source RRF row is a same-run counterfactual using the exact candidate
pool from the Jev-reranked run, which isolates reranking without another stochastic
tree walk.

| Candidate and ranking stages | Gold coverage | File recall | Files/query | Time/query | Cost/query |
|---|---:|---:|---:|---:|---:|
| BM25 unique files | 0.608 | 0.589 | 6.77 | **0.090 s** | **$0** |
| FTS unique files | 0.118 | 0.115 | 2.96 | 0.127 s | $0 |
| BM25 + FTS, RRF | 0.483 | 0.477 | 4.75 | 0.216 s | $0 |
| BM25 + FTS -> Jev Noul rerank | 0.736 | 0.720 | 5.75 | 1.64 s | $0.00045 |
| Jev + BM25 + FTS, RRF (same-run counterfactual) | 0.698 | 0.688 | 4.94 | - | - |
| Jev + BM25 + FTS -> Jev Noul rerank | **0.905** | **0.899** | 6.29 | 4.19 s | $0.00068 |

Naive BM25+FTS fusion was worse than BM25 alone because agreement promoted broad but
incidental files. Jev reranking made the larger pool useful: it raised lexical-only
coverage from 0.483 to 0.736, and adding tree candidates raised it to 0.905. The
16-file all-source pool had a 0.920 file-recall ceiling before selection; the reranker
preserved most of that signal in the files that fit the context budget. See the
[hybrid evaluation](docs/hybrid-evaluation.md) for the design, failure analysis, and
open experiments.

### Downstream answer diagnostic

For completeness, we also gave the retrieved files to one `gpt-5.6-luna` call and
scored its answer against the hidden reference. This evaluates the complete retrieval
plus generation path; it does not isolate retrieval quality.

| System | Answer score | Model calls/query | Input tokens/query | Cost/query |
|---|---:|---:|---:|---:|
| Plain Pi agent | **0.953** | 8.10 | 47,219 | $0.04696 |
| Jev route + BM25, wider | 0.870 | **3.96** | **28,977** | **$0.00562** |
| Jev + BM25 + FTS, Jev rerank, top 4 | 0.891 | 4.96 | 49,009 | $0.00792 |

The top-four hybrid produced 39 of 48 full-score answers and cost 83% less than Pi,
but scored 0.062 lower. Sending up to eight files produced the same 0.891 score while
raising cost to $0.01092. These are single runs and include answer-model behavior;
they should not be read as retriever scores. The benchmark judge added another
$0.00044 per question as evaluation overhead, excluded from system cost.

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
  --sources jev,bm25,fts --jev-k 8 --bm25-k 8 --fts-k 8 \
  --candidate-k 16 --file-k 4 --jev-rerank \
  --decision-min-probability 0.04 \
  --decision-relative-probability 0.05 \
  --out evals/results/codebase-qa-hybrid-reranked-r1.json
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

- The result covers one Python repository. Each Jev and answer configuration has only
  one run, so routing, answering, and judging variance are unknown.
- Whole-file context avoids brittle fixed sections but creates a cost tail. The reranked
  answer run used four files; candidate experiments used a 200,000-character cap.
- Preparation has a real up-front cost. We do not yet have a complete setup measurement
  or a defensible break-even point.
- The current walk does not follow symbol references or links after retrieval, so it can
  miss multi-hop evidence.
- The Noul reranker sees bounded lexical excerpts and generated index summaries, not
  every line of every candidate. Better candidate representations may change its order.
- The next controlled experiments are repeated runs, adaptive file-count selection,
  and symbol-level context extraction after the file rerank.

## License

[MIT](LICENSE). Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
