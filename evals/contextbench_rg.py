"""Measure actual ripgrep file retrieval on frozen ContextBench snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path

from evals import RESULTS
from evals.baselines.ripgrep import rank_rg, rg_hits
from evals.contextbench_snapshots import HOLDOUT
from fastindex.bundle import load_lazy_bundle

SNAPSHOTS = RESULTS / "contextbench-snapshots.json"
BATCH = 256  # Keep command-line argument length below common OS limits.


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", type=Path, default=HOLDOUT)
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOTS)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--out", type=Path,
                        default=RESULTS / "contextbench-rg-paired.json")
    args = parser.parse_args()
    cases = json.loads(args.holdout.read_text(encoding="utf-8"))["cases"]
    if args.limit:
        cases = cases[:args.limit]
    snapshots = {row["id"]: row for row in json.loads(
        args.snapshots.read_text(encoding="utf-8"))["cases"]}
    config = {"holdout_sha256": hashlib.sha256(args.holdout.read_bytes()).hexdigest(),
              "query_modes": ["full", "first500"], "batch_size": BATCH,
              "ranker": "literal terms with rarity and path boost"}
    previous = {}
    if args.resume and args.out.exists():
        saved = json.loads(args.out.read_text(encoding="utf-8"))
        if saved["config"] != config:
            parser.error("saved run settings differ; choose another --out")
        previous = {(row["id"], row["query_mode"]): row for row in saved["results"]}
    rows = []
    for case in cases:
        qid = case["id"]
        root = Path(snapshots[qid]["root"])
        enumerated = time.perf_counter()
        paths = [concept.path for concept in load_lazy_bundle(root).iter_concepts()]
        enumerate_ms = (time.perf_counter() - enumerated) * 1000
        gold = {span["path"] for span in case["gold"]}
        if not gold <= set(paths):
            raise ValueError(f"Gold files not searchable for {qid}")
        for mode in config["query_modes"]:
            key = qid, mode
            if key in previous:
                row = previous[key]
            else:
                query = case["query"] if mode == "full" else case["query"][:500]
                hits = {}
                search_ms = 0.0
                for begin in range(0, len(paths), BATCH):
                    part, elapsed = rg_hits(query, root, paths[begin:begin + BATCH])
                    hits.update(part)
                    search_ms += elapsed
                ranked = rank_rg(hits, len(paths), path_boost=2)
                row = {"id": qid, "repo": case["repo"], "query_mode": mode,
                       "file_count": len(paths), "enumerate_ms": enumerate_ms,
                       "search_ms": search_ms,
                       "matched_files": len(hits), "paths": ranked[:16],
                       "file_recall@8": len(gold & set(ranked[:8])) / len(gold),
                       "file_recall@16": len(gold & set(ranked[:16])) / len(gold)}
            rows.append(row)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.out.with_suffix(args.out.suffix + ".tmp")
            temporary.write_text(json.dumps({"config": config, "results": rows},
                                            indent=2), encoding="utf-8")
            temporary.replace(args.out)
            print(f"{len(rows)}/{len(cases)*2} {case['repo']} {mode}: "
                  f"{row['search_ms']:.0f} ms recall@8={row['file_recall@8']:.3f}",
                  flush=True)
    for mode in config["query_modes"]:
        selected = [row for row in rows if row["query_mode"] == mode]
        print(f"{mode}: recall@8={statistics.mean(row['file_recall@8'] for row in selected):.3f} "
              f"search={statistics.mean(row['search_ms'] for row in selected):.0f} ms, "
              f"enumerate={statistics.mean(row['enumerate_ms'] for row in selected):.0f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
