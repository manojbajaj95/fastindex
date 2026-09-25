"""Audit ContextBench annotated text against its pinned base-commit lines."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from evals import RESULTS
from evals.contextbench_holdout import MANIFEST, PARQUET
from evals.contextbench_snapshots import HOLDOUT

SNAPSHOTS = RESULTS / "contextbench-snapshots.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", type=Path, default=PARQUET)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--holdout", type=Path, default=HOLDOUT)
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOTS)
    parser.add_argument("--out", type=Path,
                        default=RESULTS / "contextbench-gold-audit.json")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    with args.parquet.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != manifest["parquet_sha256"]:
            parser.error("Parquet hash differs from frozen manifest")
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("Run with `uv run --with pyarrow`") from exc
    content_by_id = {
        row["instance_id"]: json.loads(row["gold_context"])
        for row in pq.read_table(args.parquet, columns=["instance_id", "gold_context"])
        .to_pylist()
    }
    cases = json.loads(args.holdout.read_text(encoding="utf-8"))["cases"]
    snapshots = {row["id"]: row for row in json.loads(
        args.snapshots.read_text(encoding="utf-8"))["cases"]}
    rows = []
    for case in cases:
        qid = case["id"]
        gold = content_by_id[qid]
        if len(gold) != len(case["gold"]):
            raise ValueError(f"Gold span count differs for {qid}")
        root = Path(snapshots[qid]["root"])
        file_lines = {}
        for expected, span in zip(case["gold"], gold, strict=True):
            if (expected["path"], expected["start_line"], expected["end_line"]) != (
                span["file"], span["start_line"], span["end_line"]
            ):
                raise ValueError(f"Gold span order differs for {qid}")
            path = expected["path"]
            if path not in file_lines:
                file_lines[path] = (root / path).read_text(encoding="utf-8").splitlines(
                    keepends=True,
                )
            actual = "".join(file_lines[path][span["start_line"] - 1:span["end_line"]])
            content = span.get("content") or ""
            normalized_actual, normalized_content = actual.strip(), content.strip()
            whitespace_exact = bool(content and actual.split() == content.split())
            containment = bool(normalized_actual and normalized_content and (
                normalized_actual in normalized_content
                or normalized_content in normalized_actual
            ))
            rows.append({
                "id": qid, "repo": case["repo"], "path": path,
                "start_line": span["start_line"], "end_line": span["end_line"],
                "exact": actual == content,
                "strip_exact": normalized_actual == normalized_content,
                "whitespace_exact": whitespace_exact,
                "containment": containment,
                "aligned": whitespace_exact or containment,
            })
    summary = {"spans": len(rows), "exact": sum(row["exact"] for row in rows),
               "strip_exact": sum(row["strip_exact"] for row in rows),
               "whitespace_exact": sum(row["whitespace_exact"] for row in rows),
               "containment": sum(row["containment"] for row in rows),
               "aligned": sum(row["aligned"] for row in rows),
               "mismatched_cases": len({row["id"] for row in rows
                                        if not row["aligned"]})}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"summary": summary, "results": rows}, indent=2),
                        encoding="utf-8")
    print(f"{summary} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
