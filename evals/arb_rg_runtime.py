"""Time actual one-call ripgrep over ARB's materialized file chunks."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from evals import RESULTS
from evals.arb_trace_ablation import DEFAULT_RELEASE, _files
from evals.arb_tree_ablation import DEFAULT_TREES
from evals.baselines.bm25 import tokenize
from evals.baselines.ripgrep import STOP_WORDS, rank_rg, rg_hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, default=DEFAULT_RELEASE)
    parser.add_argument("--trees", type=Path, default=DEFAULT_TREES.parent / "path-only-trees-v2")
    parser.add_argument("--out", type=Path, default=RESULTS / "arb-rg-runtime.json")
    args = parser.parse_args()
    samples = [json.loads(line) for line in (
        args.release / "benchmark/v2_trace2code/samples.jsonl"
    ).read_text(encoding="utf-8").splitlines()]
    rows = []
    for sample in samples:
        files = _files(args.release, sample["repo"], sample["base_commit"])
        root = args.trees / sample["repo"].replace("/", "__") / sample["base_commit"]
        if not root.is_dir():
            raise FileNotFoundError(root)
        query = sample["query"]["failure_excerpt"]
        actual, search_ms = rg_hits(query, root, list(files))
        terms = set(tokenize(query)) - STOP_WORDS
        terms = {term for term in terms if len(term) > 2}
        expected = {
            path: matched
            for path, content in files.items()
            if (matched := {term for term in terms if term in content.lower()})
        }
        gold = set(sample["gold"]["root_cause_files"])
        ranking = rank_rg(actual, len(files), path_boost=0)
        rows.append({
            "id": sample["id"], "repo": sample["repo"],
            "search_ms": search_ms, "matched_files": len(actual),
            "hit_parity": actual == expected,
            "file_recall@8": len(gold & set(ranking[:8])) / len(gold),
            "file_recall@16": len(gold & set(ranking[:16])) / len(gold),
        })
        print(f"{len(rows)}/{len(samples)} {sample['repo']} "
              f"{search_ms:.1f} ms parity={actual == expected}", flush=True)
    times = sorted(row["search_ms"] for row in rows)
    summary = {
        "n": len(rows), "mean_ms": statistics.mean(times),
        "p50_ms": statistics.median(times), "p95_ms": times[int(.95 * len(times)) - 1],
        "parity": sum(row["hit_parity"] for row in rows),
        "file_recall@8": statistics.mean(row["file_recall@8"] for row in rows),
        "file_recall@16": statistics.mean(row["file_recall@16"] for row in rows),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"summary": summary, "results": rows}, indent=2))
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
