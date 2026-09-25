# Code retrieval study: tree navigation plus exact and lexical search

September 24, 2026. Research preview with development-set and held-out
file-localization experiments; not a production benchmark. **Exploratory pilot
closed:** further tuning belongs in a separately frozen, broader study.

## Pickup summary (September 24)

This is a retrieval study, not an answer-quality or production benchmark.
The branch is `research/code-retrieval-study`; evaluation scripts and this doc
are uncommitted. Pinned external corpora and run JSON live in gitignored
`evals/fixtures/external/` and `evals/results/`. Keep the three task metrics
separate: Flask and trace2code have span/line labels, while edit2ripple has
file labels only.

| Track | Worked | Did not work / limit |
|---|---|---|
| Flask QA, 48 questions (development set) | Tree+BM25+FTS+Noul returned 0.905 mean gold-span overlap, 38/48 complete, 4.19 s and $0.00068 retrieval/query. Adding `rg`@8 to frozen source rankings raised overlap to 0.936 and complete to 41/48. | Pi's precise spans were stronger (0.932, 41/48) but took 19.40 s and $0.00656/query. Hybrid whole files had only 0.0098 line precision versus Pi's 0.442. Replacing every lexical preview with match windows regressed to 0.889 overlap. Flask tuning is not held-out evidence. |
| ARB trace2code, 101 questions (file localization) | Name-only tree recall@8 was 0.517 versus literal search's 0.317; tree+literal RRF reached 0.535@8 and 0.599@16 in one run. The sources find different files. | On verified full-source RRF@8 candidates with distractors and a 16K-token cap, whole files retained every labeled gold line on 28/101 questions; merged ±20-line hits retained 21/101. A higher span-*overlap* score for small windows did not mean complete evidence. |
| ARB edit2ripple, 58 questions (file labels only) | Full anchor+intent+diff lexical+path recall@8 was 0.454 (18/58 complete). Relaxed name-only tree scored 0.399/0.391 in two runs; RRF with lexical scored 0.545/0.547 (24/58 complete), and 0.586@16 (26/58). | Path-neighbor alone scored 0.128@8 (4/58); aggressive tree pruning scored 0.086 (3/58) and returned no files on 27 questions. Intent alone was weaker than using the diff. No line gold means this track cannot validate span packing. |
| Flask tree-menu ablation, 48 questions, two runs | Prepared summaries raised file recall@8 from 0.755/0.783 to 0.797/0.818 with the same tree, model, thresholds, and questions; routing needed about half a model call less per query. | Complete gold-file sets were nearly unchanged: 32/48 vs 31/48, then 33/48 vs 33/48. Prepared menus used about 67% more input tokens, and their generation cost is unrecorded. This is file routing, not final source-span retrieval. |
| ARB trace2code, matched full-source context, 101 questions | At 16K tokens, path-only tree retained all labeled gold lines on 26 questions versus 8 for literal search and 11 for BM25; tree+literal RRF reached 28. | The tree found all gold files on 49 questions but lost required lines during whole-file packing on 23 of them. Click had zero complete-line contexts for tree-only despite eight complete gold-file sets. This is a post-exploration check on an imbalanced corpus, not an independent held-out result. |
| ContextBench pilot, 24 frozen cases from two new repositories | Name-only tree found 0.476 of gold files at eight candidates, versus 0.266 for full-issue BM25. At the same 16K whole-file cap, tree retained 0.362 of gold lines versus 0.190 for BM25. | SQLite FTS5 using the first 500 query characters reached 0.281 line recall and the same 4/24 complete contexts as tree. Gains were much larger on Django than Svelte; gold text does not perfectly align with base commits. This is a promising pilot, not a general win. |
| ContextBench descendant-name ablation, paired 24 cases | Showing 12 deeper names raised candidate gold-file recall from 0.462 to 0.501 versus direct child names only. | At 16K, delivered gold-line recall moved only 0.370 to 0.377, with 5/24 complete contexts either way. Query cost rose from $0.000255 to $0.000368; a full fresh prepared index would require at least 188,153 model calls across snapshots, so we did not run it. |

New Flask one-pass result: with frozen Noul top-eight files and ±200-line
windows, sequential 32K-token input packing gave Luna complete gold lines on
29/48 questions, versus 27/48 with round-robin packing. The model still
dropped needed lines on three questions and cost another $0.00777/query; a
match-centered Noul preview after search did not beat file-level Noul in the
final context on 46 paired questions and returned HTTP 403 twice.

