"""Frozen lexical file-ranking controls on the ContextBench pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path

from evals import RESULTS
from evals.arb_trace_ablation import _metrics, _rankings
from evals.contextbench_snapshots import HOLDOUT
from fastindex.bundle import load_lazy_bundle

SNAPSHOTS = RESULTS / "contextbench-snapshots.json"
QUERY_MODES = ("full", "first500")
RANKERS = ("literal", "literal+path", "bm25", "fusion")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", type=Path, default=HOLDOUT)
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOTS)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--out", type=Path,
                        default=RESULTS / "contextbench-lexical-paired.json")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("limit must be nonnegative")
    cases = json.loads(args.holdout.read_text(encoding="utf-8"))["cases"]
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
        "query_modes": QUERY_MODES, "rankers": RANKERS, "output_k": 16,
    }
    previous = {}
    if args.resume and args.out.exists():
        saved = json.loads(args.out.read_text(encoding="utf-8"))
        if saved["config"] != config:
            parser.error("saved run settings differ; choose another --out")
        previous = {(row["id"], row["query_mode"]): row for row in saved["results"]
                    if "error" not in row}
    results = []
    for case in cases:
        missing_modes = [mode for mode in QUERY_MODES if (case["id"], mode) not in previous]
        if missing_modes:
            started = time.perf_counter()
            bundle = load_lazy_bundle(snapshots[case["id"]]["root"])
            files = {concept.path: concept.raw for concept in bundle.iter_concepts()}
            load_ms = (time.perf_counter() - started) * 1000
            gold_paths = {span["path"] for span in case["gold"]}
            if missing := gold_paths - files.keys():
                raise ValueError(f"Gold files not searchable in {case['id']}: {sorted(missing)}")
        for mode in QUERY_MODES:
            if (case["id"], mode) in previous:
                row = previous[case["id"], mode]
            else:
                try:
                    query = case["query"] if mode == "full" else case["query"][:500]
                    started = time.perf_counter()
                    ranked = _rankings(query, files)
                    ranking_ms = (time.perf_counter() - started) * 1000
                    row = {
                        "id": case["id"], "repo": case["repo"], "query_mode": mode,
                        "gold_paths": sorted(gold_paths), "file_count": len(files),
                        "query_chars": len(query), "load_ms": load_ms,
                        "ranking_ms": ranking_ms,
                        "rankings": {name: paths[:16] for name, paths in ranked.items()},
                    }
                except Exception as exc:
                    row = {"id": case["id"], "repo": case["repo"],
                           "query_mode": mode, "error": str(exc)}
            results.append(row)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.out.with_suffix(args.out.suffix + ".tmp")
            temporary.write_text(json.dumps({"config": config, "results": results},
                                            indent=2), encoding="utf-8")
            temporary.replace(args.out)
            print(f"{len(results)}/{len(cases) * len(QUERY_MODES)} "
                  f"{case['repo']} {mode}: "
                  f"{row.get('ranking_ms', row.get('error'))}", flush=True)
    valid = [row for row in results if "error" not in row]
    for mode in QUERY_MODES:
        selected = [row for row in valid if row["query_mode"] == mode]
        if selected:
            metrics = _metrics(selected, "literal", 8)
            print(f"{mode}: literal@8={metrics['file_recall']:.3f} "
                  f"mean ranking={statistics.mean(row['ranking_ms'] for row in selected):.0f} ms")
    return int(any("error" in row for row in results))


if __name__ == "__main__":
    raise SystemExit(main())
