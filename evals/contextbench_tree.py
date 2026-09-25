"""Paired name-only tree routing on the frozen ContextBench pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import time
from itertools import zip_longest
from pathlib import Path

from evals import RESULTS
from evals.contextbench_snapshots import HOLDOUT
from evals.tree_index_ablation import NameOnlyBundle
from fastindex.bundle import load_lazy_bundle
from fastindex.strategies import StrategyConfig
from fastindex.strategies.tree_decision import TreeDecisionStrategy, make_decision_model

SNAPSHOTS = RESULTS / "contextbench-snapshots.json"
VARIANTS = {"wide": (0.01, 0.01), "strict": (0.04, 0.05)}
DESCENDANT_VARIANTS = {"names0": (0.01, 0.01, 0),
                       "names12": (0.01, 0.01, 12)}


def _interleave(cases: list[dict]) -> list[dict]:
    by_repo: dict[str, list[dict]] = {}
    for case in cases:
        by_repo.setdefault(case["repo"], []).append(case)
    return [case for group in zip_longest(*by_repo.values())
            for case in group if case is not None]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", type=Path, default=HOLDOUT)
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOTS)
    parser.add_argument("--model", default="typesafe/jev-1.13.0")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--call-budget", type=int, default=32)
    parser.add_argument("--experiment", choices=("thresholds", "descendants"),
                        default="thresholds")
    parser.add_argument("--variant", choices=(*VARIANTS, *DESCENDANT_VARIANTS),
                        help="Run only one prespecified route; default runs both pilot variants")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    variants = VARIANTS if args.experiment == "thresholds" else DESCENDANT_VARIANTS
    if args.variant:
        if args.variant not in variants:
            parser.error("--variant must belong to --experiment")
        variants = {args.variant: variants[args.variant]}
    if args.out is None:
        name = ("contextbench-tree-names-paired.json" if args.experiment == "thresholds"
                else "contextbench-tree-descendants-paired.json")
        args.out = RESULTS / name
    if args.top_k < 1 or args.call_budget < 1 or args.limit < 0:
        parser.error("top-k and call budget must be positive; limit must be nonnegative")
    cases = _interleave(json.loads(args.holdout.read_text(encoding="utf-8"))["cases"])
    if args.limit:
        cases = cases[:args.limit]
    snapshots = {row["id"]: row for row in json.loads(
        args.snapshots.read_text(encoding="utf-8")
    )["cases"]}
    if any(case["id"] not in snapshots or snapshots[case["id"]]["errors"]
           for case in cases):
        parser.error("missing or invalid source snapshot for a selected case")
    config = {
        "holdout_sha256": hashlib.sha256(args.holdout.read_bytes()).hexdigest(),
        "model": args.model, "top_k": args.top_k, "call_budget": args.call_budget,
        "variants": variants,
    }
    previous = {}
    if args.resume and args.out.exists():
        saved = json.loads(args.out.read_text(encoding="utf-8"))
        if saved["config"] != config:
            parser.error("saved run settings differ; choose another --out")
        previous = {(row["id"], row["variant"]): row for row in saved["results"]
                    if "error" not in row}
    model = make_decision_model(args.model)
    results = []
    for number, case in enumerate(cases):
        root = Path(snapshots[case["id"]]["root"])
        actual = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                check=True, capture_output=True, text=True).stdout.strip()
        if actual != case["base_commit"]:
            raise ValueError(f"Snapshot commit changed: {root}")
        bundle = NameOnlyBundle(load_lazy_bundle(root))
        gold_paths = {span["path"] for span in case["gold"]}
        labels = list(variants)
        order = labels if number % 2 == 0 else list(reversed(labels))
        for variant in order:
            key = case["id"], variant
            if key in previous:
                row = previous[key]
            else:
                try:
                    minimum, relative, *limit = variants[variant]
                    descendant_limit = limit[0] if limit else 12
                    started = time.perf_counter()
                    retrieval = TreeDecisionStrategy(model).retrieve(
                        case["query"], bundle,
                        StrategyConfig(
                            top_k=args.top_k, wall_time_budget_s=180,
                            model_call_budget=args.call_budget,
                            extra={"decision_return_files": True,
                                   "decision_min_probability": minimum,
                                   "decision_relative_probability": relative,
                                   "decision_descendant_limit": descendant_limit},
                        ),
                    )
                    paths = [span.path for span in retrieval.spans]
                    row = {
                        "id": case["id"], "repo": case["repo"], "variant": variant,
                        "paths": paths,
                        "file_recall": len(gold_paths.intersection(paths)) / len(gold_paths),
                        "complete_files": int(gold_paths <= set(paths)),
                        "latency_ms": (time.perf_counter() - started) * 1000,
                        "model_calls": retrieval.stats.model_calls,
                        "input_tokens": retrieval.stats.input_tokens,
                        "output_tokens": retrieval.stats.output_tokens,
                        "cost_usd": retrieval.stats.estimated_cost_usd,
                        "truncated": retrieval.stats.truncated,
                    }
                except Exception as exc:
                    row = {"id": case["id"], "repo": case["repo"],
                           "variant": variant, "error": str(exc)}
            results.append(row)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.out.with_suffix(args.out.suffix + ".tmp")
            temporary.write_text(json.dumps({"config": config, "results": results},
                                            indent=2), encoding="utf-8")
            temporary.replace(args.out)
            print(f"{len(results)}/{len(cases) * len(variants)} {case['repo']} "
                  f"{variant}: {row.get('file_recall', row.get('error'))}", flush=True)
    for variant in variants:
        valid = [row for row in results if row["variant"] == variant
                 and "error" not in row]
        if valid:
            print(f"{variant}: recall={statistics.mean(row['file_recall'] for row in valid):.3f} "
                  f"complete={sum(row['complete_files'] for row in valid)}/{len(valid)} "
                  f"errors={len(cases) - len(valid)}")
    return int(any("error" in row for row in results))


if __name__ == "__main__":
    raise SystemExit(main())
