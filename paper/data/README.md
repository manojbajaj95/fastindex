# Measured results for the reduced ContextBench study

The original JSON files are copies of the first completed 82-case paired run.
They contain case IDs, repository labels, ranked paths, measurements,
gold-text alignment flags, and aggregate scores; they do not contain
source snapshots or credentials. The selected-case manifest is
[`evals/fixtures/queries/contextbench_paper.json`](../../evals/fixtures/queries/contextbench_paper.json).
The 82 retained IDs, base commits, query hashes, gold ranges, and five
exclusions are also listed directly in `contextbench-case-ledger.json`.

| File | SHA-256 |
| --- | --- |
| `contextbench-paper-tree.json` | `42fdaeb46d6a7816978003fa9131dd50d6aeb7950dad9f5aa1414b20b8c8a4f9` |
| `contextbench-paper-fts5.json` | `712363e57150e36192bdc2a4d77bd1704b6252f0378e07c10aa9b51d839aafdc` |
| `contextbench-paper-rg.json` | `1468433b13a6494fbca54f69cd5a7782d30e39aa2e9651114680dbafe281649a` |
| `contextbench-paper-gold-audit.json` | `e15508a2414b4a467c1942ffafd1bf0e914a5b589106524b4ba66d394ddd5e85` |
| `contextbench-paper-study.json` | `51db1045e6db3c2ca3268ea932ce9625bbd4f2cac921b38a5300387890cde274` |
| `contextbench-case-ledger.json` | `35dda1447560931ae5316164af90bd33631be02478ff33d3941ec977ede8e4aa` |

The following outputs are **post-study** controls or audits, added after
review of the original manuscript. They do not alter the original paired
run or its prespecified analysis.

| File | SHA-256 |
| --- | --- |
| `contextbench-annotation-audit.json` | `36764f0ee487640d1fc8bfdca8deab7fd0d67815b8a77798f28307fb035e96d0` |
| `contextbench-paper-path-fts5.json` | `76bbd27966bdcb0784776b63a38498e2e7ba13ff68434901dd9d97f8fd69e93f` |
| `contextbench-paper-rg-feedback.json` | `7b987ebed784060f3b53f137459898b5df44e9d1bf05ae420f843d591d3abb86` |
| `contextbench-paper-flat-paths.json` | `9b0a085e8623a5060ecddee4bfdcced444e8fd5249e8c5cda93ea66684624fa8` |
| `contextbench-paper-poststudy.json` | `eb6fcf1dd61d2d3f9ea11933edb942a1e680344a99d25784ab702dbb7f4372ec` |

The audited holdout and pinned source snapshots remain regenerable
local outputs under `evals/results/`; the exact commands and reduction
rule are in [`paper/study-amendment-001.md`](../study-amendment-001.md).
The paper discloses that the cohort was reduced after interim tree
output, before lexical rankings.

The amendment also gives a no-model-call path to re-score these archived
rankings. A fresh local re-score matched every study field except
machine-specific input-file provenance.

The manuscript's additional three-way overlap count is descriptive: for
each audited case, take annotated gold paths in the tree's first eight
but in neither FTS5 `first500` nor `rg` `full` first-eight paths. This
gives 57 issue-file occurrences across 37 cases. It was computed from
the archived rankings and audited gold paths, not a new retrieval run.
