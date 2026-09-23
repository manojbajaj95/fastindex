# TypeSafe Jev decision evaluation

`tree-decision` uses categorical probabilities to prune a prepared tree and,
by default, choose one evidence section per selected page. The TypeSafe Jev
version is configuration, rather than a separate retrieval strategy. It
generates no text at query time. `prepare` still uses an LLM to create missing
indexes.

## September 22, 2026 pure Jev retrieval result

This single cold-track run covers all 48 Codebase QA questions against the same
prepared Flask tree used by the main benchmark. Preparation cost is excluded.

| Strategy | Span recall@8 | Span F1@8 | File recall@8 | Latency/query | Calls/query | Input/query | Cost/query |
|---|---:|---:|---:|---:|---:|---:|---:|
| `tree-decision` / TypeSafe Jev 1.13.0 | 0.391 | 0.439 | 0.668 | 3.68 s | 4.08 | 7,156 | $0.00030 |
| `tree-reason` / `gpt-5.6-luna` | 0.693 | 0.639 | 0.729 | 13.70 s | 5.30 | 11,019 | $0.00306 |

TypeSafe Jev was 3.7 times faster and 10.2 times cheaper per query than
`tree-reason`, with 35% fewer input tokens. That saving came with a large
quality loss: span recall fell from 0.693 to 0.391. Recall saturated at four
returned spans (`0.370@2`, `0.391@4`, `0.391@8`) because the strategy returned
only 1.56 spans per question on average.

The gap between 0.668 file recall and 0.391 span recall identifies the main
failure mode. The tree often routed to a relevant file, then selected the wrong
fixed 80-line section. Examples include `src/flask/sansio/blueprints.py`,
`src/flask/cli.py`, and `src/flask/testing.py`. Improving code-aware section
boundaries or returning several plausible sections is more promising than
further tuning only the directory routing model.

The run made 196 TypeSafe calls and used 343,477 input and 29,143 output tokens
in total. Fastindex's $0.01443 total estimate uses TypeSafe's input-token price.

## Jev file routing plus one answer call

The hybrid stops the tree walk when it reaches files instead of asking Jev to
choose one fixed section. It adds the paths from the first two BM25 results,
deduplicates them, and sends the whole line-numbered files to one
`gpt-5.6-luna` answer call. This folds evidence selection into answering rather
than paying for a separate LLM reranker.

These are single runs over the same 48 questions. Model calls, tokens, latency,
and cost include retrieval and answering but exclude the evaluation judge and
one-time preparation.

| System | Answer score | Full-file gold coverage | Candidate files | Calls/query | Input/query | Cost/query |
|---|---:|---:|---:|---:|---:|---:|
| Plain Pi agent | **0.953** | — | — | 8.10 | 47,219 | $0.04696 |
| Hybrid, default routing | 0.828 | 0.705 | **2.60** | **3.46** | **22,328** | **$0.00444** |
| Hybrid, wider routing | 0.870 | **0.799** | 3.85 | 3.96 | 28,977 | $0.00562 |

The wider run used `decision_min_probability=0.04` and
`decision_relative_probability=0.05`, compared with defaults `0.12` and `0.20`.
It cost 26% more than the default hybrid but improved answer score by 0.042 and
coverage by 0.094. Against the plain agent, it used 39% fewer reported input
tokens and cost 88% less, while scoring 0.083 lower. TypeSafe tokens and Pi
tokens including cache reads are not equivalent accounting systems, so cost is
the more useful operational comparison.

Coverage strongly predicted answer quality in the wider run: questions with
complete gold coverage averaged 0.977, partial coverage averaged 0.731, and no
coverage averaged 0.333. Complete context was not sufficient in every case;
answer-model mistakes still occurred. Conversely, partial context sometimes
contained enough evidence for a full-score answer.

The wider run returned 36 full-score answers, used 142 TypeSafe routing calls
plus 48 answer calls, and cost $0.26958 in total. The same-model judge cost an
additional $0.02070. It ran directly through the local harness using Codebase
QA's judge model and criterion, rather than inside Harbor, so the score is
benchmark-compatible but not an identical infrastructure replay.

The current evidence supports the architecture but not the chosen thresholds:
both routing and answer generation have visible single-run variance. Repeated
runs are required before treating the 0.042 score difference as stable.

## Jev as a file reranker

A follow-up experiment added eight unique BM25 files and eight unique FTS files
to the Jev tree candidates, fused a pool of at most 16, and scored every
candidate with one batched Jev Noul request. The best four whole files then went
to one `gpt-5.6-luna` answer call.

The retrieval-only run reached 0.905 gold-span coverage and 0.899 file recall at
4.19 seconds and $0.00068 per question. On the exact same candidate pool,
unreranked reciprocal-rank fusion reached only 0.698 coverage and 0.688 file
recall. A lexical-only Jev rerank reached 0.736 coverage, showing that the tree
and lexical candidates were complementary.

