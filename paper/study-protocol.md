# Study protocol: classifier-guided repository tree routing

Status: **superseded by [budget amendment 001](study-amendment-001.md)**
(September 24, 2026). The original design below is retained as a
transparent record and must not be mistaken for the preprint's final run
plan. Its original status was: **cohort and primary comparison locked locally; retrieval pending**
(September 24, 2026). This is a held-out test, not an externally
preregistered study. Preserve the manifest and analysis code hashes before
scoring; record any corrections as amendments. Commit the final protocol and
artifacts before release, but do not imply that a later commit proves
preregistration. The [local pre-run freeze](pre-run-freeze.json) records
design settings and SHA-256s of the case manifest, runners, scorer, and
router before the independent-cohort retrieval run.

## Question and scope

Does a model-guided walk of a repository's existing directory tree contribute
annotated *files* that strong lexical code search misses at the same candidate
budget? Does combining the two improve evidence available to a coding agent?
The unit is a query against a pinned source revision. This is a study of file
discovery and evidence delivery, not of answer generation and not a claim that
`rg` itself cannot find a file after an agent reformulates its search.

The evaluated method is `tree-decision`: a categorical model judges plausible
children at each visited directory and retains multiple branches under fixed
probability and call budgets. The main method uses inherited path names and
does not require generated `index.md` summaries. The exact model, menu format,
branch thresholds, and file limit must be frozen before the confirmatory run.

## Hypotheses

1. At fixed top-8 and top-16 file budgets, the tree contributes gold files
   absent from the strongest development-selected lexical ranking. Report the
   paired per-query gain, not only average recall or a selected example.
2. At those same candidate budgets, a predeclared tree-plus-lexical fusion
   improves complete gold-file sets and, after *identical* context packing,
   gold-line coverage over the stronger of its two components. A larger union
   is not a fair win over a smaller lexical-only list.

## Data and freeze gate

- Keep the 48 Flask questions, ARB runs, and the already inspected 24
  Django/Svelte ContextBench cases as **development and pilot** material.
- The [frozen cohort manifest](../evals/fixtures/queries/contextbench_confirmatory.json)
  selects 171 issues at 171 base revisions across 11 previously unseen
  repositories and six languages. Its SHA-256 before retrieval is
  `658899a62dc24d87cb8120bd0183c523f53d835e324baceb493c7cbd4fd82e5c`.
  The selection uses only dataset metadata and eligibility, never retrieval
  outcomes: top three Python, top two TypeScript with distinct owners, top
  two JavaScript, top two Go, and top one each C/Rust repositories by eligible
  count, excluding the pilot repositories; first 20 eligible issue IDs per
  repository ordered by SHA-256 of ID. This is a stratified convenience
  sample, not a representative sample of all coding-agent tasks. The pool
  contained 605 eligible cases across 56 repositories and eight languages.
  Keep all selected cases in the audit and report source/gold failures
  explicitly; do not replace them after outcomes are visible.
- Before any retrieval, [the mechanical audit](../evals/contextbench_audit.py)
  checks each base-commit snapshot and the shared searchable-file set.
  Exclude from the primary denominator only selected cases with a missing or
  unreadable gold file, an invalid gold start line, or a gold file omitted by
  the common corpus policy. Keep every exclusion with its reason in the
  ledger; never backfill. Gold ends beyond EOF are clipped and counted.
  Text-content mismatches are flagged and analyzed as a sensitivity check,
  not silently removed.
- Use the issue statement only as the query: never expose gold paths, patches,
  test patches, or post-fix source to a retriever. Pin the base-commit source
  worktree for every case. Audit gold-file existence, line bounds, and stored
  span-text alignment *before* scoring. Preserve a flag for annotation
  mismatches instead of silently discarding them.
- Predeclare objective subgroups from issue text and gold metadata, without
  seeing retrieval output: whether the issue mentions an annotated full path
  or basename of at least five characters (case-insensitive substring),
  and whether gold files span multiple directories. These
  describe likely search difficulty; do not label a subjective semantic gap
  after seeing outcomes. Cases with no gold evidence need a separate
  negative-query track; do not score them with positive-case recall.
- Freeze searchable paths and ignores identically for all methods. A
  source-only corpus and an all-eligible-text corpus can be separate declared
  tracks; neither method may silently use a different file set.

## Comparisons

