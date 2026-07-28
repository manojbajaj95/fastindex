#!/usr/bin/env python3
"""Local bench harness under evals/ (not a CLI subcommand).

Defaults to tree-reason (owned) plus lexical baselines. Writes JSON under
``evals/results/`` (gitignored — local only; do not commit runs).

Usage:
  uv run python -m evals.bench
  uv run python -m evals.bench --strategies tree-reason,bm25,fts \\
    --fixtures evals/fixtures/queries/multi_hop.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from evals import RESULTS, SAMPLE_BUNDLE, SAMPLE_QUERIES
from evals.baselines.cognee_kg import ensure_cognee_index
from evals.index import build_index, is_fresh, read_meta
from evals.metrics import GoldSpan, build_metrics
from evals.registry import get_strategy, list_strategies
from fastindex.bundle import load_bundle
from fastindex.strategies import StrategyConfig


def load_fixtures(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(json.loads(line))
    return rows


def ensure_vsearch_index(bundle: Path) -> bool:
    """Build .fastindex/ with embeddings if needed for vsearch. Returns True if ready."""
    from fastindex.models import RemoteModel

    meta = read_meta(bundle)
    if is_fresh(bundle) and meta and meta.embeddings:
        return True
    if not RemoteModel().available:
        print(
            "SKIP vsearch: LLM credentials required to build embeddings",
            file=sys.stderr,
        )
        return False
    print(f"Building index with embeddings for {bundle} …", file=sys.stderr)
    meta = build_index(bundle, force=True, with_embeddings=True)
    if not meta.embeddings:
        print("SKIP vsearch: embedding build failed", file=sys.stderr)
        return False
    print(f"Index ready (hash={meta.content_hash[:12]}…)", file=sys.stderr)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="fastindex local bench")
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=SAMPLE_QUERIES,
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        default=SAMPLE_BUNDLE,
    )
    parser.add_argument(
        "--strategies",
        default="tree-reason,bm25,fts",
        help="Comma-separated strategy names (default: tree-reason + lexical baselines)",
    )
    parser.add_argument("--top-k", type=int, default=2)
    parser.add_argument("--wall-budget", type=float, default=60.0)
    parser.add_argument("--call-budget", type=int, default=32)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write JSON results (default evals/results/bench-<ts>.json)",
    )
    parser.add_argument(
        "--skip-missing-constraints",
        action="store_true",
        default=True,
        help="Skip strategies whose constraints are unmet (default)",
    )
    args = parser.parse_args()

    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    list_strategies()

    bundle = load_bundle(args.bundle)
    fixtures = load_fixtures(args.fixtures)

    vsearch_ready = False
    if "vsearch" in strategies:
        vsearch_ready = ensure_vsearch_index(args.bundle)

    cognee_ready = False
    if "cognee" in strategies:
        cognee_ready = ensure_cognee_index(args.bundle)

    warm = is_fresh(args.bundle)

    results: list[dict] = []
    for strat_name in strategies:
        try:
            strat = get_strategy(strat_name)
        except KeyError as e:
            print(f"SKIP {strat_name}: {e}", file=sys.stderr)
            continue

        c = strat.constraints
        if strat_name == "vsearch":
            if not vsearch_ready:
                results.append(
                    {
                        "strategy": strat_name,
                        "skipped": True,
                        "reason": "requires_embeddings",
                        "metrics": {"track": "cold"},
                    }
                )
                continue
        elif strat_name == "cognee":
            if not cognee_ready:
                results.append(
                    {
                        "strategy": strat_name,
                        "skipped": True,
                        "reason": "requires_cognee_index",
                        "metrics": {"track": "cold"},
                    }
                )
                continue
        elif c.requires_index and strat_name == "qmd":
            if not warm:
                msg = f"SKIP {strat_name}: requires warm index (evals.bench builds sidecars)"
                print(msg, file=sys.stderr)
                results.append(
                    {
                        "strategy": strat_name,
                        "skipped": True,
                        "reason": "requires_index",
                        "metrics": {"track": "cold"},
                    }
                )
                continue
        if c.requires_llm:
            from fastindex.models import RemoteModel

            if not RemoteModel().available:
                print(f"SKIP {strat_name}: LLM credentials not configured", file=sys.stderr)
                results.append(
                    {
                        "strategy": strat_name,
                        "skipped": True,
                        "reason": "requires_llm",
                    }
                )
                continue

        cfg = StrategyConfig(
            top_k=args.top_k,
            wall_time_budget_s=args.wall_budget,
            model_call_budget=args.call_budget,
        )

        for fix in fixtures:
            qid = fix.get("id") or fix["query"][:40]
            query = fix["query"]
            gold = [
                GoldSpan(
                    path=g["path"],
                    start_line=int(g["start_line"]),
                    end_line=int(g["end_line"]),
                    text=g.get("text"),
                )
                for g in fix.get("gold", [])
            ]
            try:
                t0 = time.perf_counter()
                out = strat.retrieve(query, bundle, cfg)
                latency_ms = (time.perf_counter() - t0) * 1000
                metrics = build_metrics(out.stats, out.spans, gold, latency_ms=latency_ms)
                row = {
                    "id": qid,
                    "query": query,
                    "strategy": strat_name,
                    "skipped": False,
                    **metrics.to_bench_fields(),
                    "n_spans": len(out.spans),
                    "spans": [s.to_dict() for s in out.spans],
                }
                results.append(row)
                print(
                    f"{strat_name:12} {qid:20} "
                    f"rec={metrics.span_recall:.2f} f1={metrics.span_f1:.2f} "
                    f"lat={metrics.latency_ms:7.1f}ms "
                    f"${metrics.cost_usd:.4f} {metrics.track}"
                )
            except Exception as e:
                print(f"FAIL {strat_name} {qid}: {e}", file=sys.stderr)
                results.append(
                    {
                        "id": qid,
                        "query": query,
                        "strategy": strat_name,
                        "skipped": False,
                        "error": str(e),
                    }
                )

    out_path = args.out
    if out_path is None:
        RESULTS.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out_path = RESULTS / f"bench-{ts}.json"

    payload = {
        "recorded": ["retrieval_quality", "ops", "setup"],
        "metrics": {
            "retrieval_quality": [
                "span_recall",
                "span_precision",
                "span_f1",
                "line_recall",
                "line_precision",
                "line_f1",
            ],
            "ops": [
                "latency_ms",
                "model_calls",
                "input_tokens",
                "output_tokens",
                "cost_usd",
                "hops",
            ],
            "setup": ["track", "model_id", "truncated"],
        },
        "bundle": str(args.bundle),
        "fixtures": str(args.fixtures),
        "strategies": strategies,
        "results": results,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {out_path} (local only; evals/results/ is gitignored)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
