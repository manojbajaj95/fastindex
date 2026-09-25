# Budget amendment 001: one paired ContextBench evaluation

September 24, 2026. The author requested fewer evaluation runs while the
first tree pass was in progress. This amendment supersedes the original
171-case study plan **before any lexical ranking on the independent cohort**.
Interim tree values had been printed by the runner, so this is not an
externally preregistered or fully blinded reduction. The subset rule uses
only the original case-ID order, not any tree outcome; that distinction
must be disclosed in the paper.

## Fixed reduced cohort

- Keep the first eight SHA-256-ordered IDs per repository from the
  [original 171-case manifest](../evals/fixtures/queries/contextbench_confirmatory.json).
  PonyC has only seven. This gives 87 selected issues across the same 11
  repositories; do not backfill source-audit exclusions.
- Apply the unchanged source/corpus audit: 82/87 remain. Five exclusions
  are recorded in the local audit ledger. Of the 82, 55 have all annotated
  text spans aligned to pinned source under the predeclared normalization.
- The [reduced manifest](../evals/fixtures/queries/contextbench_paper.json)
  has SHA-256
  `42c470c29df04c04cc3072aae76ba29e42b8b270bf1e9d77b7785ab2c3918f43`.
  There are 16 cases with more than eight gold files and two with more
  than sixteen, so complete-set rates need those ceilings.
- Reuse the already completed tree output for all 82 retained IDs, without
  rerunning or choosing the best stochastic attempt. The derived tree
  artifact records the SHA-256 of the original partial tree run
  (`ed4afb175f3fbd3174ff483872507a162d4a95b30cb63ae37b264056a40bdaa4`)
  and has SHA-256
  `42fdaeb46d6a7816978003fa9131dd50d6aeb7950dad9f5aa1414b20b8c8a4f9`.

## One evaluation, minimal arms

The study now has one benchmark and one paired analysis on the same 82
issues. The [reduced design freeze](reduced-freeze.json) records exact
source and parent-artifact hashes before the reduced-cohort lexical
runs. The arms are:

1. Name-only Jev tree routing, top 16, already measured.
2. Actual `rg` over the same searchable paths with the fixed full-issue
   literal-term/path-aware file ranking.
3. Pilot-selected FTS5 over the first 500 issue characters, top 16.
4. Offline equal-budget RRF of tree and FTS5, truncated to top 8 or 16.

The primary endpoint is macro gold-file recall@8/@16. Also report
complete gold-file sets with attainable ceilings, gold files uniquely
found by tree versus FTS5 and versus `rg`, and equal-budget fusion gains
or losses. Score all arms through the same four-anchor, ±200-line merged
window selector and 16K-token packer; report gold-line recall for all 82
and the 55-case aligned-text sensitivity subset. Pair comparisons and
repository-cluster intervals remain prespecified.

Do **not** run BM25, a second tree/pipeline timing repeat, chat/flat-path
gates, 8K/32K context sweeps, or a coding-agent experiment for this
preprint. Report measured per-arm wall time, model calls/tokens, estimated
Jev cost, FTS5 build/query time, and `rg` search time, but do not claim
measured end-to-end fusion latency or downstream issue-resolution gains.
The first tree run's partial extra cases are not included in paper
outcomes.

During the `rg` replay, the JSON event parser failed after 86 of 164
query-mode rows because Python's `splitlines()` treated a Unicode line
separator inside a matched source line as an event boundary. We changed
the parser to split only on LF and added a regression test. The resume
configuration also needed a JSON-stable list rather than a tuple; this
allows the same saved run to continue. These repairs do not change query
terms, paths, ranking, or the selected cohort.

## Reproduction

The archived rank and gold-text audit files in [`paper/data/`](data/README.md)
allow the reported scores to be recomputed **without another model run**.
From a fresh checkout, first download the [pinned ContextBench
`full.parquet`](https://huggingface.co/datasets/Contextbench/ContextBench/blob/c2855792b006af41c67202d33883fb9d46362853/data/full.parquet)
to the gitignored path below. Its SHA-256 is
`2f56535bdc73eb8a68bf4ebb49789d8e9cd4f219ea60df6290b85278aee61ca8`.
The selected-case exporter independently checks that hash. Source snapshots
require Git network access and disk space; they are pinned to each issue's
pre-fix commit. Run these commands from the repository root:

```bash
uv sync --extra evals
mkdir -p evals/fixtures/external/contextbench
curl -fL 'https://huggingface.co/datasets/Contextbench/ContextBench/resolve/c2855792b006af41c67202d33883fb9d46362853/data/full.parquet?download=true' \
  --output evals/fixtures/external/contextbench/full.parquet
shasum -a 256 evals/fixtures/external/contextbench/full.parquet
uv run --with pyarrow python -m evals.contextbench_holdout \
  --manifest evals/fixtures/queries/contextbench_paper.json \
  --out evals/results/contextbench-paper-holdout.json
uv run python -m evals.contextbench_snapshots \
  --holdout evals/results/contextbench-paper-holdout.json \
  --out evals/results/contextbench-paper-snapshots.json
uv run python -m evals.contextbench_audit \
  --holdout evals/results/contextbench-paper-holdout.json \
  --snapshots evals/results/contextbench-paper-snapshots.json \
  --out evals/results/contextbench-paper-audited.json
uv run python -m evals.contextbench_study \
  --holdout evals/results/contextbench-paper-audited.json \
  --snapshots evals/results/contextbench-paper-snapshots.json \
  --tree paper/data/contextbench-paper-tree.json \
  --fts5 paper/data/contextbench-paper-fts5.json \
  --rg paper/data/contextbench-paper-rg.json \
  --gold-audit paper/data/contextbench-paper-gold-audit.json \
  --out evals/results/contextbench-paper-rescored.json
```

The scorer requires the reconstructed audited holdout to have the same
SHA-256, `28b0f8961bf191da6e504b82df13bdabacca006565a901f52cedd40bb94e330e`,
as the original. Compare its metrics with
[`paper/data/contextbench-paper-study.json`](data/contextbench-paper-study.json).
The rescored JSON's *own* hash need not match: the snapshot ledger records
machine-specific paths and setup times, and its hash is embedded in the
scorer's provenance. The original 171-case snapshot ledger and unfinished
tree pass are not needed for this no-API re-score.

This path was checked locally on September 24, 2026: a fresh 87-case
holdout and snapshot ledger produced the original 82-case audited-holdout
hash. Re-scoring the archived rankings yielded the same cohort, summary,
paired intervals, per-case scores, and unique-file counts as the archived
study JSON. After excluding the machine-specific `config` provenance, both
JSON files had canonical SHA-256
`2fb4ef6e7705a2dabf106434cfd87b37518fa63329752b54382a94859917f681`.
This is an analysis reproduction from archived rankings, not a second
retrieval evaluation.

To independently rerun the lexical arms, use the same audited holdout and
new snapshot ledger with `evals.contextbench_fts` and
`evals.contextbench_rg`; compare their ranked paths, not machine-dependent
timings. Rerunning the stochastic tree requires a TypeSafe credential and
may return different paths. The archived first tree run, not a selected
rerun, defines the paper's result. Local outputs remain gitignored under
`evals/results/`; do not commit source snapshots or credentials.