| Arm | What it isolates |
| --- | --- |
| Literal/identifier `rg` ranking with a frozen query builder | Cheap exact-match discovery, including path boosts and source-file filtering selected on development data |
| BM25 and SQLite FTS5 | Stronger cheap lexical rankings; choose the primary lexical comparator on development data only |
| Jev path-name tree | Main hierarchical candidate generator, without generated summaries |
| Tree plus the frozen best lexical arm | Incremental candidate and delivered-evidence value at equal top-k and token budgets |

An adaptive coding agent using `grep`/`read` is **not** a single lexical
search baseline. The main paper tests file discovery and equal-budget
evidence fusion, not downstream coding success. A same-agent `rg` versus
`rg`-plus-tree experiment and a matched Jev-versus-chat/flat-path routing
ablation are valuable follow-ups, but are outside the frozen main claim.
Do not infer agent utility from offline fusion alone. Pi's earlier Flask
tool-loop run is background, not a head-to-head comparator in the main table.

The pilot selected these settings before retrieval on the new cohort:
TypeSafe `typesafe/jev-1.13.0` name-only tree, 0.01 absolute/0.01 relative
branch thresholds, descendant limit 12, 32 model-call and 180-second
per-query limits, first 500 issue characters in each routing prompt, output
top 16 (score prefixes at 8 and 16). The primary
lexical comparator is FTS5 over the first 500 issue characters, the highest
pilot lexical recall@8 (0.295); full-issue BM25 and full-issue actual `rg`
rankings are controls. All index/query build costs must be reported. Fuse
tree@16 and FTS5@16 with reciprocal-rank fusion, constant 60, then truncate
to the same 8 or 16 candidates. Pack every arm with the same four
lexical anchors per file, ±200-line merged windows, and 16K
`cl100k_base` tokens. The 8K/32K context limits are sensitivity analyses.
The [locked scoring script](../evals/contextbench_study.py) computes these
without selecting a winner on this cohort. Time the *actual* combined
pipeline in a separate run rather than adding offline stage estimates.

## Outcomes and analysis

Primary file-discovery outcomes: macro gold-file recall@8 and @16, proportion
with all gold files in candidates, count of gold files uniquely contributed by
tree versus primary FTS5 and versus actual `rg`, and the proportion of
questions with such a contribution.
These reflect the method's actual claim. The decisive application check is
gold-line recall and fully covered gold-line questions after identical
16K-token packing. Report pre- and post-packing coverage separately.
Report line outcomes both for all audited cases and for cases where every
annotated span aligns to pinned source text after whitespace normalization
or exact-text containment. The aligned subset is a data-quality sensitivity
analysis; it must not replace the main file-discovery denominator.
Because 32/171 cases have more than eight gold files, and 8/171 have more
than sixteen, report the impossible-complete-set ceiling and attainable-case
rates alongside all-case complete counts. The primary contrast is paired
tree-versus-FTS5 recall, followed by fusion-versus-FTS5 and
fusion-versus-tree. Use 10,000 paired repository-cluster bootstrap samples
with seed zero for intervals; no post-hoc best-arm selection.

Secondary outcomes: returned-file precision, line precision, context tokens,
model calls and tokens, p50/p95 wall time, estimated and observed model cost,
index/build and refresh cost where applicable, and failure/truncation counts.
If provider billing data are unavailable, label token-price calculations
as estimates and do not describe them as observed charges.
Repeat stochastic model routes and report path-set and metric variance.
Report paired differences and uncertainty with repository-aware resampling,
plus per-repository and query-type tables; do not substitute a selected 3/3
case for the distribution. Inspect lost branches, lexical noise, files found
but not packed, and cross-directory misses.

## Pilot evidence, not confirmatory evidence

