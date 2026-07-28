"""LLM rerank over candidates from another strategy."""

from __future__ import annotations

import json
import re

from evals.baselines import StrategyConfig, StrategyConstraints, register
from evals.registry import get_strategy
from fastindex.bundle import Bundle
from fastindex.models import RemoteModel, estimate_cost_usd
from fastindex.types import RetrieveResult, RunStats, Span

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


@register
class RerankStrategy:
    name = "rerank"
    constraints = StrategyConstraints(
        requires_llm=True,
        allows_cold=True,
        track="vectorless",
    )

    def retrieve(self, query: str, bundle: Bundle, cfg: StrategyConfig) -> RetrieveResult:
        base_name = cfg.extra.get("candidate_strategy") or "bm25"
        base = get_strategy(base_name)
        base_cfg = StrategyConfig(
            top_k=max(cfg.top_k * 3, 12),
            wall_time_budget_s=cfg.wall_time_budget_s,
            model_call_budget=cfg.model_call_budget,
        )
        base_result = base.retrieve(query, bundle, base_cfg)
        candidates = base_result.spans
        if not candidates:
            return RetrieveResult(spans=[], stats=base_result.stats)

        model = RemoteModel()
        if not model.available:
            return RetrieveResult(spans=candidates[: cfg.top_k], stats=base_result.stats)

        payload = {
            "query": query,
            "candidates": [
                {
                    "i": i,
                    "path": s.path,
                    "start_line": s.start_line,
                    "end_line": s.end_line,
                    "preview": s.text[:500],
                }
                for i, s in enumerate(candidates)
            ],
            "instructions": (
                f"Rank the most relevant candidates. Return JSON: "
                f'{{"order": [indices], "reason": "short"}} with up to {cfg.top_k} indices.'
            ),
        }
        chat = model.chat(
            [
                {
                    "role": "system",
                    "content": "You rerank retrieval candidates. Reply with JSON only.",
                },
                {"role": "user", "content": json.dumps(payload)},
            ]
        )
        try:
            text = chat.text.strip()
            data = json.loads(text)
        except json.JSONDecodeError:
            m = _JSON_RE.search(chat.text)
            if m:
                data = json.loads(m.group(0))
            else:
                data = {"order": list(range(min(cfg.top_k, len(candidates))))}

        order = data.get("order") or []
        spans: list[Span] = []
        for i in order:
            if isinstance(i, int) and 0 <= i < len(candidates):
                spans.append(candidates[i])
            if len(spans) >= cfg.top_k:
                break
        if not spans:
            spans = candidates[: cfg.top_k]

        stats = RunStats(
            model_calls=base_result.stats.model_calls + chat.usage.calls,
            input_tokens=base_result.stats.input_tokens + chat.usage.input_tokens,
            output_tokens=base_result.stats.output_tokens + chat.usage.output_tokens,
            estimated_cost_usd=base_result.stats.estimated_cost_usd
            + estimate_cost_usd(
                chat.usage.input_tokens, chat.usage.output_tokens, chat.usage.model_id
            ),
            hops=base_result.stats.hops,
            nodes_visited=base_result.stats.nodes_visited,
            max_depth=base_result.stats.max_depth,
            truncated=base_result.stats.truncated,
            track=base_result.stats.track,
            model_id=chat.usage.model_id,
            extra={"candidate_strategy": base_name},
        )
        return RetrieveResult(spans=spans, stats=stats)
