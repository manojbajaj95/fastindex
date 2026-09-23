# Hybrid retrieval studies

This note records the September 22, 2026 Codebase QA experiments that combine
Fastindex's TypeSafe Jev tree walk, BM25, FTS, and a Jev reranker. Our best
retrieval pipeline was **4.6x faster and 9.6x cheaper** than Pi's lowest-cost
OpenAI configuration in this study, `openai/gpt-5.6-luna`.

Retrieval is the system under study. We retain a separate `gpt-5.6-luna` answer
experiment as a downstream context-utility diagnostic. Results are single runs
over all 48 questions against Flask commit `85c5d93`. Raw runs remain local
under the gitignored `evals/results/` directory.

## System under test

The best measured pipeline is:

1. Walk the prepared directory tree with Jev using the recall-oriented `0.04`
   absolute and `0.05` relative branch thresholds.
2. Independently retrieve up to eight unique BM25 files and eight unique FTS
   files.
3. Form a pool of at most 16 files with reciprocal-rank fusion.
4. Send bounded path, index-summary, and lexical-excerpt descriptions to one
   TypeSafe System One request containing one Noul question per candidate.
5. Rank by Noul relevance score and return up to eight whole, line-numbered
   files under a 200,000-character cap. The downstream answer variant uses the
   best four files.