The four-file answer run scored 0.891 with 39 of 48 full-score answers. It cost
$0.00792 per question, 83% below the plain Pi agent's $0.04696, while Pi still
scored higher at 0.953. An eight-file run also scored 0.891 but cost $0.01092,
so extra whole-file context did not help this single run.

See the [hybrid evaluation](hybrid-evaluation.md) for the ablations, cost split,
failures, recommendation, and open studies.

## September 20–21, 2026 sample result

These are single cold-track runs over the ten queries in
`evals/fixtures/queries/sample.jsonl`, with `top_k=2`. Every comparison within
an index group used the same bundle and span-level gold. Values are means;
calls and query cost are totals. Preparation cost is excluded.

| Strategy | Span recall | Span F1 | Line F1 | Latency | Calls | Query cost |
|---|---:|---:|---:|---:|---:|---:|
| `tree-decision` / TypeSafe Jev 1.13 | **0.95** | **0.933** | **0.759** | **4.19 s** | 38 | **$0.00082 estimated** |
| `tree-reason` / `gpt-5.6-luna` | 0.80 | 0.833 | 0.734 | 12.58 s | 48 | $0.0066 estimated |

Direct TypeSafe Jev reached 0.95 span recall and 0.933 span F1, matching the
strongest observed LLM result in this fixture. On the same routing indexes it
beat `tree-reason` quality, ran 3.0 times faster, and cost about 8.0 times less.
These measurements are too small and unreplicated for a general speed or
quality claim. The larger Codebase QA run above did not reproduce the quality
result.

## Experiments

### Index generation

The routing prompt asks for the questions each item directly answers and exact
entities, while excluding incidental linked topics. The implementation also
retries one empty model response and bounds a summary deterministically when a
model ignores the second length instruction.

An additional prompt that preserved every explicit join and SKU relationship
did not raise recall and lowered span F1 back to 0.883, so it was not retained.
The two remaining sample misses each require two source pages. For “How do
orders join to customers?”, Jev strongly preferred `orders.md` and pruned
`customers.md`. For the espresso-tonic SKU question, it followed a plausible
POS overview instead of assembling the recipe and orders pages. Preserving
relationships in the labels did not overcome the categorical branch choice;
the walk still does not perform link traversal.

### Classification and pruning

TypeSafe's direct Choice API initially over-selected `none_of_these` because a
directory summary is a route to evidence rather than the evidence itself. Its
provider adapter now states that directory options summarize descendants and
reserves none for queries unrelated to every option. Including the actual
frontmatter resource URL in metadata choices recovered resource-location
questions. Together these changes raised the direct TypeSafe run from 0.65
span recall and 0.667 span F1 to 0.95 and 0.933.

Opening branches more widely (`decision_min_probability=0.04`,
`decision_relative_probability=0.05`) raised span recall from 0.90 to 0.95,
but lowered span F1 from 0.883 to 0.850 through extra false-positive spans.
This is useful when recall is the priority; the defaults retain the better F1.

### Multi-hop limit

The dedicated multi-hop questions require following relationships between
pages. The current tree walk only descends the directory hierarchy, so prompt
tuning cannot fully solve that benchmark. Link traversal or another retrieval
stage is required.

## Cost and service caveats

The official [TypeSafe API](https://docs.typesafe.ai/api) is supported as
`typesafe/jev-latest`. It returns exact token usage; fastindex estimates query
cost using the current [Jev model price](https://docs.typesafe.ai/models). The
ten-query run used 19,512 input tokens and 1,842 output tokens. TypeSafe bills
input tokens, producing the $0.00082 estimate above. It requires
`TYPESAFE_API_KEY`.

The public [JevBench repository](https://github.com/fstandhartinger/jevbench)
is a broader independent decision-model benchmark. The linked
[jevbench.dev leaderboard](https://jevbench.dev/leaderboard) contains a small
set of application smoke checks and is not comparable with span retrieval.

## Reproduce

```bash
FASTINDEX_DECISION_MODEL=typesafe/jev-latest \
  uv run python -m evals.bench --strategies tree-decision
FASTINDEX_MAX_TOKENS=2048 uv run python -m evals.hybrid \
  --sources jev,bm25,fts --jev-k 8 --bm25-k 8 --fts-k 8 \
  --candidate-k 16 --file-k 4 --jev-rerank \
  --decision-min-probability 0.04 \
  --decision-relative-probability 0.05
uv run python -m evals.bench --strategies tree-decision \
  --fixtures evals/fixtures/queries/multi_hop.jsonl
```

Raw runs are under gitignored `evals/results/`. They are deliberately not
committed. The reported generated bundles are also local experiment artifacts.
