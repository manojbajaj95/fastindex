# Amendment 002: post-review controls and annotation audit

Date: September 25, 2026. This extension began **after** the original
82-case study was scored and a draft manuscript reviewed. It is not part of
the original frozen design in [`study-amendment-001.md`](study-amendment-001.md).
Do not describe these controls as prespecified or use them to replace the
archived primary analysis.

All controls use the same 82 audited issue IDs, pinned pre-fix source trees,
eligible UTF-8 file policy, first 500 issue characters where stated, and
top-8/top-16 candidate and 16K-token context budgets. Gold annotations are
used only for the post-retrieval scorer.

1. **Path-only FTS5:** index only each eligible path, not file content, and
   search the same issue-derived token OR query as the original FTS5 arm.
   This isolates what a simple lexical path-name ranker can recover.
2. **Basename heuristic:** rank eligible paths by issue-token overlap,
   counting each basename match twice plus each full-path match once.
   Return only positive-score paths, breaking ties by path. This tests a
   still simpler path-name control without model calls.
3. **Flat Jev path tournament:** use the original `typesafe/jev-1.13.0`
   model and first 500 issue characters. Show all eligible paths as source
   file choices in SHA-256 order determined by issue ID and path. Each
   bounded Choice menu advances its top 16 paths; repeat until one top-16
   ranking remains. Menus contain at most 200 paths and respect the
   provider's request-character limit. This covers every eligible path but
   uses more model calls than the tree; it is a batched tournament, not a
   calibrated global probability score or a call-matched comparison.
   The Choice instruction is: “Choose the source file path most likely to
   contain direct evidence for the issue. Judge paths only; no file contents
   or directory summaries are available. Choose none only if no listed path
   could contain relevant evidence.” The request supplies no temperature
   setting. Save ranked outputs and
   usage; raw provider responses are not retained. A transient read timeout
   caused one first attempt on a large Material UI case to fail after
   unpersisted partial requests. The failed row was not scored or selected;
   that case was rerun from the beginning with up to three bounded retries
   per request. The archive reports usage for the successful run, not the
   failed partial attempt.
4. **Two-search `rg` feedback:** run the original full-issue literal search,
   then search for the five rarest issue terms found in the first search's
   matched files. Fuse the two file rankings with RRF at rank constant 60.
   This is a scripted feedback-based lexical control, not a coding agent.
5. **Annotation text audit:** classify archived mismatches mechanically as
   text elsewhere in the same file, text not found in that file, or empty
   annotation. These labels do not establish why the annotation differs.
   The mismatch flags are independent of retrieval arms.

The new rankings are scored by the same selector and packer as the original
study. Results must be reported separately and with their additional API
calls, tokens, estimated model charges, and observed latency. The full
path tournament is not meant to have the same operational budget as the
tree; that cost difference is part of the result.

## Reproduction

Recreate `evals/results/contextbench-paper-audited.json` and the paper
snapshot ledger with the commands in amendment 001. Then run:

```bash
uv run python -m evals.contextbench_fts \
  --holdout evals/results/contextbench-paper-audited.json \
  --snapshots evals/results/contextbench-paper-snapshots.json \
  --path-only --out evals/results/contextbench-paper-path-fts5.json
uv run python -m evals.contextbench_flat_paths \
  --holdout evals/results/contextbench-paper-audited.json \
  --snapshots evals/results/contextbench-paper-snapshots.json \
  --out evals/results/contextbench-paper-flat-paths.json
uv run python -m evals.contextbench_rg_feedback \
  --holdout evals/results/contextbench-paper-audited.json \
  --snapshots evals/results/contextbench-paper-snapshots.json \
  --out evals/results/contextbench-paper-rg-feedback.json
uv run --with pyarrow python -m evals.contextbench_annotation_audit \
  --holdout evals/results/contextbench-paper-audited.json \
  --snapshots evals/results/contextbench-paper-snapshots.json
uv run python -m evals.contextbench_poststudy \
  --holdout evals/results/contextbench-paper-audited.json \
  --snapshots evals/results/contextbench-paper-snapshots.json \
  --path-fts5 evals/results/contextbench-paper-path-fts5.json \
  --flat-jev evals/results/contextbench-paper-flat-paths.json \
  --rg-feedback evals/results/contextbench-paper-rg-feedback.json
```

The flat model run requires `TYPESAFE_API_KEY` and can differ on repetition.
Archived rankings under `paper/data/` allow the analysis to be rerun without
model calls after source snapshots are regenerated.

## Measured results

The completed extension has 82 rankings for each control and no scored
failures. All recall values below are unweighted issue means. Aligned-line
recall uses the 55 cases whose full annotated text aligns with pinned
source; all file values use 82 cases.

| Arm | File recall@8 | File recall@16 | Aligned-line recall@8 |
| --- | ---: | ---: | ---: |
| Original content + path FTS5 | 0.352 | 0.421 | 0.246 |
| Original fixed full-issue `rg` | 0.245 | 0.328 | 0.116 |
| Original name-only tree | 0.465 | 0.591 | 0.443 |
| Path-only FTS5 | 0.191 | 0.237 | 0.214 |
| Basename overlap | 0.173 | 0.219 | 0.226 |
| Two-search `rg` feedback | 0.260 | 0.316 | 0.209 |
| Flat Jev path tournament | 0.572 | 0.630 | 0.511 |

Flat Jev minus tree file recall@8 was +0.107 (95% repository-cluster
bootstrap interval 0.017 to 0.224); its aligned-line difference was +0.069
(0.006 to 0.130). At 16 files the file difference was +0.039 (-0.038 to
0.127). The higher quality came with 24.6 calls, 156,437 input tokens,
30.6 seconds, and estimated $0.00657 per completed issue, compared with
tree's 8.9 calls, 12,200 input tokens, 8.9 seconds, and estimated
$0.000512. The successful flat run had one transient request retry. The
earlier failed partial attempt's calls were not retained and are excluded
from those means; it does not change the rankings of the completed run.

Two-search `rg` minus fixed `rg` file recall@8 was +0.015 (-0.061 to
0.083), and its mean wall time was 2.67 seconds. It lost file recall at
16 candidates. Path-only FTS5 and basename overlap were both below
content + path FTS5 on file recall@8. These exploratory results support
model-guided path-name judgment as a source of candidate files. They do
not isolate a quality contribution from hierarchy: the flat control
used a different instruction and far more calls. A call- and
prompt-matched repeated comparison would be needed for that estimate.