The reranker follows the pattern in TypeSafe's
[reranking cookbook](https://docs.typesafe.ai/cookbooks/rerank_typesafe): use
cheap retrieval to make a candidate set, then score candidate relevance. It
uses a single batched request because System One supports
[multiple primitives](https://docs.typesafe.ai/primitives) in one call. The
experiment was motivated by
[Steerable Reranking: How JEV Solves RAG](https://www.youtube.com/watch?v=UhGH8cNG0qs),
but the implementation is based on the published Noul API rather than claims
from the video.

## Pi retrieval comparison

The primary comparison measures retrieval quality, wall time, and model cost.
A query counts as complete only when the returned context overlaps every
curated gold span.

| Retriever | Complete context | Mean gold coverage | Time/query | Cost/query |
|---|---:|---:|---:|---:|
| Pi + `openai/gpt-5.6-luna` | **41/48 (85.4%)** | **0.932** | 19.40 s | $0.00656 |
| Jev + BM25 + FTS -> Jev Noul | 38/48 (79.2%) | 0.905 | **4.19 s** | **$0.00068** |

Across every query, the Fastindex hybrid was 4.63x faster and 9.66x cheaper.
It produced 38 complete contexts in about 201 seconds. Pi produced 41 in about
931 seconds. Dividing total retrieval time by complete results gives 5.29
seconds for the hybrid and 22.71 seconds for Pi, a 4.3x advantage for the
hybrid, with a 6.2-point lower complete-context rate.

Pi returned up to eight precise spans. The hybrid returned up to eight whole
files under the context cap. The hybrid won on retrieval time and cost while
passing more text to the downstream consumer.

## Retrieval ablations

Every row uses whole selected files and measures curated gold-span coverage
after the 200,000-character context cap. Lexical `k` means unique files, not
raw chunk hits. The all-source RRF row is computed from the exact candidate
pool of the following reranked row, so it is a controlled same-run comparison.

| Candidate and ranking stages | Gold coverage | File recall | Files/query | Time/query | Cost/query |
|---|---:|---:|---:|---:|---:|
| BM25@8 | 0.608 | 0.589 | 6.77 | **0.090 s** | **$0** |
| FTS@8 | 0.118 | 0.115 | 2.96 | 0.127 s | $0 |
| BM25@8 + FTS@8, RRF | 0.483 | 0.477 | 4.75 | 0.216 s | $0 |
| BM25@8 + FTS@8 -> Jev Noul | 0.736 | 0.720 | 5.75 | 1.64 s | $0.00045 |
| Jev + BM25@8 + FTS@8, RRF counterfactual | 0.698 | 0.688 | 4.94 | - | - |
| Jev + BM25@8 + FTS@8 -> Jev Noul | **0.905** | **0.899** | 6.29 | 4.19 s | $0.00068 |

The all-source pool had 0.920 file recall before selecting files for context.
Jev reranking retained 0.899, whereas unreranked RRF retained 0.688. The
reranker therefore recovered 0.211 file recall on the same candidate pool.

Tree candidates were complementary. Jev-reranked lexical candidates alone
reached 0.720 file recall; adding the tree candidates raised this to 0.899.
The tree is not merely an expensive duplicate of BM25 in this fixture.

Naive fusion did not work. BM25+FTS RRF reduced gold coverage from BM25's
0.608 to 0.483 because broad files ranked by both lexical methods displaced
more specific files. Candidate diversity only became useful after the Noul
relevance decision.

## End-to-end answer ablation

The answer score uses Codebase QA's 1 to 5 judge rubric normalized to 0 to 1. System
cost includes retrieval, reranking, and answering. It excludes the evaluation
judge and one-time index preparation.

| System | Answer score | Gold coverage | Files/query | Calls/query | Input/query | Cost/query |
|---|---:|---:|---:|---:|---:|---:|
| Plain Pi agent | **0.953** | - | - | 8.10 | 47,219 | $0.04696 |
| Jev route + BM25, wider routing | 0.870 | 0.799 | 3.85 | **3.96** | **28,977** | **$0.00562** |
| All-source Jev rerank, top 4 | 0.891 | 0.884 | 4.00 | 4.96 | 49,009 | $0.00792 |
| All-source Jev rerank, up to 8 | 0.891 | 0.899 | 6.31 | 4.98 | 63,985 | $0.01092 |

Top four is the best measured hybrid. It produced 39 full-score answers,
cost 83% less than Pi, and used 39% fewer model calls, while scoring 0.062
lower. Its input-token count was 4% higher than Pi's, so this is a model-mix
and interaction-count saving, not a token reduction.

Top eight also produced 39 full-score answers and exactly the same aggregate
score. The additional context raised cost by 38% relative to top four without
improving the measured answer result. The two rows are separate stochastic
runs, so this is evidence for preferring four files, not proof that four is a
universal optimum.

Compared with the older wide route-and-answer hybrid, top four improved score
by 0.021 and cost 41% more. The older system remains the better choice when
minimum cost matters more than the measured quality gain.

## Where the cost and time went

For the top-four run:

| Stage | Cost/query | Share of system cost | Time/query |
|---|---:|---:|---:|
| Jev tree route | $0.00023 | 2.9% | included below |
| Jev Noul rerank | $0.00046 | 5.8% | included below |
| All retrieval and reranking | $0.00068 | 8.6% | 4.36 s |
| `gpt-5.6-luna` answer | $0.00723 | 91.4% | 8.84 s |
| Total system | $0.00792 | 100% | 13.20 s |

The Jev stages are not the main cost. Answering dominates even though it is
only one call. The evaluation judge added $0.00044 and 4.39 seconds per query,
but it is not part of the proposed system.

## What worked

- Returning whole files fixed pure Jev's largest Codebase QA failure: reaching
  the correct file and then choosing the wrong fixed 80-line section.
- A tree candidate source plus lexical candidate sources increased pool recall
  to 0.920.
- One batched Noul call made that high-recall pool usable, improving same-pool
  file recall from 0.688 to 0.899.
- Four reranked files matched the eight-file answer score at substantially
  lower cost.
- The final architecture preserves a simple division of labor: decisions find
  files; the text model reads code and answers.

## What did not work

- FTS was weak alone, and equal-weight BM25+FTS RRF was worse than BM25 alone.
- Unreranked all-source RRF lost more than 0.21 file recall relative to Noul
  reranking on the same pool.
- More whole-file context did not improve the measured answer score.
- Complete retrieval was not sufficient. Among 37 questions with full gold
  coverage in the top-four run, mean answer score was 0.959 rather than 1.0.
  Questions 5 and 38 had all gold files but answer scores of 0.25 and 0.50.
- Missing context still mattered. Partial-coverage questions averaged 0.725;
  the one zero-coverage question scored 0.

## Recommendation

For context retrieval, use Jev tree routing, unique BM25 and FTS candidates, a
16-file fused pool, one batched Jev Noul rerank, and up to eight whole files
under the context cap. This is the best measured retrieval configuration.

For the separate downstream answer diagnostic, four reranked files were the
best measured quality/cost point; eight files produced the same answer score
at higher cost.

BM25 and FTS remain evaluation sidecars in `evals/`; Fastindex owns the tree
retriever. Pi remained 0.062 higher on the downstream answer score. That result
comes from one repository and one run per configuration.

## Scope and limitations

- The headline comparison covers 48 questions against one Python repository.
- Retrieval cost excludes one-time tree preparation. The prepared Flask bundle
  contains 52 generated indexes over 234 source files, but the original setup
  run did not capture complete preparation cost.
- The hybrid returns whole files, while Pi returns spans. Latency, cost, and
  gold coverage are directly measured; output context size is not equivalent.
- The tree does not follow symbol references or cross-file links after
  retrieval, so multi-hop assembly remains a weak point.
- Repeated runs and more repositories are still needed to measure variance and
  cross-language behavior.

## Open questions and next studies

1. Repeat the retrieval comparison and top-four answer run to estimate routing,
   answer, and judge variance.
2. Select two, four, or six files adaptively from Noul score margins instead of
   using a fixed cutoff.
3. After file selection, extract symbol definitions and matched neighborhoods
   rather than sending whole files.
4. Add a cheap groundedness check and invoke a second answer or broader context
   only when the first answer lacks direct evidence.
5. Compare Noul ranking with one Choice question over the same candidate pool,
   holding candidate text constant.
6. Test source weighting and remove FTS if it does not add candidate-pool recall
   across more repositories.
7. Repeat on other languages and repository layouts, and measure preparation
   cost well enough to calculate a break-even query count.

## Reproduce

Retrieval-only comparison:

```bash
FASTINDEX_MAX_TOKENS=2048 uv run python -m evals.hybrid \
  --sources jev,bm25,fts --jev-k 8 --bm25-k 8 --fts-k 8 \
  --candidate-k 16 --file-k 8 --jev-rerank --retrieval-only \
  --decision-min-probability 0.04 \
  --decision-relative-probability 0.05 \
  --out evals/results/codebase-qa-hybrid-reranked-retrieval-r1.json
```

This run requires `TYPESAFE_API_KEY` and the prepared Flask bundle. Remove
`--retrieval-only`, set `--file-k 4`, and provide `OPENAI_API_KEY` plus the
sibling `agent-learning-bench` checkout to reproduce the answer diagnostic.
