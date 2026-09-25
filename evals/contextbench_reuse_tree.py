"""Reuse already completed tree rows for a metadata-only reduced cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from evals import RESULTS


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reduce(
    source_tree: dict, source_holdout: dict, target_holdout: dict,
    source_tree_hash: str, target_holdout_hash: str,
) -> dict:
    source_ids = {case["id"] for case in source_holdout["cases"]}
    target_ids = [case["id"] for case in target_holdout["cases"]]
    if len(target_ids) != len(set(target_ids)) or not set(target_ids) <= source_ids:
        raise ValueError("Target is not a unique subset of the original cases")
    by_id = {}
    for row in source_tree["results"]:
        if row["variant"] != "wide":
            raise ValueError("Expected only the wide tree variant")
        if row["id"] in by_id:
            raise ValueError(f"Duplicate tree result: {row['id']}")
        by_id[row["id"]] = row
    if missing := set(target_ids) - by_id.keys():
        raise ValueError(f"Missing {len(missing)} required tree results")
    if errors := [qid for qid in target_ids if "error" in by_id[qid]]:
        raise ValueError(f"Failed tree results cannot be reused: {errors}")
    config = dict(source_tree["config"])
    config["holdout_sha256"] = target_holdout_hash
    config["derived_from_tree_sha256"] = source_tree_hash
    config["reduction"] = "first eight IDs per repository in frozen manifest; no backfill"
    return {"config": config, "results": [by_id[qid] for qid in target_ids]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-tree", type=Path, required=True)
    parser.add_argument("--source-holdout", type=Path, required=True)
    parser.add_argument("--target-holdout", type=Path, required=True)
    parser.add_argument("--out", type=Path,
                        default=RESULTS / "contextbench-paper-tree.json")
    args = parser.parse_args()
    source_tree = json.loads(args.source_tree.read_text(encoding="utf-8"))
    if source_tree["config"]["holdout_sha256"] != _sha(args.source_holdout):
        parser.error("Source tree and holdout hashes differ")
    payload = _reduce(
        source_tree,
        json.loads(args.source_holdout.read_text(encoding="utf-8")),
        json.loads(args.target_holdout.read_text(encoding="utf-8")),
        _sha(args.source_tree), _sha(args.target_holdout),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Reused {len(payload['results'])} existing tree results -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
