# tree-reason

Owned retrieval strategy: **LLM relevance-gated walk** over an OKF directory tree → evidence **spans**.

Inspired by [PageIndex](https://github.com/VectifyAI/PageIndex) (vectorless ToC descent). We do not ship Vectify; this is an OKF-native walker.

## Algorithm

1. `prepare` reads each UTF-8 file in bounded chunks and writes `index.md` from leaves to root. Each index lists its immediate subdirectories and files once, with a short model-written routing sentence per entry. A completed child index supplies the parent directory's entry; `log.md` is excluded. Existing nonempty indexes remain intact unless `--force` is given.
2. Query opens the root directory lazily, then schedules selected directory and file gates across up to `--parallelism` workers. It reads only visited indexes and selected files.
3. **Directory gate** — model sees the current `index.md` and immediate child names. Returns JSON:

   ```json
   {"relevant": true, "open_dirs": ["data/warehouse"], "open_concepts": [], "reason": "…"}
   ```

   Irrelevant → prune subtree. Bad / unparseable JSON → fail closed (open nothing).

4. **Section gate** — for each opened file, model returns line ranges:

   ```json
   {"relevant": true, "sections": [{"start_line": 11, "end_line": 18}], "reason": "…"}
   ```

   Relevant with empty sections → whole page. Materialize text from 1-based line ranges.

5. Cap output at `top_k` spans. Record ops: wall time, model calls, tokens, hops, depth, branching, `truncated`.

The call budget limits scheduled model calls. The wall budget is checked between batches; calls already running may finish after it. Several relevant branches can still make query cost grow with tree width, so this is not a worst-case logarithmic guarantee.

## Knobs

| Knob | Default | Source |
|------|---------|--------|
| `top_k` | 2 | CLI `--top-k` / `StrategyConfig` |
| Wall-time budget | 60s | `--wall-budget` |
| Model-call budget | 32 | `--call-budget` |
| Concurrent calls | 4 | `--parallelism` |
| Chat model | `FASTINDEX_MODEL` (required) | env; override with `FASTINDEX_TREE_REASON_MODEL` |
| Preparation model | `FASTINDEX_MODEL` | env; override with `FASTINDEX_PREPARE_MODEL` |
| Max completion tokens | 1024 | `FASTINDEX_MAX_TOKENS` |

Inject a fake chat model in tests via `StrategyConfig(extra={"model": fake})` or `TreeReasonStrategy(model=…)`.

## Constraints

- **Vectorless** — no `.fastindex/` sidecars required. Directory `index.md` files are the human-readable preparation artifact.
- **No wikilink hops** — parent→child dirs and in-doc headings only. Multi-hop bridges remain an [open question](../README.md#open-questions).
- **Spans, not answers** — retrieval returns `{path, start_line, end_line, text}`.

## Related

- Product CLI: `fastindex query`
- Bench: `python -m evals.bench --strategies tree-reason,…`
- Prior art: [LLM-Wiki](https://arxiv.org/abs/2605.25480), [OKF SPEC](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)

## Decision-model variants

`tree-jev`, `tree-laya`, and `tree-watt` walk the same prepared directory tree,
but send categorical choices over immediate children and page sections to a
decision API. Options contain the index entry or section preview; the model
returns a probability for each option instead of generating paths or line
ranges. The walk follows the strongest branch first and also keeps plausible
second branches for multi-page questions. Generation for `prepare` still uses
the configured LLM. For directory choices, it reads up to three levels of
bounded descendant index titles to make vague parent summaries more useful. A
directory with one child needs no model call.

Run `uv run fastindex query examples/sample-bundle "…" --strategy tree-jev` or
`uv run python -m evals.bench --strategies tree-reason,tree-jev,tree-laya,tree-watt`.
Jev and Laya use [classifier.dev](https://classifier.dev/); Watt uses
[WattAI](https://wattai.dev/#api). Their current hosted free routes need no key.
Set `WATTAI_API_KEY` for a higher Watt limit or `FASTINDEX_WATT_URL` for a
compatible endpoint. `FASTINDEX_CLASSIFIER_INSTRUCTIONS` overrides the Jev and
Laya classification instruction.

The APIs report no compatible token counts; `input_tokens` is a rough
character-based estimate and `cost_usd=0` reflects the current free routes. A
large node may require several bounded requests. The strategy selects one
section per opened page; if the model selects frontmatter, it returns the whole
page to retain factual sections. See the [matched evaluation](tree-decision-evaluation.md).
