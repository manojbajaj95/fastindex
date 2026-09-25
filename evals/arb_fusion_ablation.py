"""Compare fixed-size tree, literal, and RRF file rankings on ARB traces."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from evals import RESULTS
from evals.hybrid import _rrf


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lexical", type=Path, default=RESULTS / "arb-trace-ablation.json")
    parser.add_argument("--lexical-name", default="literal")
    parser.add_argument("--tree", type=Path, default=RESULTS / "arb-tree-path-only-v2.json")
    parser.add_argument("--out", type=Path, default=RESULTS / "arb-fusion-ablation.json")
    args = parser.parse_args()
    lexical = {row["id"]: row for row in json.loads(args.lexical.read_text())["results"]}
    tree = {row["id"]: row for row in json.loads(args.tree.read_text())["results"]}
    if set(lexical) != set(tree):
        raise ValueError("Lexical and tree results have different query IDs")
    rows = []
    for qid, lex in lexical.items():
        routed = tree[qid]
        if "error" in routed:
            raise ValueError(f"Tree result failed for {qid}: {routed['error']}")
        if lex["gold_paths"] != routed["gold_paths"]:
            raise ValueError(f"Gold files differ for {qid}")
        literal = lex["rankings"][args.lexical_name][:8]
        paths = routed["paths"]
        fused, _ = _rrf({"tree": paths, "literal": literal})
        gold = set(lex["gold_paths"])
        rankings = {
            "literal": lex["rankings"][args.lexical_name],
            "tree": paths,
            "rrf_tree_literal": fused,
        }
        rows.append({
            "id": qid, "repo": lex["repo"],
            "recall": {
                f"{name}@{k}": len(gold & set(ranking[:k])) / len(gold)
                for name, ranking in rankings.items() for k in (8, 16)
            },
            "rrf_paths": fused[:16],
        })
    summary = {
        repo: {
            key: {
                "recall": statistics.mean(row["recall"][key] for row in selected),
                "complete": sum(row["recall"][key] == 1 for row in selected),
            }
            for key in rows[0]["recall"]
        }
        for repo in ["all", *sorted({row["repo"] for row in rows})]
        if (selected := [row for row in rows if repo == "all" or row["repo"] == repo])
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "lexical": str(args.lexical), "lexical_name": args.lexical_name,
        "tree": str(args.tree),
        "summary": summary, "results": rows,
    }, indent=2))
    for key, result in summary["all"].items():
        print(f"{key}: recall={result['recall']:.3f} complete={result['complete']}/{len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
