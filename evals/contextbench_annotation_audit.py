"""Classify the archived study's annotation-text mismatches without changing scores."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from evals import RESULTS
from evals.contextbench_holdout import PARQUET


def _normalized(text: str) -> str:
    return " ".join(text.split())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", type=Path, default=PARQUET)
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--audit", type=Path,
                        default=Path("paper/data/contextbench-paper-gold-audit.json"))
    parser.add_argument("--out", type=Path,
                        default=RESULTS / "contextbench-annotation-audit.json")
    args = parser.parse_args()
    import pyarrow.parquet as pq

    cases = {row["id"]: row for row in json.loads(args.holdout.read_text())['cases']}
    roots = {row["id"]: Path(row["root"]) for row in json.loads(
        args.snapshots.read_text()
    )['cases']}
    audit = json.loads(args.audit.read_text())
    original = {row["instance_id"]: json.loads(row["gold_context"])
                for row in pq.read_table(args.parquet, columns=[
                    "instance_id", "gold_context"
                ]).to_pylist() if row["instance_id"] in cases}
    by_id: dict[str, list[dict]] = defaultdict(list)
    for row in audit["results"]:
        by_id[row["id"]].append(row)
    if set(by_id) != set(cases):
        raise ValueError("Archived audit does not match the selected cohort")
    details = []
    by_repo: dict[str, dict[str, int]] = defaultdict(lambda: {"cases": 0, "mismatched": 0})
    for qid, case in cases.items():
        rows = by_id[qid]
        if len(rows) != len(original[qid]):
            raise ValueError(f"Annotation count differs for {qid}")
        by_repo[case["repo"]]["cases"] += 1
        by_repo[case["repo"]]["mismatched"] += any(not row["aligned"] for row in rows)
        for row, span in zip(rows, original[qid], strict=True):
            if row["aligned"]:
                continue
            if (row["path"], row["start_line"], row["end_line"]) != (
                span["file"], span["start_line"], span["end_line"]
            ):
                raise ValueError(f"Archived annotation differs for {qid}")
            content = _normalized(span.get("content") or "")
            source = _normalized((roots[qid] / row["path"]).read_text(encoding="utf-8"))
            kind = ("empty_annotation" if not content else
                    "text_elsewhere_in_file" if content in source else
                    "text_not_found_in_file")
            details.append({"id": qid, "repo": case["repo"], "path": row["path"],
                            "start_line": row["start_line"],
                            "end_line": row["end_line"], "kind": kind})
    output = {
        "meaning": "Mechanical text-location categories; not diagnoses of dataset causes",
        "cases": len(cases), "spans": audit["summary"]["spans"],
        "aligned_spans": audit["summary"]["aligned"],
        "mismatched_cases": sum(v["mismatched"] for v in by_repo.values()),
        "mismatch_kinds": dict(Counter(row["kind"] for row in details)),
        "by_repo": dict(sorted(by_repo.items())), "mismatches": details,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print({key: output[key] for key in ("cases", "spans", "aligned_spans",
                                          "mismatched_cases", "mismatch_kinds")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