The inspected 24-case Django/Svelte pilot found mean gold-file recall@8 of
0.476 for name-only tree, 0.219 for fixed full-issue literal/path ranking
(matched by an actual `rg` replay), and 0.295 for first-500-character FTS5.
At 16K whole-file context, tree and FTS5 each produced 4/24 complete gold-line
contexts. One Django password-reset case had 3/3 tree gold files versus 0/3
in the literal/path top eight. The cases, thresholds, and packing options
have already been inspected: these numbers motivate the hypotheses but cannot
test them independently. Full setup and caveats are in
[the exploratory report](../docs/code-retrieval-study-plan.md#independent-repository-contextbench-pilot-september-24).

## Publication rule

The paper can report a conditional or negative result. Claim a useful tree
contribution only if it survives the frozen strong lexical comparator and an
equal-budget final-context check. Do not claim to be the first codebase tree
retriever or the first Jev-based code search: [LocAgent](https://aclanthology.org/2025.acl-long.426/),
[repository-aware file-path retrieval](https://arxiv.org/abs/2510.08850),
[Blink](https://github.com/ellipsis-dev/blink), and
[Jev Code Finder](https://github.com/Peu77/JevFind) are relevant prior work.
The publishable contribution is a reproducible measurement of when
classifier-guided directory routing complements lexical search for annotated
repository evidence, including its quality, latency, cost, and failure modes.

## Locked run sequence

The pinned Parquet is downloaded to the gitignored
`evals/fixtures/external/contextbench/full.parquet` and verified by the
exporter. Run the source and corpus audit before any model retrieval. The
snapshot command may exit nonzero because it records invalid source cases;
inspect its complete ledger, then apply the prespecified audit rule without
replacing cases.

```bash
uv run --with pyarrow python -m evals.contextbench_holdout \
  --manifest evals/fixtures/queries/contextbench_confirmatory.json \
  --out evals/results/contextbench-confirmatory-holdout.json
uv run python -m evals.contextbench_snapshots \
  --holdout evals/results/contextbench-confirmatory-holdout.json \
  --out evals/results/contextbench-confirmatory-snapshots.json
uv run python -m evals.contextbench_audit \
  --holdout evals/results/contextbench-confirmatory-holdout.json \
  --snapshots evals/results/contextbench-confirmatory-snapshots.json \
  --out evals/results/contextbench-confirmatory-audited.json
uv run --with pyarrow python -m evals.contextbench_gold_audit \
  --manifest evals/fixtures/queries/contextbench_confirmatory.json \
  --holdout evals/results/contextbench-confirmatory-audited.json \
  --snapshots evals/results/contextbench-confirmatory-snapshots.json \
  --out evals/results/contextbench-confirmatory-gold-audit.json
uv run python -m evals.contextbench_tree \
  --holdout evals/results/contextbench-confirmatory-audited.json \
  --snapshots evals/results/contextbench-confirmatory-snapshots.json \
  --variant wide --top-k 16 --resume \
  --out evals/results/contextbench-confirmatory-tree.json
uv run python -m evals.contextbench_fts \
  --holdout evals/results/contextbench-confirmatory-audited.json \
  --snapshots evals/results/contextbench-confirmatory-snapshots.json --resume \
  --out evals/results/contextbench-confirmatory-fts5.json
uv run python -m evals.contextbench_lexical \
  --holdout evals/results/contextbench-confirmatory-audited.json \
  --snapshots evals/results/contextbench-confirmatory-snapshots.json --resume \
  --out evals/results/contextbench-confirmatory-lexical.json
uv run python -m evals.contextbench_rg \
  --holdout evals/results/contextbench-confirmatory-audited.json \
  --snapshots evals/results/contextbench-confirmatory-snapshots.json --resume \
  --out evals/results/contextbench-confirmatory-rg.json
uv run python -m evals.contextbench_study \
  --holdout evals/results/contextbench-confirmatory-audited.json \
  --snapshots evals/results/contextbench-confirmatory-snapshots.json \
  --tree evals/results/contextbench-confirmatory-tree.json \
  --fts5 evals/results/contextbench-confirmatory-fts5.json \
  --bm25 evals/results/contextbench-confirmatory-lexical.json \
  --rg evals/results/contextbench-confirmatory-rg.json \
  --gold-audit evals/results/contextbench-confirmatory-gold-audit.json \
  --out evals/results/contextbench-confirmatory-study.json
uv run python -m evals.contextbench_pipeline_runtime \
  --holdout evals/results/contextbench-confirmatory-audited.json \
  --snapshots evals/results/contextbench-confirmatory-snapshots.json --resume \
  --out evals/results/contextbench-confirmatory-runtime.json
```

The final command is a second, separately identified tree run that actually
builds FTS5 and fuses candidates in one timed pipeline. It measures warm
source setup and stochastic routing variance; it must not replace the
first tree run's predeclared quality result. Its code hash is recorded
before that timing run.