What to carry forward: cheap lexical discovery and a *broad enough* tree walk
can complement each other, but file recall is only the first gate. The new
independent-repository pilot supports studying tree routing further, while
showing that full evidence assembly remains unsolved. Do not make
±20 merging, a particular tree threshold, RRF, or whole-file delivery the
product default from these exploratory runs. A future paper-grade study has a
separate [decision gate](#future-study-and-decision-gate). The detailed
tables, setup/cost notes, caveats, and reproduction commands follow below.

**Current paper scope:** study `tree-decision` as a code-retrieval method,
including its path-only and prepared-summary variants, against simple lexical
and existing retrieval controls. The prior hybrid, window-packing, and LLM
selection runs remain diagnostic background, not a second proposed system or
a requirement for this paper. PageIndex is related architectural prior art,
not an evaluation target.

## Working conclusion

A repository's directory tree is a useful routing signal, especially when a
question describes a component rather than naming a symbol. It is not a complete
retrieval index: tests, configuration, callers, and implementations often live
in different branches. Nor is a model-driven tree walk automatically fast. The
paper question is whether **probabilistic tree routing finds gold code evidence
that cheap search misses**, at an acceptable setup and query cost. Keep it as
one retrieval component only if that gain survives held-out repositories and
fixed context budgets.

### PageIndex as architectural prior art

[PageIndex](https://github.com/VectifyAI/PageIndex) uses a hierarchical index
and model-guided navigation for long documents. That makes tree-guided retrieval
a credible design pattern, **not** an empirical result for source code or a
baseline that this study needs to beat. Repositories already have a tree, but
callers, tests, configuration, and implementations often cross directory
branches. The code-specific question is whether a bounded tree walk finds
more *annotated source evidence* than low-cost search after identical context
limits, including its setup and query cost. The pilot below says this is worth
testing at larger scale; it does not establish a general win.

### Prepared summaries versus inherited path names

We isolated the value of generated `index.md` descriptions in `tree-decision`.
Both variants walk the same prepared Flask tree at commit `85c5d93`, use the same
TypeSafe `jev-1.13.0` model, questions, top-eight *whole-file* output,
0.04/0.05 branch thresholds, and 32-call cap. The control replaces each index
menu with child path names; both variants still expose up to 12 descendant
names per directory through the existing router. Order alternates by question.

| Run | Menu | Mean gold-file recall@8 | All gold files | Calls/query | Input tokens/query | Estimated cost/query | Mean / p95 time |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | Names only | 0.755 | 31/48 | 3.44 | 3,227 | $0.000136 | 3.52 / 5.61 s |
| 1 | Prepared summaries | 0.797 | 32/48 | 2.98 | 5,419 | $0.000228 | 3.03 / 4.84 s |
| 2 | Names only | 0.783 | 33/48 | 3.52 | 3,288 | $0.000138 | 3.58 / 5.86 s |
| 2 | Prepared summaries | 0.818 | 33/48 | 2.98 | 5,459 | $0.000229 | 3.03 / 5.14 s |

Prepared summaries improve mean file recall by 3.5–4.2 percentage points in
these two runs, but only one additional question has a complete gold-file set
across 96 paired queries. Prepared wins on eight/six questions, names-only on
five/four, and they tie on 35/38. The two menus select different path lists on
41/42 questions, so summaries do change routing. Across repeated runs *of the
same menu*, paths changed on 21/48 name-only and 12/48 prepared questions,
while gold-file recall changed on only two and three questions respectively.
There were no errors or budget truncations. The prepared variant reads more
tokens but makes fewer calls and was faster on this provider in both runs;
its per-query cost is about 67% higher, *excluding* the unrecorded preparation
cost. This is a modest signal for descriptions, not yet an amortization case or
proof that the tree retrieves the right lines. The name-only control remains a
credible low-setup baseline; do not require generated summaries by default
without a held-out file-and-span benefit.

Reproduce with `uv run python -m evals.tree_index_ablation`; use `--out` for
each repeat. Local, gitignored artifacts:
`evals/results/flask-tree-index-ablation.json` and
`evals/results/flask-tree-index-ablation-repeat2.json`. Next, repeat the
paired comparison on pinned repositories with annotated line evidence, record
cold index-generation time/cost and update cost, then apply the same final
token budget to both variants.

## What our runs actually show

The table below reports single-run retrieval measurements on 48 curated questions
against Flask commit `85c5d93`. The prepared corpus has 234 source files, 52
directories, and 52 generated `index.md` files (about 60 KB total). The complete
preparation cost was not recorded. Mean gold coverage counts a question's
annotated source spans that overlap returned context; complete context requires
*every* gold span. See [the original study](hybrid-evaluation.md),
[tree-decision evaluation](tree-decision-evaluation.md), the frozen
[questions](../evals/fixtures/queries/codebase_qa.jsonl), and local, gitignored
`evals/results/` artifacts.

| Retriever and output | Mean gold coverage | File recall | Complete contexts | Time/query | Model cost/query |
|---|---:|---:|---:|---:|---:|
| BM25@8, whole files | 0.608 | 0.589 | 22/48 | 0.090 s | $0 |
| Jev tree-decision, selected spans | 0.391 | 0.668 | 8/48 | 3.68 s | $0.00030 |
| Tree + BM25@8 + FTS@8, RRF, whole files | 0.698 | 0.688 | — | — | — |
| Same sources, 16-file pool + batched Noul rerank, whole files | **0.905** | **0.899** | **38/48** | **4.19 s** | **$0.00068** |
| Pi agent, selected spans | **0.932** | 1.000 | **41/48** | 19.40 s | $0.00656 |

The RRF row is a same-pool counterfactual from the reranked run; its timing and
cost were not measured separately. The hybrid is 4.63x faster and 9.66x cheaper
than Pi *for retrieval only*, with three fewer complete contexts. Pi returns
precise spans while the hybrid returns whole files under a 200,000-character
context cap, so these are not equivalent context payloads. The four-file
downstream answer run spent $0.00068/query on retrieval and $0.00723/query on
answer generation; its judged answers are a separate diagnostic, not a
retriever score.

Pi is a fresh `gpt-5.6-luna` agent allowed `read`, `grep`, `find`, and `ls`,
not a single raw grep call. Its saved run averaged 5.63 model calls, 10.54 tool
executions, and 51,196 reported input/cache-read tokens per question; it
returned about 3,365 characters of selected source spans. The hybrid averaged
3.96 TypeSafe calls and 16,181 input tokens, then delivered 184,074 characters
of whole-file context. Pi cost comes from its reported model charges; hybrid
cost is estimated from TypeSafe's input-token rate. The 4.63x/9.66x ratios
compare these *systems and model prices*, not tree search against grep. Pi's
saved results count tool calls but do not preserve each call's tool name or
search query. Pi timing includes the subprocess and its tool loop; the hybrid's
retrieval timer stops before final line-numbered context packing.

The same 48 saved rows show the evidence-density difference more clearly:

| Output | Span recall | Span precision | Span F1 | Line recall | Line precision | Line F1 | Final source characters/query |
|---|---:|---:|---:|---:|---:|---:|---:|
| Pi spans | 0.932 | 0.464 | 0.593 | 0.797 | 0.442 | 0.523 | 3,365 |
| Hybrid whole files | 0.905 | 0.221 | 0.346 | 0.916 | 0.0098 | 0.019 | 184,074 |

These are means of per-question metrics, not an F1 calculated from the displayed
mean precision and recall. A returned *whole file* counts as one span hit if it
overlaps any gold line, even if most of that file is irrelevant. Line precision
captures that difference: the hybrid covers many gold lines, but spends far
more context on unrelated lines. Pi is the stronger precise-section retriever
in this run.

An illustrative fresh replay of q002 used Pi's `grep` tool first with
`registered|registration|setup` over `.`, then `find` over `**/*`, then a
targeted `grep` for `_got_registered_once|deferred_functions|record_once|register\(`
in `src/flask/sansio/blueprints.py`, followed by `read` of lines 1–260 and two
more targeted greps. Pi's installed `grep` tool invokes `rg` with JSON and
line-number flags; `bash` was not among the tools allowed by this evaluation.
This replay demonstrates the search pattern, but it does not reconstruct the
original 48 runs' individual tool calls.

An audit of the saved `hybrid-ablation-jev-bm25-fts-noul.json` trace against the
frozen gold files localizes the remaining loss:

| Stage | Mean gold-file recall | Questions with every gold file |
|---|---:|---:|
| 16-file candidate pool | 0.920 | 40/48 |
| Noul top-eight ranking, before context packing | 0.920 | 40/48 |
| Whole files after the 200,000-character cap | 0.899 | 38/48 |

Eight questions already missed a required file in the candidate pool. For
example, q001 missed `tests/test_json.py`, and q047 missed both `pyproject.toml`
and `src/flask/typing.py`. Noul retained every available gold file within its
top eight on this run. Two further questions, q012 and q016, lost a selected
gold file during whole-file packing. At least one selected file was dropped by
the cap on 43/48 questions; mean delivered context was 184,074 characters.
This makes candidate discovery and span extraction the immediate bottlenecks,
not another reranker prompt tweak. The earlier pure Jev run also reached
relevant *files* more often than relevant fixed 80-line *sections* (0.668 versus
0.391 recall).

An offline packing ablation replayed the saved Noul top-eight file ranking,
without new model calls. The opt-in `evals.hybrid --context-mode windows` mode
keeps files of at most 1,200 lines whole; in larger files it finds 80-line
blocks containing distinct non-stopword query terms and expands the best three
nonoverlapping matches to 160-line windows. A large file with no term match
falls back to the whole file. Both variants use the same 200,000-character cap.

| Packing on the same ranked files | Mean gold-span coverage | Complete contexts | Mean context characters | Mean line precision |
|---|---:|---:|---:|---:|
| Whole files | 0.905 | 38/48 | 184,074 | 0.0098 |
| Find + expand for large files | 0.913 | 39/48 | 174,973 | 0.0107 |

Find + expand recovered q012 and q016, whose relevant selected files had been
dropped by whole-file packing, but lost q046 because its lexical anchors were
elsewhere in a large file. Replacing simple term matching with BM25 over the
same 80-line blocks yielded 0.920 coverage and 40/48 complete contexts at
174,009 characters in a separate offline check; that variant is not the
implemented default. Applying windows to *every* file performed markedly worse
(0.755 coverage for two 240-line term-matched windows per file). The mixed
rule is therefore a useful cheap control, not a proven best extractor. These
are development-set, retrieval-only replay results; they omit answer quality,
new model runs, and held-out repositories. The 1,200-line cutoff was inspected
on this same set and should not be treated as validated tuning.

Across 77 query/gold-file pairs in this trace, 20 gold files appeared in the
Jev source ranking but neither lexical ranking; one was BM25-only and two were
FTS-only. Seven appeared in none of the three source rankings. One of the two
FTS-only files was then cut from the 16-file pool. This is a description of
one candidate trace, not a leave-one-source-out quality result; it does show
why the tree and source diversity deserve controlled tests.

A second offline candidate ablation used literal, case-insensitive `rg` terms
over the same 221 searchable Flask files. It searched all non-stopword query
terms in one ripgrep call per question, ranked files by distinct matched-term
rarity, and optionally boosted terms in the path. The existing Jev/BM25/FTS
rankings and 16-file RRF cap were held fixed; no new model calls were made.

| Candidate pool | Mean gold-file recall | Questions with every gold file |
|---|---:|---:|
| Jev + BM25@8 + FTS@8 | 0.920 | 40/48 |
| Same + `rg`@4, path boost | 0.920 | 40/48 |
| Same + `rg`@8, no path boost | 0.951 | 43/48 |
| Same + `rg`@8, path boost | **0.962** | **44/48** |
| Same + `rg`@16, path boost | 0.962 | 44/48 |
| BM25@8 + FTS@8 + `rg`@8, no tree | 0.814 | 33/48 |

At `rg`@8, the path-boosted pool gained q001, q011, q017, and q025 with no
pool losses on this fixture. Three gains were test files absent from all
original source rankings; q011's file was already ranked but had been cut by
the pool cap. The one-call ripgrep search averaged 21.8 ms/question in this
local run (48 questions, one repetition). These are *candidate-pool* results,
not final span coverage or Noul rerank results. A better pool is useful only
if ranking and context packing preserve those files. The reproducible local
check is `uv run python -m evals.rg_ablation`; its detailed result is written
to gitignored `evals/results/rg-candidate-ablation.json`.

We then replayed *identical saved tree/BM25/FTS source rankings* through Noul,
with and without `rg`@8, using the same 1,600-character candidate descriptors,
top-eight file limit, and 200,000-character context cap. This isolates the
candidate-source change from variation in the tree walk. Both variants made
one new batched Noul call per question and completed without provider errors.

| Frozen-source rerank and packing | Mean gold-span coverage | Complete contexts | Mean context characters | Mean Noul cost/query |
|---|---:|---:|---:|---:|
| Jev + BM25 + FTS | 0.905 | 38/48 | 184,688 | $0.000293 |
| Same + `rg`@8 | **0.936** | **41/48** | 183,623 | $0.000296 |
| Same + `rg`@8, replacing all lexical previews with matched windows | 0.889 | 39/48 | 183,274 | $0.00031 |

The `rg` gains were q001, q017, and q025: JSON test files newly present in
the candidate pool survived reranking and packing. q011's additional gold
file entered the pool but ranked below the final eight. A matched-line preview
fixed q011 in isolation, yet replacing previews for *all* `rg` files regressed
five other questions, so blanket preview replacement is rejected. The replay
measures only Noul time/cost and packing, not a full new tree walk; `rg` adds
about 22 ms of local search per query on this corpus. A separate live pair
at the same descriptor size scored 0.884 without `rg` and 0.951 with it, but
Jev routes differed on 14/48 questions, so that larger delta cannot be
attributed solely to `rg`. All of these remain single-run Flask development
results, not held-out validation or downstream answer scores. Reproduce the
frozen rerank with `uv run python -m evals.rg_rerank_ablation`; local result
JSON stays under gitignored `evals/results/`.

The simplest wider-context variant is now measured on the same frozen
`base+rg` Noul file rankings. It keeps files at most 1,200 lines whole; in
larger files it finds the three best nonoverlapping lexical 80-line blocks and
expands each to the specified width. The file ranking and 48 Flask questions
stay fixed. The caps below are **characters, not tokens**; these are offline
packing replays with no additional model calls.

| Final character cap | Whole files: span recall / complete | 3 x 80-line windows | 3 x 160-line windows | 3 x 640-line windows |
|---|---:|---:|---:|---:|
| 64,000 | 0.743 / 29 | **0.821 / 33** | 0.788 / 32 | 0.778 / 31 |
| 128,000 | 0.877 / 37 | **0.939 / 41** | **0.939 / 41** | 0.905 / 39 |
| 200,000 | 0.936 / 41 | 0.944 / 42 | 0.944 / 42 | **0.951 / 43** |

At the large cap, 640-line expansion recovered q012 and q016 without a
per-question span-recall loss relative to whole files; mean final context was
184,414 versus 183,623 characters, so this is primarily a coverage gain, not
a compression gain. Local packing averaged 3.6 ms versus 1.2 ms for whole
files, excluding the shared file-ranking stage. At smaller caps, narrow windows won more often because
wide windows crowded out later files. The variant was tuned on Flask and has
no held-out *end-to-end* span validation. Reproduce with
`uv run python -m evals.flask_packing_ablation`; its local result is
`evals/results/flask-packing-ablation.json`. The context builder now enforces
the character cap even when the first ranked file alone exceeds it.

The current `fts` adapter is a term-frequency count with a path boost over
in-memory documents, not a database full-text index. BM25 uses both whole-file
and section documents. The `rg` candidate control is measured above; `git grep`
has not been tested. The dedicated multi-hop fixture confirms that parent-to-child
tree traversal cannot follow a relationship into another branch by itself.

### Held-out failure-trace lexical check

We verified the SHA-256 checksum of [Agent Retrieval Bench's public
`v2_trace2code` release](https://huggingface.co/datasets/eyuansu71/agent_retrieval_bench/tree/main/releases/v2_trace2code)
and evaluated its 101 positive failure-trace
questions over 98 frozen snapshots in seven repositories. Gold is the release's
`root_cause_files`, and the query is its full `failure_excerpt` (median 1,476
characters). This check reads the release's **truncated file chunks** and
scores case-insensitive literal term containment. BM25 is a simple file-chunk
control over the same terms, not ARB's published implementation. The release
is usable for file-label tests but not a whole-source span-packing test by
itself: 89
of 189 annotated gold spans, affecting 56/101 questions, lie beyond their
corresponding truncated file chunk. For example, a Caddy gold span is at lines
527–536, but the released `listeners.go` file chunk ends at line 242. We
discarded the invalid span-packing replay rather than report those misses as
retriever failures.

| Held-out file ranking, sample-weighted | Gold-file recall@8 | Gold-file recall@16 | Complete gold sets@16 |
|---|---:|---:|---:|
| Distinct literal terms + rarity | 0.317 | **0.556** | **51/101** |
| Same + path bonus | **0.342** | 0.545 | 50/101 |
| Whole-file BM25 | 0.264 | 0.373 | 34/101 |
| BM25@8 + literal/path@8, RRF | 0.305 | 0.413 | 37/101 |

The path bonus is not uniformly helpful: on `pallets/click` (26 cases), it
raised recall@8 from 0.327 to 0.423; on `gin-gonic/gin` (56 cases), it slipped
from 0.393 to 0.384. One Caddy failure trace names `listeners_test.go` while
the root cause is `listeners.go`; the path bonus pushed that source file from
rank 12 to below 16. The six Tokio cases had zero recall@8 with both literal
variants. These are materially different from Flask's curated natural-language
QA prompts, so Flask-tuned path weighting and equal-weight fusion should not
be promoted as general rules. The corpus is sample-imbalanced (Gin contributes
56/101); report per-repository results alongside the pooled table. Reproduce
with `uv run python -m evals.arb_trace_ablation` after downloading the release;
the result is local `evals/results/arb-trace-ablation.json`.

The same literal rule was run through **actual one-call `rg --json -i -F -f -`**
over materialized release chunks. Its per-file matched-term sets agreed with
the in-memory check on all 101 questions; mean search time was 56 ms,
p50 39 ms, p95 164 ms on this machine, excluding corpus materialization and
Python ranking. Reproduce with `uv run python -m evals.arb_rg_runtime` after
materializing the path-only trees below. The local result is
`evals/results/arb-rg-runtime.json`.

A separate model-backed test reconstructed the released paths into a directory
tree and generated name-only `index.md` menus—no model-generated summaries.
In two runs with the same generated menus, `tree-decision` returned at most
eight files per question, using the first 500 characters of each failure trace.
It reached 0.517/0.512 mean gold-file recall and all gold files for 48/47 of
101 questions, versus literal search's 0.317 recall@8 and 29/101 complete
sets. Median tree time in the first run was
1.15 s (p95 3.98 s), with 2.37 model calls and an estimated $0.000136 per
query; path-tree materialization averaged 73 ms per snapshot/question in this
warm local run. With the same eight-file output cap, equal-weight RRF of
tree@8 and literal@8 reached 0.535/0.545 recall and 50/51 complete sets in
the two runs, versus tree alone's 0.517/0.512 and literal alone's 0.317.
At a 16-file cap, the fused pool reached 0.599/0.604 recall and 57/58
complete sets, versus literal@16's 0.556 and 51. Fusion itself is an offline
replay; its query cost includes both tree routing and literal search. RRF@8
regressed Tokio's six cases from 0.500 tree recall to 0.333, so the pooled
gain is not uniform across repositories. Selected
file sets differed on 29/101 questions between identical-menu runs, though
gold recall changed on only six. This is a warning to repeat model routing,
not a confidence interval. Neither run used full
repository source files or tested final evidence spans. The generated code
trees intentionally lack OKF frontmatter, so product `fastindex lint` reports
source-Markdown frontmatter errors; the generated index links themselves were
checked to have no dangling targets. Reproduce with
`uv run python -m evals.arb_tree_ablation --trees
evals/fixtures/external/arb-trace2code/path-only-trees-v2 --out
evals/results/arb-tree-path-only-v2.json`, followed by
`uv run python -m evals.arb_fusion_ablation`.

### Held-out full-source span check, with oracle files

To repair that corpus limitation, we fetched all 124 distinct gold files at
their pinned GitHub base commits. Every file matched the released chunk prefix,
covered its annotated gold lines, and has a SHA-256 recorded in the local
`evals/results/arb-full-source-gold.json` manifest. This test gives the packer
the *correct files*; it isolates line localization and cannot be read as
end-to-end retrieval. It uses the full failure trace and `cl100k_base` token
counts including file headers and line numbers. The fixed-window controls take
at most three lexical windows per file. “Mixed” keeps files of at most 1,200
lines whole; “all” windows every file.

| Oracle-file packing | 8K: span recall / complete | 16K | 32K |
|---|---:|---:|---:|
| Whole gold files | 0.410 / 35 | 0.673 / 63 | 0.828 / 81 |
| All files, 3 x 160-line windows | **0.724 / 66** | 0.744 / 69 | 0.744 / 69 |
| Mixed, 3 x 320-line windows | 0.582 / 51 | **0.856 / 80** | **0.882 / 84** |

At 16K, mixed 320-line windows beat whole files on 22 questions and lose on
four. Mean line recall rose from 0.669 to 0.849, while mean line precision
remained low (0.063 to 0.072): most included lines are still not labeled
evidence. The best width depends on budget, and 21/101 questions still lack
complete spans even with oracle files at 16K.

We also tested the simpler rule: choose up to 80 lexical hit lines **per gold
file** from the failure trace, expand each by ±20/40/80 lines, sort the resulting
line intervals, and merge overlapping or touching intervals within that file.
The token packer then considers each merged interval as one block. Here
“complete spans” means every gold span has *some* overlap; “complete lines”
means every labeled gold line is present. Both counts are out of 101 questions.

| Oracle-file packing | Budget | Mean span-overlap recall / complete spans | Mean gold-line recall / complete lines | Mean context tokens |
|---|---:|---:|---:|---:|
| Whole gold files | 16K | 0.673 / 63 | 0.669 / 63 | 4,925 |
| Mixed, 3 × 320-line windows | 16K | 0.856 / 80 | 0.849 / 79 | 8,120 |
| Merge 80 hits, ±20 lines | 16K | **0.896 / 84** | 0.827 / 57 | 8,192 |
| Merge 80 hits, ±40 lines | 16K | 0.876 / 85 | **0.863 / 78** | 8,619 |
| Merge 80 hits, ±80 lines | 16K | 0.824 / 77 | 0.823 / 77 | 8,020 |
| Mixed, 3 × 320-line windows | 32K | 0.882 / 84 | 0.882 / 83 | 8,670 |
| Merge 80 hits, ±20 lines | 32K | 0.916 / 87 | 0.847 / 59 | 8,880 |
| Merge 80 hits, ±40 lines | 32K | 0.933 / 93 | 0.920 / 85 | 10,915 |
| Merge 80 hits, ±80 lines | 32K | **0.964 / 96** | **0.957 / 94** | 12,296 |

The ±20 rule is fast and finds many spans, but its overlap score hides clipped
evidence: at 16K, only 57 questions retain *all* labeled lines versus 79 for
the 320-line control. Forty-one of the 189 gold spans are longer than 41 lines,
so one isolated ±20 hit cannot cover them. Wider expansion helps when the
budget allows it, but at 16K ±80 crowds out useful intervals. The merged
variants and hit count were explored on these same 101 questions, so this is
an ablation, **not** an independently validated winner. The next test needs
real candidate files, including false positives, plus symbol boundaries and
better hit scoring. Reproduce with
`uv run python -m evals.arb_full_source` and
`uv run python -m evals.arb_oracle_span_ablation`; source and results are
gitignored. The next section adds full *candidate* files and false positives
to the fixed-token packing comparison.

### Full-source candidate packing, including distractors

We next kept the earlier tree+literal RRF **top-eight file order fixed** and
fetched all 800 distinct selected file versions across the 101 questions.
Each matched its pinned release chunk; nine chunks omitted leading blank
lines, which verification permits while preserving the raw source line numbers.
The 19.6 MB of verified source and SHA-256 manifest are local and gitignored at
`evals/results/arb-full-source-rrf8.json`. File discovery still used the
release's truncated chunks; this replay replaces only the *packing input*
with full source. It is therefore a frozen-candidate, offline packing test,
not a live end-to-end retriever or answer-quality test. Three questions have
root-cause files without annotated line spans, so the line metrics score only
their annotated evidence. The selected pool covers all annotated gold files
on 51/101 questions (mean annotated-file recall 0.536).

| Frozen RRF@8 packing | Budget | Mean span-overlap recall / complete spans | Mean gold-line recall / complete lines | Mean context tokens |
|---|---:|---:|---:|---:|
| Whole files | 16K | 0.318 / 28 | 0.321 / **28** | 13,838 |
| Mixed, 3 × 80-line windows in large files | 16K | 0.352 / **32** | 0.324 / 26 | 14,870 |
| Merge 40 hits/file, ±20 lines | 16K | **0.362** / 29 | **0.330** / 21 | 15,773 |
| Whole files | 32K | 0.397 / 36 | 0.400 / 36 | 28,470 |
| Mixed, 3 × 160-line windows in large files | 32K | **0.444 / 41** | 0.425 / 37 | 29,452 |
| Mixed, 3 × 320-line windows in large files | 32K | 0.438 / 40 | **0.442 / 40** | 30,367 |
| Merge 80 hits/file, ±80 lines | 32K | 0.394 / 37 | 0.396 / 37 | 30,738 |

The oracle-file result does **not** survive distractors as a general win for
small merged windows. At 16K, the best ±20 variant slightly improves mean
overlap and line recall over whole files, but loses seven questions on
*complete* gold-line coverage. Relative to whole files, its per-question
line-recall wins/losses are 13/20; the other 68 tie. Skipping candidate files
with no lexical hit barely changes these results. At 32K, mixed windows beat
the tested merge settings. File localization is the first ceiling (only 51
questions have all annotated gold files in the top eight), but packing still
fails on 23 of those 51 at 16K with whole files. The next controlled tests
should improve candidate ordering and hit scoring on this **same** pool before
claiming a best window size; stronger file discovery needs a separate test.
Reproduce with `uv run python -m evals.arb_full_source --fusion
evals/results/arb-fusion-ablation.json` and `uv run python -m
evals.arb_oracle_span_ablation --fusion
evals/results/arb-fusion-ablation.json`.

One same-pool ordering control already warns against a naive shortcut:
reranking those eight full candidate files with BM25 over unique,
stopword-filtered failure-trace terms took another 6.5 ms/query locally and
reduced 16K whole-file mean gold-line recall from 0.321 to 0.177, with
complete-line questions falling from 28 to 15. Paired line-recall wins/losses
against frozen RRF order were 2/17. This does not discredit BM25 as a *source*
of candidates; it says whole-file BM25 is a poor late ranker for this long
trace workload and fixed pool. Reproduce with the same packing command plus
`--file-order bm25`; its local result is
`evals/results/arb-candidate-span-rrf8-bm25.json`.

For match-line scoring, we held the frozen file order fixed and replaced the
distinct-query-term count with an IDF weight computed from line frequency
*within each candidate file*. With ten ±20-line anchors/file at 16K, mean
span-overlap recall rose from 0.315 to 0.350 and complete-line questions from
8 to 12. At 40 anchors/file, however, mean overlap fell from 0.362 to 0.307
and complete-line questions from 21 to 14; paired line-recall wins/losses
were 5/13. At 32K with 40 anchors, the overlap difference was only +0.005
and complete-line count stayed 23. Rare-term weighting helps a tight anchor
quota but is not a robust improvement to the best-tested packing setting.
This is line-level IDF, **not** the chunk BM25 method in *Better Call Grep*.
Reproduce with the same packing command plus `--hit-score idf`; results are
local at `evals/results/arb-candidate-span-rrf8-idf.json`. All these packing
choices were explored on the same 101 questions and still need independent
confirmation before selecting defaults.

A direct-match diagnostic helps separate vocabulary from ranking: 157/189
annotated gold spans contain at least one non-stopword failure-trace
term. That rises to 183/189 when checking the span plus ±20 lines, 187/189
at ±40, and 189/189 at ±80 in the verified gold files. This is *not* a
retrieval score—the nearby term can rank too low, its file can be absent, or
the expanded interval can be dropped by the token cap—but it shows that
strict exact-line hits would miss some relevant code while wider local
context can bridge many of those misses.

Candidate depth alone has limited payoff under this packer. Extending the
*same frozen RRF ranking* from eight to sixteen candidates required 224 more
distinct pinned source versions (1,024 total, all verified; 23.9 MB). Mean
annotated-file recall rose from 0.536 to 0.601, and complete candidate file
sets from 51 to 58/101, with no new ranking-model call. At the **same 16K
token cap**, whole-file complete-line questions rose only 28→29; mixed
3 × 80-line packing rose 26→28, with mean line recall 0.324→0.344 and mean
context length 14,870→15,241 tokens. Local select-and-pack time rose from
about 11.5 to 14.5 ms/query, excluding the shared candidate-generation stage.
Only two of the seven newly complete candidate sets gained complete line
evidence; the other five still lost it
in packing. At 32K, mixed 3 × 160-line complete-line count remained 37 and
mixed 3 × 320-line count remained 40. This points to candidate *ordering and
budget allocation*, not merely a larger candidate list. Reproduce the depth
control with `uv run python -m evals.arb_full_source --fusion
evals/results/arb-fusion-ablation.json --candidate-k 16` followed by
`uv run python -m evals.arb_oracle_span_ablation --fusion
evals/results/arb-fusion-ablation.json --candidate-k 16`; the local result is
`evals/results/arb-candidate-span-rrf16.json`.

We then kept those same selected intervals but changed only packing order:
round-robin offers one interval from each file in candidate-rank order before
offering a second. On RRF@8 at 16K, mixed 3 × 320-line windows improved mean
gold-line recall 0.305→0.331 and complete-line questions 27→30 (paired wins /
losses 5/3). RRF@16 reached the same 30 complete-line questions, versus 28
with sequential packing. At 8K, all-file 320-line windows rose from 11 to 18
complete-line questions on RRF@16. But round-robin is not a universal win:
the RRF@8 mixed 320-line control **fell** from 40 to 36 complete-line
questions at 32K (RRF@16: 40→37). A first interval from every file can
displace a second relevant interval when there is enough budget. Keep this
as a tight-budget control, not a default without a budget- and query-stratified
decision rule. Reproduce by adding `--pack-order round-robin` to the packing
command; local results are `evals/results/arb-candidate-span-rrf8-round-robin.json`
and `evals/results/arb-candidate-span-rrf16-round-robin.json`.

### Matched tree, lexical, and fusion context under a fixed budget

We now replayed the saved path-only `tree-decision`@8, literal@8, BM25@8,
and tree+literal RRF@8 rankings through the *same* full-source whole-file
packer. All selected files were verified against their pinned release chunks
and source SHA-256, including 801 distinct BM25-selected file versions (some
already cached). The packer uses `cl100k_base` token counts and preserves
frozen candidate order. These
are maximum budgets, not forced output sizes. Tree routing often returns fewer
than eight paths (mean 3.34); the other rankings always return eight. Each
row uses the same 101 trace2code questions and their annotated gold lines.

| Candidate source | Annotated gold-file recall / complete sets | 8K line recall / complete | 16K line recall / complete | 32K line recall / complete | Mean delivered tokens at 16K |
|---|---:|---:|---:|---:|---:|
| Path-only tree | 0.518 / 49 | 0.159 / 14 | 0.288 / 26 | **0.406 / 38** | 8,473 |
| Literal search | 0.322 / 30 | 0.043 / 4 | 0.097 / 8 | 0.184 / 17 | 14,194 |
| BM25 | 0.269 / 24 | 0.057 / 5 | 0.115 / 11 | 0.195 / 17 | 13,639 |
| Tree+literal RRF | **0.536 / 51** | **0.164 / 15** | **0.321 / 28** | 0.400 / 36 | 13,838 |

At 16K, the tree has higher per-question line recall than literal on 26
questions and lower recall on four (71 ties); against BM25 the counts are
24/4 (73 ties). RRF beats tree on seven and loses on four (90 ties), adding
only two complete-line questions while delivering 63% more tokens on average.
At 32K, tree-only has two *more* complete-line questions than RRF: adding
files can crowd out a deeper gold file under sequential whole-file packing.
These are matched output caps, **not** matched file counts or query costs.
The saved tree run took mean 1.62 s (p95 3.98 s), 2.37 model calls, and an
estimated $0.000136/query; the lexical `rg` replay measured 56 ms mean for
its search call but excluded corpus materialization and Python ranking.

File routing remains insufficient. At 16K, tree-only has all gold files on
49 questions but all gold lines on 26, losing 23 in packing; RRF likewise
falls from 51 to 28. On Click's 26 questions, tree-only found every gold file
eight times yet produced no complete-line context. One example selects
`src/click/core.py` sixth for the flag-value failure, but its 3,134-line
source cannot fit whole in 16K tokens; the gold range is lines 824–904.
This motivates a separately controlled *line-localization* ablation, not a
claim that more tree routing solves context selection.

The already-tested simple expansion controls offer limited relief on these
same tree candidates. At 16K, mixed three 160-line windows in files over
1,200 lines raises complete-line questions from 26 to 28 (mean line recall
0.288→0.324); at 32K, mixed 320-line windows raise them from 38 to 41.
Click still has no complete-line context at 16K with the 160-line variant.
These widths were inspected on this same dataset, so they are diagnostic,
not a selected default or held-out gain.

This corpus was already used to explore ranking and packing, and Gin supplies
56/101 questions. The tree sees only the failure excerpt's first 500
characters through the existing router; lexical rankings use the full
excerpt. Do not call this an independent held-out validation or a matched
input-length comparison. These are frozen-candidate replays, not live
end-to-end timings or answer-quality results. Reproduce candidate manifests
with `uv run python -m evals.arb_full_source --rankings
<saved-ranking.json> --candidate-source <tree|literal|bm25|rrf>`; replay each
ranking with `uv run python -m evals.arb_oracle_span_ablation --rankings
<saved-ranking.json> --candidate-source <tree|literal|bm25|rrf>`. Each command
uses its matching `arb-full-source-<source>8.json` manifest by default. The
four local results are
`arb-candidate-span-{tree8,literal8,bm258,rrf8-matched}.json` under
`evals/results/`.

## Cross-task check: anchored edits and ripple files

[Agent Retrieval Bench's edit2ripple task](https://arxiv.org/html/2607.24882v1)
starts with a known anchor file, its diff, and an edit intent; the targets are
*other* affected files. Its release has file-level gold only, so these scores
cannot validate ±20-line expansion or be pooled with trace2code span recall.
We used the [official dataset release](https://huggingface.co/datasets/eyuansu71/agent_retrieval_bench)
at revision `5901e1ee3aff048290db72edf9c63bc498b79ea3`,
verified the archive SHA-256
`a174196d69b531d176a65c76fea928b3f1c893710baa4efccc48e901ff404b2c`,
and evaluated all 58 released examples across 10 repositories and 44 corpus
snapshots. The archive, reconstructed trees, and results are local and
gitignored. Gold and given paths were verified present in each corpus;
already-given anchor files were removed before final ranking and fusion.

The lexical methods rank released `kind=file` previews, which may be truncated,
not full source. “Literal+path” weights distinct matching terms and path
matches; “BM25” scores the same candidate text; fusion is RRF of their
top-eight lists. “Path neighbor” ignores content and ranks by common directory
prefix with the anchor. Full query means anchor path + intent + diff; the other
modes omit the diff or both diff and anchor. Mean file recall averages the
fraction of target files found per question; “complete” requires *all* target
files in the top k.

| Edit2ripple method | File recall@8 / complete | File recall@16 / complete |
|---|---:|---:|
| Path neighbor only | 0.128 / 4 of 58 | 0.290 / 12 of 58 |
| Intent-only literal+path | 0.266 / 10 | 0.450 / 19 |
| Anchor+intent literal+path | 0.323 / 13 | 0.490 / 20 |
| Full-query literal+path | 0.454 / 18 | 0.507 / 21 |
| Full-query BM25 | 0.408 / 17 | **0.513 / 22** |
| Full-query lexical RRF | 0.457 / 18 | 0.510 / 21 |
| Model path-only tree, 0.04 minimum / 0.05 relative threshold | 0.086 / 3 | 0.086 / 3 |
| Same strict tree + full-query literal+path, RRF | 0.478 / 19 | 0.489 / 20 |
| Model path-only tree, 0.01 / 0.01 thresholds | 0.399 / 17 | 0.399 / 17 |
| Same wider tree + full-query literal+path, RRF | **0.545 / 24** | **0.586 / 26** |
| Wider tree, independent repeat | 0.391 / 16 | 0.391 / 16 |
| Repeat tree + full-query literal+path, RRF | **0.547 / 24** | **0.586 / 26** |

The full diff contributes useful identifiers: full-query literal+path beats
anchor+intent by 0.131 mean recall at eight files. But the 58 cases are uneven:
17 are from Gin, and full-query literal+path recall@8 ranges from 0 on Clap
and Diffusers to 0.750 on Transformers. Many targets are tests or config in
other directories, explaining why a same-directory heuristic is weak. Fusion
with the strict tree gains on three questions and loses on one versus lexical
top-eight. The wider tree changes that to eight paired gains and two losses;
the ten-repository macro average rises from 0.326 for lexical+path to 0.390
for RRF, so the gain is not only Gin's sample count. The 48 cases after the
first-ten threshold sweep still rise from 0.483 to 0.550 top-eight recall
(four paired gains, one loss), but this is a post-sweep check, not a separate
preregistered holdout.

The strict path-only tree used TypeSafe `typesafe/jev-1.13.0`, 236 model calls
across 58 questions, median 1.56-second query time, and $0.01575 total
*input-token cost estimate*. It returned a median of one path; 27 questions
returned none. Its weakness was substantially due to aggressive branch
pruning. On a first-ten sweep, relaxing both thresholds to 0.01 raised recall
from 0.033 to 0.317; the full 58-case rerun reached 0.399 and returned a
median eight paths, with none empty. The wider run used 302 model calls,
median 2.30-second query time (empirical p95 6.07 seconds), and $0.01867
total input-token cost estimate. A first full attempt hit repeated TypeSafe
read timeouts; after the service recovered, the resumable run completed all
58 cases without errors. The same first question scored 0.5 in the ten-case
sweep but 1.0 on retry, motivating a full repeat to check model variance.
In an independent full repeat, only 24/58 exact tree path lists matched, but
57/58 per-question tree recall values did; RRF top-eight recall changed on
two questions and its aggregate stayed 0.545→0.547 with 24 complete cases.
This supports the broad fusion signal, not a guarantee for individual queries.
The lexical all-methods pass took median 0.32 seconds for the full query
(empirical p95 11.53 seconds), plus median 0.043 seconds to read its released
corpus snapshot (p95 0.796 seconds); it has zero index-build bytes. The
materialized path trees occupy 476 MB locally, but the 23,383 `index.md`
files total only 4.0 MB; the rest is copied candidate content and filesystem
overhead. Tree query time includes model calls but not this materialization,
so these are not cold end-to-end speed comparisons.

Run the completed lexical comparison with
`uv run python -m evals.arb_edit_ablation`. The tree runner now accepts
`--task v2_edit2ripple`, `--release`, `--trees`, routing thresholds, and
`--resume`; `evals.arb_fusion_ablation` accepts
`--lexical-name full/literal+path`. The recorded local results are
`arb-edit-ablation.json`, `arb-edit-tree-path.json`,
`arb-edit-tree-path-wide.json`, `arb-edit-tree-path-wide-repeat2.json`,
`arb-edit-fusion-strict.json`, `arb-edit-fusion-wide.json`, and
`arb-edit-fusion-wide-repeat2.json` under `evals/results/`. Next test a bounded
anchor-to-test/config expansion and check complete context under a token
budget; do not promote it from file-only development evidence.

## What the outside evidence changes

- [PageIndex](https://github.com/VectifyAI/PageIndex) independently pursues
  LLM-guided retrieval over a hierarchical index for long documents. Its
  [File System design](https://pageindex.ai/blog/pageindex-filesystem) also
  starts from a folder tree, but argues that inherited names can be weak and
  describes semantic virtual nodes and bypassing uninformative levels. That
  supports studying *when* a code tree carries useful routing signal; it does
  not validate our code-retrieval quality or require us to build its enterprise
  features. Treat it as architectural prior art, not a baseline in this study.
- [Hornet's 100M-document hybrid study](https://hornet.dev/blog/100m-doc-search-part-3-hybrid-search)
  found different lexical/semantic weights optimal for different query styles;
  RRF was a robust default there. In *our* Flask run, equal-weight BM25+FTS RRF
  reduced gold coverage from BM25's 0.608 to 0.483. Fusion needs a fixed-pool,
  query-stratified ablation, not a universal weight copied from web search.
- Hornet's [corpus preparation study](https://hornet.dev/blog/100m-doc-search-part-1-what-we-learned)
  found that noisy or thin source pages spoiled its evaluation, while its
  [ANN study](https://hornet.dev/blog/100m-doc-search-part-2-ann-tuning) found
  a query-embedding instruction mismatch, an under-connected graph, and a
  one-bit representation ceiling. None is evidence that Fastindex needs ANN;
  they are reminders to validate source coverage, query construction, and
  index behavior before attributing a miss to the ranking algorithm.
- [Hornet's query-workload study](https://hornet.dev/blog/this-is-what-agentic-retrieval-looks-like)
  observed long, operator-heavy, repeated agent searches. Its
  [code-mode experiment](https://hornet.dev/blog/same-retriever-fewer-tokens-better-recall)
  improved gold-document recall from 0.265 to 0.437 while reducing prompt
  tokens on 100 web questions with the same retriever. These are web-agent
  results, but motivate measuring a bounded *search session* as well as one
  query. [Hornet's keyword scaling tests](https://hornet.dev/blog/the-scaling-dimensions-of-keyword-search)
  also show that query length and term distribution affect search cost; a
  two-word benchmark is a poor proxy for agent queries.
- [Agent Retrieval Bench](https://arxiv.org/abs/2607.24882) evaluates 427
  repository-context cases across 25 repositories, including test discovery,
  stack traces, related edits, and no-gold cases. No retriever family wins every
  task; a repository map gave the best context yield at an 8K-token budget.
  [ContextBench](https://arxiv.org/abs/2602.05892) likewise measures gold
  context use across 1,136 issue tasks and 66 repositories.
  [CORE-Bench](https://arxiv.org/abs/2606.11864) separates code understanding,
  issue-to-edit localization, and broader context retrieval. These support
  task-stratified, token-budgeted evaluation beyond our one-repository QA set.
- [SweRank](https://arxiv.org/abs/2505.07849) finds that retrieve-then-rerank
  can beat costly multi-step agents on issue localization. Conversely,
  [LocAgent](https://aclanthology.org/2025.acl-long.426/) uses code-structure
  and dependency edges for cross-file localization. These studies do not prove
  either design best for source-evidence QA; they make ranking and a small
  cross-file expansion worth testing against a tree-only walk.
- [CodeGrep](https://arxiv.org/abs/2608.05886) trains a multi-turn agent to use
  grep, glob, and read; its downstream result depends on candidate precision.
  [ripgrep's own speed benchmark](https://ripgrep.dev/benchmarks/) measures
  scan speed, not relevance. Both justify an `rg` search/read control with
  the same evidence budget as Fastindex.
- [Better Call Grep](https://arxiv.org/abs/2601.23254) studies repository-level
  *code completion*, finding that index-free ripgrep can be competitive with
  graph-based retrieval, but generic keyword matches and rigid snippet cuts
  add noise and fragmentation. Its identifier-weighted reranking and merging
  of adjacent line ranges motivate testing exact identifiers and span fusion
  after our basic find-and-expand control; its completion scores are not QA
  evidence-recall scores.
- [FastContext's original paper](https://arxiv.org/html/2606.14066v1) uses a
  trained explorer to synthesize final file-line citations from parallel
  Read/Glob/Grep observations; it does **not** specify a deterministic
  overlapping-window merge. The arXiv entry was subsequently
  [withdrawn for product-IP review](https://arxiv.org/abs/2606.14066).
  The interval-fusion rule above comes from *Better Call Grep*, not FastContext.
- [Deep Agentic Search for Repository-Level Code QA](https://arxiv.org/abs/2608.01507)
  reports better answer accuracy and lower cost for its indexed semantic-search
  agent than for a delegated grep-search subagent on SWE-QA. Most failures of
  the delegated design were attributed to the planner/subagent hand-off. This
  is a warning about delegation and answer grounding, not a direct comparison
  of our flat `rg` hybrid with Pi's single-agent tool loop.

Hornet's web-scale ANN tuning and serving throughput do not transfer directly
to a 234-file repository. Do not build a vector database or graph service on
the strength of those results. The relevant lesson is to measure corpus,
index, query mix, and context exposure as separate stages.

## Evaluation contract before more tuning

1. Keep Flask as a development and regression set. Add at least two pinned,
   held-out repositories with different size and language/layout, then label
   file **and line-range** evidence before looking at new retriever outputs.
   Include exact identifiers, behavioral paraphrases, error traces, config,
   implementation-plus-test, and multi-hop questions. Add genuine no-evidence
   questions. Report each track and query type separately; do not combine QA,
   completion, and issue-localization scores into one number.
2. Freeze the searchable file list, source revision, ignore rules, prepared
   indexes, candidate text, models, and final context budget per comparison.
   Evaluate at fixed 8K, 16K, and 32K *tokens* as well as fixed file counts.
   Distinguish candidate-pool recall, top-k file recall, final gold-span
   coverage, complete-context rate, line precision, and context tokens.
3. Record p50/p95 wall time, model calls and tokens, cost, preparation time and
   cost, index bytes, update latency after a file edit, and cold versus warm
   runs. Compare paired query deltas and uncertainty across repositories;
   repeat model-driven runs to expose routing variance. Keep answer quality as
   a separate downstream check with the same answer model and prompt.

## Ordered experiments and ablations

This is the broader engineering backlog. For the narrower tree-decision paper,
use the [future decision gate](#future-study-and-decision-gate)
below; do not treat the LLM merge rows as paper prerequisites.

| Priority | Question | Controlled variants | Main readout |
|---|---|---|---|
| P0 | How much does the directory tree itself add? | Path/name-only tree vs prepared `index.md` tree; Jev vs `tree-reason`; same files, branch budget, and output stage | Candidate file recall, calls, latency, preparation break-even |
| P0 | Which cheap candidates find missing gold files? | BM25, current FTS, `rg` exact/identifier extraction, each pair, and all three; then add tree candidates; hold the pool at 8/16/32 unique files | Pool recall and unique gold contribution by source and query type |
| P0 | Does routing order matter? | Tree-first gating of lexical search vs parallel full-repo lexical search plus tree union; tree beam width/thresholds with the same call budget | Cross-branch misses, pool recall, p95 latency |
| P0 | What ranking survives a fixed pool? | No rerank, RRF with source weights, Noul, and one Choice ranking; identical 16 candidate descriptors and final file count | Top-k recall, cost, variance; check whether Noul gain survives held-out repos |
| P0 | Can exact spans replace whole files? | Whole file, fixed line window around match, symbol/function boundaries, and a mixed fallback; 8K/16K/32K-token packing | Complete gold-span coverage per token, line precision, large-file failures |
| P0 | Can one cheap model pass turn broad evidence into Pi-like precise context? | Freeze the tree+lexical candidate pool; compare direct widened windows with a no-tools `gpt-5.6-luna` pass that selects and combines cited ranges from those *same* windows | Pre/post complete gold-line coverage, line precision, final tokens, added latency and cost |
| P1 | Can a second lookup assemble multi-hop evidence? | One bounded follow-up from selected imports, calls, tests, or links vs no follow-up; no graph database | Complete multi-file context, added cost and distractors |
| P1 | Do agent-style queries need a different interface? | Single full question, deterministic identifier/phrase extraction, bounded query fan-out with `rg`/BM25, and one feedback search when evidence is incomplete | Session-level coverage, turns, tokens, p95 time |
| P2 | When do new indexes pay for themselves? | One code embedding baseline and one local symbol/reference index only after P0/P1 miss analysis | Held-out gain after setup, storage, edit-refresh, and query cost |

Use identical per-question candidate pools for ranking ablations; use identical
final context token budgets for packing ablations. Source-removal tests should
compare *pool recall before ranking* as well as final output, because a better
ranker cannot recover a file no candidate source found. For `rg`, test both a
literal identifier/phrase query and a documented deterministic query rewrite;
passing the entire natural-language question as one regular expression would
be an unfair control. Include path and filename matching in lexical baselines.

### Broad tree/search context, then one model selection pass

The **current** hybrid is Jev/BM25/FTS (optionally `rg`) candidate discovery,
RRF pool, Noul *file* reranking, then up to eight whole files by default (or
optional deterministic windows) under a character cap. It does not run a
final Luna evidence-selection pass. The proposed ordering below is therefore
an ablation, not a description of the product or existing hybrid.

Test the proposed pipeline as a separate **retrieval** ablation: Jev tree
routing with its recorded absolute/relative probability thresholds, union with
full-repository lexical candidates, search *inside* the candidate files, merge
overlapping hit windows expanded by ±200 lines, and present the bounded,
line-numbered context to `gpt-5.6-luna` in one prompt. Give the model no tools,
no gold labels, and no answer-generation task. It must return repository paths
and inclusive line ranges; validate that every returned line was actually in
the supplied context, then coalesce touching ranges deterministically. This
borrows Pi's semantic evidence selection, not its multi-turn tool loop.

Run paired controls in this order, changing only one factor at a time:

1. On the *same frozen candidate order*, compare whole files, merged ±20/80/200
   lexical windows, and ±200 windows followed by one Luna pass. Score the
   windows **before** and **after** the model so a precision gain cannot hide
   dropped gold lines. Keep both the model-input budget and final-output budget
   fixed (8K/16K/32K tokens); report actual tokens when a variant cannot fill
   the budget. Include a no-LLM deterministic interval merge and the prior
   Noul-ranked whole-file result as controls.
2. Hold window generation and the final model prompt fixed while varying the
   candidate target (8/16/32) and Jev's absolute/relative branch thresholds.
   Compare tree-only, lexical-only, and their union, with full-repository
   lexical search versus tree-gated search. Measure pool recall first: the last
   model cannot recover a file never supplied to it. Repeat model-driven tree
   routes and report candidate-set variance.
3. Hold candidates and model-input context identical while comparing the
   deterministic output, one Luna selection pass, and Pi's tool-using agent.
   Separately vary only the Luna prompt between “choose precise ranges” and
   “keep all lines needed to explain the behavior, including tests/config,”
   to test whether aggressive compression clips evidence. Avoid selecting a
   prompt on held-out results.
4. On the same broad candidate pool, test **rerank order** explicitly: Noul
   file rerank before find-and-expand (the current hybrid order), rerank the
   expanded snippets before Luna, and no Noul rerank. Hold candidate paths,
   window generation, and both token caps fixed. A later ranker cannot repair
   files or source lines already removed upstream.

Rank by complete gold-*line* questions, then mean line recall, context tokens,
p50/p95 latency, and model cost. Report line precision and F1 as diagnostics,
not selection objectives: extra context is acceptable when it preserves gold
evidence within the budget, but irrelevant lines can raise cost or distract
the final model. The Luna pass can also discard needed lines, so report its
post-selection recall separately from the recall of the windows it received.
Break results out by
exact-symbol, paraphrase, test/config, trace, multi-hop, and no-evidence queries.
Log invalid citations, out-of-window ranges, empty outputs, and prompt/context
overflow as failures, not silent fallbacks. Start on Flask for feasibility,
then use pinned, separately labeled repositories for the decision; the
existing ARB trace candidates provide an immediate distractor-rich check but
are not a new held-out set after the earlier window exploration.

The first frozen-source Luna feasibility run is complete on all 48 Flask
development questions. It uses the saved `base+rg` Noul top-eight file order,
up to four lexical anchors per file expanded by ±200 lines and merged, a 32K
`cl100k_base`-counted model-input cap, one no-tools `gpt-5.6-luna` pass at low
reasoning, and an 8K final-output cap. Thus it tests **rerank-before-expand**, not the proposed
rerank-after-expand ordering. It does not rerun Jev or Noul, and its cost/time
below are for the new Luna pass only. The local result is gitignored
`evals/results/llm-merge-flask-ranked8.json`; reproduce with
`uv run python -m evals.llm_merge_ablation --candidates ranked --candidate-k 8`.

| Stage on identical frozen files | Complete gold-file sets | Complete gold-line questions | Mean gold-line recall | Mean context tokens |
|---|---:|---:|---:|---:|
| Noul top-eight file candidates | 43/48 | — | — | — |
| Merged ±200 windows visible to Luna | 43/48 | **30/48** | **0.791** | 29,934 |
| Direct 8K-token packing of those windows, no Luna | — | 17/48 | 0.549 | 6,840 |
| Luna-selected and coalesced ranges, packed to 8K | 39/48 | 27/48 | 0.769 | 1,910 |

The one-pass model recovered 11 complete-line questions that direct 8K packing
missed, but lost one that direct packing retained; relative to its *full visible
input*, it lost complete lines on q005, q030, and q036. Four returned ranges
were outside the supplied windows and were rejected. Mean incremental Luna
time was 4.35 seconds and reported cost was $0.00776/question, before the
frozen candidate discovery and Noul rerank costs. It is therefore premature
to call this pipeline cheaper than Pi or lossless. The input cap admitted a
mean 6.9 of eight ranked files, though all labeled gold files remained visible
on 43 questions. Five questions already lacked all gold files in the ranked
eight; thirteen more lost complete gold lines in the windows/input cap; the
final model dropped complete lines on three additional questions. These are
single-run, Flask-development findings.

### Paired rerank order and context-budget follow-up

We reran Noul on the **same frozen `base+rg` RRF pool** with 1,600-character
candidate descriptors and a 320-character index-summary allowance. The only
descriptor change was replacing the prior lexical preview with a source
excerpt centered on each file's strongest literal hit (±20 lines). Final
context still uses merged ±200-line windows. This is a practical
*search-before-rerank preview*, not a Noul pass over every line of every
400-line window. Two of 48 snippet-preview Noul requests returned HTTP 403 on
both the original and an independent retry; the paired table uses the 46
successfully scored questions. It does not silently substitute rankings for
those two failures.

| Same 46 questions, top eight files, 32K input / 8K output | Questions with all gold lines in Luna input | Mean input line recall | Complete with direct 8K packing | Complete after Luna | Mean final line recall |
|---|---:|---:|---:|---:|---:|
| File-summary/lexical-preview Noul before expansion | 30/46 | 0.812 | 17/46 | **27/46** | **0.802** |
| Match-centered preview to Noul after search | 30/46 | **0.824** | **19/46** | 25/46 | 0.797 |

Snippet previews improved direct packing on six paired questions and lost one,
but the final Luna output gained only q011 and lost q002 and q010 relative to
the file-preview order. Mean Noul cost was $0.000296 versus $0.000318/query;
mean additional Luna cost was $0.00777 versus $0.00774/query. Candidate
discovery and the Jev walk were frozen and excluded from these incremental
figures. At this cap, the extra snippet-aware Noul pass is **not** a measured
improvement to final evidence, and its two repeated 403s are an operational
regression. A no-Noul RRF top-eight control, scored offline on all 48, left
complete gold lines in only 21/48 32K inputs and 10/48 direct 8K outputs,
versus 30/48 and 17/48 with file-level Noul. That justifies keeping the
reranker in this Flask pipeline, not promoting either preview variant as a
general rule. Local results are `rerank-order-flask.json`,
`rerank-order-{base,window}-{packing,llm}.json`, and
`rerank-order-rrf{,-packing}.json` under gitignored `evals/results/`.

With the **same file-level Noul order**, changing only the Luna-input cap,
candidate target, and interval packing order gives this offline upper bound:

| Pack order / candidate target | 16K input: complete lines / recall | 32K input | 64K input |
|---|---:|---:|---:|
| Round-robin / 8 files | 26/48 / 0.731 | 30/48 / 0.791 | 32/48 / 0.801 |
| Round-robin / 16 files | 26/48 / 0.731 | 30/48 / 0.802 | 31/48 / 0.807 |
| Sequential / 8 files | — | **32/48 / 0.801** | 32/48 / 0.801 |
| Sequential / 16 files | — | **32/48 / 0.801** | 32/48 / 0.811 |

At 64K, adding eight lower-ranked files under round-robin packing lost q041's
complete lines because their first windows displaced a useful later window;
more selected files are not automatically more useful context. Sequential
packing at 32K exposed complete lines on q040 and q041 that round-robin
missed, with no paired input-line loss. Its Luna replay kept both gains:
**29/48** complete gold-line questions versus **27/48** for round-robin at
similar $0.00777 incremental cost, while mean final line recall slipped from
0.781 to 0.768 due partial-line losses elsewhere. A targeted independent
repeat again kept q040/q041 complete, but q026 changed from zero to 0.859 line
recall, showing model-selection variance. Even with the better input packing,
Luna dropped complete lines on q030, q035, and q036. These are development-set
ablations, not a lossless or held-out winner. The offline frontier is
reproducible with `evals.llm_merge_ablation --offline --candidates ranked-pool`
and `--pack-order round-robin` or `sequential`; the Luna replay is local
`evals/results/input-frontier-sequential-k8-b32000-llm.json`.

## Independent-repository ContextBench pilot (September 24)

This is the first **new-repository, pinned-source** check after the Flask and ARB
development work. It is a pilot, not a confirmatory held-out benchmark:
the same 24 cases informed this report, issue types are not balanced, and the
pruning settings came from previous exploration. We froze the IDs before
running retrieval. From the
[ContextBench dataset](https://huggingface.co/datasets/Contextbench/ContextBench),
revision `c2855792b006af41c67202d33883fb9d46362853`, we selected the first
12 eligible cases per repository in SHA-256 order of `instance_id` for
`django/django` and `sveltejs/svelte`. Eligibility required a nonempty issue,
40-hex base commit, and nonempty, valid repository-relative gold spans. This
excluded rows with absolute `/workspace` gold paths before any retrieval run;
it therefore does **not** sample the entire dataset population. The pinned
Parquet SHA-256 is
`2f56535bdc73eb8a68bf4ebb49789d8e9cd4f219ea60df6290b85278aee61ca8`.
The [manifest](../evals/fixtures/queries/contextbench_holdout.json) records
the exact IDs and rule. All 24 base-commit source worktrees were checked, with
zero missing gold files or invalid starts. Four gold end lines in two cases
exceeded EOF and were clipped, matching ContextBench's
[line-to-byte reader](https://github.com/EuniAI/ContextBench/blob/main/contextbench/core/fileio.py).

The primary comparison uses **name-only** `tree-decision` at top eight, TypeSafe
`jev-1.13.0`, a 32-call cap, and two previously chosen branch-threshold pairs:
wide 0.01/0.01 and strict 0.04/0.05. They alternate execution order per case.
The lexical controls rank every searchable UTF-8 source file in the same
snapshot using literal matched terms, path-boosted literal terms, BM25,
SQLite FTS5, or a two-source RRF. Both full issue text and its first 500
characters were tried;
the tree routing prompt uses the first 500. The main lexical ranking runs in
memory. A separate actual-`rg` replay checked the path-boosted literal ranking
and measured its search time; all 48 top-16 rankings matched exactly. The
context check reads the same sources and packs ranked whole
files in order, skipping files that do not fit. Every method uses the same
`cl100k_base` 8K/16K/32K-token budgets; the table shows 16K. Candidate file
recall and delivered gold-line recall are different stages. Means are macro
averages over questions, not fractions of all lines pooled together.

| Retriever | Gold-file recall@k | All gold files in candidates | Gold-line recall at 16K | Complete gold-line contexts | Retrieval time/query | Model cost/query |
|---|---:|---:|---:|---:|---:|---:|
| Tree names, wide @8 | **0.476** | **7/24** | 0.362 | **4/24** | 6.45 s | $0.000296 |
| Tree names, strict @8 | 0.468 | 6/24 | **0.368** | **4/24** | 8.21 s | $0.000447 |
| Literal + path, full issue @8 (actual `rg` parity) | 0.219 | 3/24 | 0.079 | 1/24 | 1.85 s incl. enumeration | $0 |
| BM25, full issue @8 | 0.266 | 4/24 | 0.190 | 3/24 | — | $0 |
| BM25, full issue @16 | 0.335 | 5/24 | 0.203 | 3/24 | — | $0 |
| SQLite FTS5, first 500 chars @8 | 0.295 | 5/24 | 0.281 | 4/24 | 0.89 s cold | $0 |
| SQLite FTS5, first 500 chars @16 | 0.354 | 5/24 | 0.290 | 4/24 | 0.89 s cold | $0 |
| SQLite FTS5, full issue @8 | 0.257 | 3/24 | 0.198 | 2/24 | 0.90 s cold | $0 |
| RRF, full issue @8 | 0.260 | 3/24 | 0.172 | 2/24 | — | $0 |
| RRF, full issue @16 | 0.370 | 5/24 | 0.172 | 2/24 | — | $0 |
| Tree wide + BM25, RRF @8 | 0.470 | 7/24 | 0.395 | 5/24 | — | same tree calls |
| Tree wide + BM25, RRF @16 | **0.568** | **9/24** | **0.406** | **5/24** | — | same tree calls |

The lexical pipeline loads an average of 4,391 files per case in 0.80 s and
ranks a full issue in 2.30 s in this local run; those shared costs apply to
the in-memory literal/BM25/RRF rankings, not separately to each table row.
First-500-character ranking averaged 1.35 s. SQLite FTS5 is a distinct
in-memory, full-file index with path/content weights 2:1; it took 0.88 s to
build per snapshot, then 7 ms for a first-500 query or 20 ms for the full
issue. This is **not** the older `fts` adapter's term-frequency count, and
the cold number should not be confused with a warm persisted-index query.
The union rows are an **offline recombination** of
the already saved tree-wide@8 and BM25@8 file rankings; no combined wall time
was measured, and they require both retrieval stages. At @8, union candidate
recall is slightly below tree alone, even though its delivered-line recall is
higher; at @16, candidate and final evidence both improve. This is a useful
complementarity signal, not a validated hybrid default. The tree read files on
demand, averaging 6.5 model calls and 7,036 input tokens for wide routing
(8.0 and 10,644 for strict);
there were no errors or budget truncations. Tree wide's p50/p95 query times
were 6.69/9.99 s; strict's were 8.37/12.89 s. Source snapshot setup was
mixed cold/warm: 21 newly materialized worktrees averaged 3.93 s, three were
already present from a smoke check. That is **not** the tree-index preparation
cost; the name-only tree needs no generated summaries.
The saved pilot did not include output-token counts; the runner now records
them for subsequent runs.

The actual `rg` replay averaged 1.07 s for batched search on full issue text
and 0.79 s to enumerate the same UTF-8 source-file set first. Its first-500
search averaged 0.55 s. Enumeration currently reads every file through the
bundle interface; that overhead is not intrinsic to ripgrep and could be
reduced with a path-only file list. Search times exclude context packing.
The exact ranking parity is with the in-memory literal+path method, not with
BM25 or tree. Local artifact: `evals/results/contextbench-rg-paired.json`.

One **exploratory** final-context ablation replaces whole files with merged
match-centered windows: up to four highest-scoring lexical anchor lines per
candidate file, each expanded by ±200 lines, with touching intervals merged. The setting
existed in the earlier ARB experiment, but was chosen for this replay after
the pilot's whole-file results were inspected; it is not a preregistered
held-out test. File candidates and ranks stay frozen, and every method uses
the same full issue text for this final packing pass. At 16K tokens:

| Frozen file ranking | Whole-file gold-line recall / complete | Find-and-expand recall / complete | Mean input tokens, whole → windows |
|---|---:|---:|---:|
| Tree wide @8 | 0.362 / 4 of 24 | 0.430 / 3 of 24 | 10,769 → 11,423 |
| Tree strict @8 | 0.368 / 4 of 24 | 0.410 / 4 of 24 | 9,711 → 10,141 |
| BM25 full issue @8 | 0.190 / 3 of 24 | 0.180 / 3 of 24 | 12,133 → 13,784 |
| FTS5 first 500 @8 | 0.281 / 4 of 24 | 0.266 / 4 of 24 | 11,313 → 12,378 |
| Tree wide + BM25 RRF @16 | 0.406 / 5 of 24 | 0.439 / 4 of 24 | 14,803 → 15,379 |

Windows make previously oversized files fit, so they improve tree mean line
recall; tree mean line precision also rose from 0.043 to 0.071. They are not
a compression win at this budget. They also dropped full
evidence for Django case `768d44e5`: whole-file tree context contained every
annotated line, but match-centered windows retained only 0.864 of them,
despite all three gold files being among tree's eight candidates. No case
became complete under the wide-tree window variant. This reinforces the need
to report both average coverage and complete evidence, and to validate a
window policy on another frozen set. Local artifact:
`evals/results/contextbench-context-merge4-200.json`.

The observed gain is heterogeneous. At 16K, wide tree file recall was 0.684
on Django versus BM25's 0.413, and 0.269 on Svelte versus BM25's 0.118.
Wide tree beat BM25 on candidate file recall in 12 paired cases, lost in four,
and tied in eight; BM25 alone completed one gold-file set that tree missed.
Across 24 paired cases, wide tree minus BM25 was +0.211 candidate file recall
and +0.171 delivered-line recall. A descriptive case-paired bootstrap (10,000
resamples, seed 0) gave 95% percentile ranges of +0.043 to +0.388 and +0.012
to +0.341 respectively. These post-run intervals quantify pilot uncertainty;
they are not confirmatory significance claims or corrections for the many
earlier development experiments.
Against the stronger *first-500 FTS5* control, wide tree's line-recall delta
is only +0.081, with a descriptive paired-bootstrap range of -0.109 to +0.263;
its candidate-file delta is +0.181, range -0.043 to +0.402. On Svelte, both
methods retain about 0.25 of gold lines at 16K, while FTS5 completes three
cases to tree's one. In the paired 24 cases, tree and FTS5 each uniquely
completed two contexts and completed two together. This control prevents a
blanket “tree beats lexical”
claim.
The frozen pilot includes a Svelte issue with 15 gold files and 291 annotated
spans, making complete@8 impossible. Excluding just that outlier in a labeled
sensitivity check changes wide tree's candidate/16K-line recall from
0.476/0.362 to 0.497/0.377, and BM25's from 0.266/0.190 to 0.277/0.199;
the direction does not reverse. This is not a reason to drop it from the
primary result.

Gold-content alignment is another important limit. Comparing the dataset's
stored span text with the pinned base-commit lines gave 418/454 exact after
trimming whitespace, 439/454 with containment in either direction, and 15
non-containing spans across eight cases. The primary metric uses annotated
paths and clipped line ranges, not text similarity. On the 16 cases without
non-containing spans, wide tree still leads BM25 in candidate recall
(0.453 vs 0.172) and 16K line recall (0.337 vs 0.126), but that is a
post-hoc sensitivity check, not a replacement score. See the local audit
`evals/results/contextbench-gold-audit.json`.

**Prepared-index feasibility check.** We did not generate summaries for the
24 ContextBench snapshots. A read-only inventory following the current
`prepare` traversal and 10,000-character file chunking counted 105,377
readable source-file instances and 55,688 directory instances across the
24 pinned worktrees. A full fresh/forced bottom-up preparation would require
at least **188,153 model calls** (121,929 source segments, 10,560 segment
reductions, and 55,664 child-index summaries), averaging 7,840 per snapshot:
124,475 across Django's 12 revisions and 63,678 across Svelte's 12.
These are *call-count lower bounds*, not observed latency, token usage, cost,
or a quality score; the bound assumes every child index fits in one model
call and no retries. Ten pre-existing `index.md` files would be preserved by
a default non-forced run, so the fresh/forced estimate is not a prediction for
that invocation. Revisions overlap, but cross-revision caching and incremental
refresh are not implemented or measured here. The inventory therefore makes
full independent preparation an inappropriate way to close this pilot; the
held-out value of prepared summaries remains unanswered. Reproduce the
read-only count with `uv run python -m evals.contextbench_prepare_inventory`;
the local artifact is `evals/results/contextbench-prepare-inventory.json`.

**Descendant-name menu ablation.** We closed one more paired check on the
same frozen cases, model, top-eight output, 32-call cap, and wide 0.01/0.01
thresholds. Both variants retain the directory tree and direct child names;
`names0` omits the extra deeper-name hints while `names12` shows up to 12,
matching the previous default. Execution order alternated by case. This
tests menu detail, **not** tree navigation against flat search. The saved
threshold-pilot scorer was replayed unchanged in aggregate before scoring
this new run.

| Menu | Gold-file recall@8 / complete candidate sets | Gold-line recall at 16K / complete contexts | Calls / input tokens per query | Mean / p95 latency | Estimated cost/query |
|---|---:|---:|---:|---:|---:|
| Direct child names (`names0`) | 0.462 / 8 of 24 | 0.370 / 5 of 24 | 6.58 / 6,065 | 6.60 / 10.49 s | $0.000255 |
| Plus 12 descendant names (`names12`) | 0.501 / 8 of 24 | 0.377 / 5 of 24 | 7.33 / 8,772 | 7.19 / 11.39 s | $0.000368 |

Lists changed on 22/24 questions, but candidate recall improved on four,
worsened on one, and tied on 19; each variant uniquely completed one gold-file
set. The +0.039 candidate-recall change shrank to +0.007 delivered-line recall
under the identical 16K whole-file cap, with no gain in complete evidence.
A descriptive case-paired bootstrap (10,000 resamples, seed 0) gave 95%
percentile ranges of -0.083 to +0.164 for candidate recall and -0.108 to
+0.101 for delivered-line recall. On Django, descendant names slightly
*lowered* delivered-line recall (0.554 to 0.533); on Svelte they raised it
(0.186 to 0.221). Input tokens rose about 45% and estimated query cost about
44%. There were no errors or budget truncations. This single paired run does
not establish a benefit from deeper hints; keep the menu limit tunable and
do not promote 12 names as the evidence-optimal default. Its 5/24 complete
contexts versus the previous wide run's 4/24 also show path-selection variance
across runs, not an improvement caused by this ablation.

Reproduce with `uv run python -m evals.contextbench_tree --experiment descendants`
and `uv run python -m evals.contextbench_context --tree-experiment descendants`.
The local artifacts are `evals/results/contextbench-tree-descendants-paired.json`
and `contextbench-context-descendants-paired.json`.

These results justify a larger tree-decision study, **not** a claim that it is
the best retrieval engine. Only 4/24 wide-tree contexts contain all annotated
lines at 16K, despite seven complete file candidate sets. Greedy whole-file
packing can even be non-monotonic as a budget grows: a large, higher-ranked
distractor may enter and displace a useful smaller file. We have not run this
pilot's prepared-summary tree, no-evidence questions,
independent repeat trials, or downstream coding tasks.
Path-only tree is an encouraging low-setup baseline; prepared summaries must
earn their generation and refresh cost on additional held-out data.

Reproduce from the repository root (the holdout exporter validates the
downloaded file's hash):

```bash
mkdir -p evals/fixtures/external/contextbench
curl -fL https://huggingface.co/datasets/Contextbench/ContextBench/resolve/c2855792b006af41c67202d33883fb9d46362853/data/full.parquet \
  -o evals/fixtures/external/contextbench/full.parquet
uv run --with pyarrow python -m evals.contextbench_holdout
uv run python -m evals.contextbench_snapshots
uv run python -m evals.contextbench_tree
uv run python -m evals.contextbench_lexical
uv run python -m evals.contextbench_fts
uv run python -m evals.contextbench_rg
uv run python -m evals.contextbench_context
uv run python -m evals.contextbench_context --packing merge4-200
uv run --with pyarrow python -m evals.contextbench_gold_audit
```

The tree run needs `TYPESAFE_API_KEY`; its output is resumable with `--resume`.
All downloaded source, worktrees, and result JSON are gitignored. The scripts
and manifest are in-repository study artifacts. The primary machine-readable
results are `evals/results/contextbench-tree-names-paired.json`,
`contextbench-lexical-paired.json`, and `contextbench-context-paired.json` in
that local results directory.

## Future study and decision gate

This exploratory study stops here. The Flask menu ablation shows a modest
file-recall benefit from generated summaries, but it is still a development-set
file-routing test. The paired ContextBench descendant-name check did not
improve complete evidence. If we pursue a paper-grade `tree-decision` study,
the remaining work is, in order:

1. **Expand the frozen pilot into a diverse evaluation.** The first two new
   repositories and path+line labels are in place. Add more repositories and
   prespecified query types: paraphrase, exact symbol, implementation plus
   tests, cross-directory and multi-hop, plus no-evidence cases. Audit label
   alignment before scoring. Keep Flask and ARB as development diagnostics.
2. **Measure the tree itself.** The pilot compared direct names with up to 12
   deeper names under the same model, thresholds, call cap, and top-k, and
   inventoried full preparation calls without running them. A future study
   must compare inherited path names with prepared summaries on more than
   Flask, using a feasible cached/incremental preparation design and measured
   cold-build and edit-refresh costs. Sweep branch thresholds and breadth
   only on development data; inspect lost branches and paired query deltas.
3. **Finish the fixed-budget controls.** The pilot has in-memory literal/path,
   BM25, SQLite FTS5, RRF, actual-`rg` timing/parity, offline tree+BM25 union,
   and whole-file versus merged ±200-line packing at 8K/16K/32K. Freeze a
   packing rule before the next held-out corpus, and time the combined
   tree+lexical pipeline end to end. Report candidate and
   final file recall@8/16, complete gold-file sets, and gold-line coverage at
   identical caps. Do not conflate file recall with final context.
4. **Repeat model-driven runs and account for operations.** Report per-repo and
   query-type results, p50/p95 latency, calls, tokens, query cost, index bytes,
   preparation/update cost, and path-selection variance. Do not collapse
   no-evidence cases into ordinary recall or claim answer-quality gains from
   retrieval labels.

Pursue a tree-decision paper only if it adds reproducible, complementary gold
evidence over cheap search under a fixed context budget. Otherwise, report the
negative result and keep tree navigation as an optional eval strategy rather
than a default retrieval layer.
