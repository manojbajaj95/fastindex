"""Create the auditable, source-accessible cohort before retrieval.

Keep every selected issue in the audit ledger. Exclude only cases whose pinned
gold files are absent/unreadable or outside the common searchable corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from evals import RESULTS
from fastindex.bundle import load_lazy_bundle


def _audit_case(case: dict, snapshot: dict) -> list[str]:
    if (snapshot["repo"], snapshot["base_commit"]) != (
        case["repo"], case["base_commit"]
    ):
        raise ValueError(f"Snapshot identity differs for {case['id']}")
    reasons = list(snapshot["errors"])
    if reasons:
        return reasons
    bundle = load_lazy_bundle(Path(snapshot["root"]))
    for missing in sorted(
        path for path in {span["path"] for span in case["gold"]}
        if bundle.get_concept(path) is None
    ):
        reasons.append(f"Gold file outside common searchable corpus: {missing}")
    return reasons


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--out", type=Path,
                        default=RESULTS / "contextbench-confirmatory-audited.json")
    args = parser.parse_args()
    original = json.loads(args.holdout.read_text(encoding="utf-8"))
    snapshots = json.loads(args.snapshots.read_text(encoding="utf-8"))["cases"]
    by_id = {row["id"]: row for row in snapshots}
    if len(by_id) != len(snapshots) or not {
        case["id"] for case in original["cases"]
    } <= set(by_id):
        parser.error("Snapshot ledger must contain the selected cases")
    retained, excluded = [], []
    for case in original["cases"]:
        reasons = _audit_case(case, by_id[case["id"]])
        if reasons:
            excluded.append({"id": case["id"], "repo": case["repo"],
                             "reasons": reasons})
        else:
            retained.append(case)
    audited = {
        "dataset": original["dataset"], "revision": original["revision"],
        "parquet_sha256": original["parquet_sha256"],
        "manifest_sha256": original["manifest_sha256"],
        "selected_holdout_sha256": hashlib.sha256(
            args.holdout.read_bytes()).hexdigest(),
        "audit_rule": "Exclude only source/gold-file errors or gold outside the "
                      "common searchable corpus; no replacement cases",
        "selected_n": len(original["cases"]), "excluded": excluded,
        "cases": retained,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(audited, indent=2), encoding="utf-8")
    print(f"Retained {len(retained)}/{len(original['cases'])} cases; "
          f"excluded {len(excluded)} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
