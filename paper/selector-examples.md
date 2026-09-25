# Shared line-selector examples

The selector implementation is [`_merged_match_spans`](../evals/arb_oracle_span_ablation.py)
with `radius=200`, `anchors_per_file=4`; [`_pack`](../evals/arb_oracle_span_ablation.py)
then counts `cl100k_base` tokens in line-numbered blocks. Both run independently
for every retrieval arm. They receive the issue text and ranked pre-fix files,
not annotated paths, ranges, text, or patches.

These are actual input/output pairs from the archived ContextBench study.
The issue text and base commit for each ID are in the selected-case manifest
and the regenerated audited holdout (reproduction commands in
[`study-amendment-001.md`](study-amendment-001.md)). The listed files are
from the archived tree ranking, but the selector does not know which arm
provided them.

| Issue ID suffix | Input file | Top four matching lines (line: distinct issue terms) | Selected range(s) before token packing |
| --- | --- | --- | --- |
| `b01e9113` | `src/transformers/models/idefics/modeling_idefics.py` (1,579 lines) | 1463:2, 1464:2, 2:1, 20:1 | 1263–1579, then 1–220 |
| `37b10945` | `src/transformers/utils/constants.py` (6 lines) | no matching lines | whole file, 1–6 |

The first example shows that merged windows remain in match-score order,
even when their source-line order differs. The second shows the whole-file
fallback when no issue term occurs in a retrieved file. A range that would
exceed the remaining 16K-token budget is skipped; later ranges are still
considered. Gold annotations are used only after packing to score the output.
