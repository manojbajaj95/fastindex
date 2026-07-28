# tree-reason

Owned retrieval strategy: **LLM relevance-gated walk** over an OKF directory tree → evidence **spans**.

Inspired by [PageIndex](https://github.com/VectifyAI/PageIndex) (vectorless ToC descent). We do not ship Vectify; this is an OKF-native walker.

## Algorithm

1. DFS from bundle root (`""`).
2. **Directory gate** — model sees current `index.md` preview, child dirs, and concept frontmatter. Returns JSON:

   ```json
   {"relevant": true, "open_dirs": ["data/warehouse"], "open_concepts": [], "reason": "…"}
   ```

   Irrelevant → prune subtree. Bad / unparseable JSON → fail closed (open nothing).

3. **Section gate** — for each opened concept, model returns line ranges:

   ```json
   {"relevant": true, "sections": [{"start_line": 11, "end_line": 18}], "reason": "…"}
   ```

   Relevant with empty sections → whole page. Materialize text from 1-based line ranges.

4. Cap output at `top_k` spans. Record ops: wall time, model calls, tokens, hops, depth, branching, `truncated`.

## Knobs

| Knob | Default | Source |
|------|---------|--------|
| `top_k` | 2 | CLI `--top-k` / `StrategyConfig` |
| Wall-time budget | 60s | `--wall-budget` |
| Model-call budget | 32 | `--call-budget` |
| Chat model | `FASTINDEX_MODEL` (required) | env; override with `FASTINDEX_TREE_REASON_MODEL` |
| Max completion tokens | 1024 | `FASTINDEX_MAX_TOKENS` |

Inject a fake chat model in tests via `StrategyConfig(extra={"model": fake})` or `TreeReasonStrategy(model=…)`.

## Constraints

- **Cold / vectorless** — no `.fastindex/` sidecars required.
- **No wikilink hops** — parent→child dirs and in-doc headings only. Multi-hop bridges are planned (see [ROADMAP.md](../ROADMAP.md)).
- **Spans, not answers** — retrieval returns `{path, start_line, end_line, text}`.

## Related

- Product CLI: `fastindex query`
- Bench: `python -m evals.bench --strategies tree-reason,…`
- Prior art: [LLM-Wiki](https://arxiv.org/abs/2605.25480), [OKF SPEC](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
