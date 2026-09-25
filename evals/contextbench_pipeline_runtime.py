"""Time the actual tree + FTS5 + RRF pipeline on an audited cohort.

This is a separate warm-source repeat, not a sum of component timers and not
the primary quality run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import subprocess
import time
from pathlib import Path

from evals import RESULTS
from evals.contextbench_fts import _rank
from evals.contextbench_tree import _interleave
from evals.hybrid import _rrf
from evals.tree_index_ablation import NameOnlyBundle
from fastindex.bundle import load_lazy_bundle
from fastindex.strategies import StrategyConfig
from fastindex.strategies.tree_decision import TreeDecisionStrategy, make_decision_model


def _run_case(case: dict, root: Path, strategy: TreeDecisionStrategy) -> dict:
    started = time.perf_counter()
    bundle = load_lazy_bundle(root)
    route = strategy.retrieve(
        case["query"], NameOnlyBundle(bundle),
        StrategyConfig(
            top_k=16, wall_time_budget_s=180, model_call_budget=32,
            extra={
                "decision_return_files": True,
                "decision_min_probability": 0.01,
                "decision_relative_probability": 0.01,
                "decision_descendant_limit": 12,
            },
        ),
    )
    tree_ms = (time.perf_counter() - started) * 1000
    build_start = time.perf_counter()
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE files USING fts5(path, content)")
        connection.executemany(
            "INSERT INTO files (path, content) VALUES (?, ?)",
            ((concept.path, concept.raw) for concept in bundle.iter_concepts()),
        )
        file_count = connection.execute("SELECT count(*) FROM files").fetchone()[0]
        build_ms = (time.perf_counter() - build_start) * 1000
        query_start = time.perf_counter()
        lexical_paths = _rank(connection, case["query"][:500])
        query_ms = (time.perf_counter() - query_start) * 1000
    finally:
        connection.close()
    fusion_start = time.perf_counter()
    tree_paths = [span.path for span in route.spans]
    fused, _ = _rrf({"tree": tree_paths, "fts5": lexical_paths})
    fusion_ms = (time.perf_counter() - fusion_start) * 1000
    return {
        "id": case["id"], "repo": case["repo"],
        "tree_paths": tree_paths, "fts5_paths": lexical_paths,
        "fused_paths": fused[:16], "file_count": file_count,
        "total_ms": (time.perf_counter() - started) * 1000,
        "tree_ms": tree_ms, "fts5_build_ms": build_ms,
        "fts5_query_ms": query_ms, "fusion_ms": fusion_ms,
        "model_calls": route.stats.model_calls,
        "input_tokens": route.stats.input_tokens,
        "output_tokens": route.stats.output_tokens,
        "estimated_cost_usd": route.stats.estimated_cost_usd,
        "truncated": route.stats.truncated,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--model", default="typesafe/jev-1.13.0")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--out", type=Path,
                        default=RESULTS / "contextbench-confirmatory-runtime.json")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be nonnegative")
    cases = _interleave(json.loads(args.holdout.read_text(encoding="utf-8"))["cases"])
    if args.limit:
        cases = cases[:args.limit]
    snapshots = {row["id"]: row for row in json.loads(args.snapshots.read_text(
        encoding="utf-8"))["cases"]}
    if any(case["id"] not in snapshots or snapshots[case["id"]]["errors"]
           for case in cases):
        parser.error("Missing or invalid source snapshot")
    config = {
        "holdout_sha256": hashlib.sha256(args.holdout.read_bytes()).hexdigest(),
        "model": args.model, "top_k": 16, "call_budget": 32,
        "thresholds": [0.01, 0.01], "descendant_limit": 12,
        "fts5_query_chars": 500, "fts5_index": "in-memory SQLite FTS5",
        "fusion": "RRF constant 60", "limit": args.limit,
    }
    previous = {}
    if args.resume and args.out.exists():
        saved = json.loads(args.out.read_text(encoding="utf-8"))
        if saved["config"] != config:
            parser.error("Saved run settings differ; choose another --out")
        previous = {row["id"]: row for row in saved["results"]
                    if "error" not in row}
    strategy = TreeDecisionStrategy(make_decision_model(args.model))
    rows = []
    for case in cases:
        if case["id"] in previous:
            row = previous[case["id"]]
        else:
            root = Path(snapshots[case["id"]]["root"])
            try:
                actual = subprocess.run(
                    ["git", "-C", str(root), "rev-parse", "HEAD"],
                    check=True, capture_output=True, text=True,
                ).stdout.strip()
                if actual != case["base_commit"]:
                    raise ValueError(f"Snapshot commit changed: {root}")
                row = _run_case(case, root, strategy)
            except Exception as exc:
                row = {"id": case["id"], "repo": case["repo"], "error": str(exc)}
        rows.append(row)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.out.with_suffix(args.out.suffix + ".tmp")
        temporary.write_text(json.dumps({"config": config, "results": rows},
                                        indent=2), encoding="utf-8")
        temporary.replace(args.out)
        print(f"{len(rows)}/{len(cases)} {case['repo']}: "
              f"{row.get('total_ms', row.get('error'))}", flush=True)
    valid = [row for row in rows if "error" not in row]
    if valid:
        times = sorted(row["total_ms"] for row in valid)
        print(f"n={len(valid)}, median={statistics.median(times):.0f} ms, "
              f"p95={times[int(.95 * (len(times) - 1))]:.0f} ms, "
              f"mean cost=${statistics.mean(row['estimated_cost_usd'] for row in valid):.6f}")
    return int(len(valid) != len(cases))


if __name__ == "__main__":
    raise SystemExit(main())
