"""Freeze ContextBench issue queries and gold lines before retrieval runs.

Download the pinned Parquet named in the manifest, then run with
``uv run --with pyarrow python -m evals.contextbench_holdout``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from evals import RESULTS

MANIFEST = Path(__file__).parent / "fixtures/queries/contextbench_holdout.json"
PARQUET = Path(__file__).parent / "fixtures/external/contextbench/full.parquet"


def _gold(row: dict) -> list[dict]:
    spans = json.loads(row["gold_context"] or "[]")
    if not isinstance(spans, list):
        return []
    for span in spans:
        if not isinstance(span, dict) or not isinstance(span.get("file"), str):
            return []
        path = PurePosixPath(span["file"])
        if not path.parts or path.is_absolute() or "\\" in span["file"] or any(
            part in {".", ".."} for part in path.parts
        ):
            return []
        if not isinstance(span.get("start_line"), int) or not isinstance(
            span.get("end_line"), int
        ) or span["start_line"] < 1 or span["end_line"] < span["start_line"]:
            return []
    return spans


def _eligible(row: dict) -> bool:
    return bool(
        row["problem_statement"]
        and re.fullmatch(r"[0-9a-f]{40}", row["base_commit"] or "")
        and _gold(row)
    )


def _selected_ids(rows: list[dict], repo: str, count: int) -> list[str]:
    eligible = (row for row in rows if row["repo"] == repo and _eligible(row))
    return [row["instance_id"] for row in sorted(
        eligible,
        key=lambda row: hashlib.sha256(row["instance_id"].encode()).hexdigest(),
    )[:count]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", type=Path, default=PARQUET)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--out", type=Path, default=RESULTS / "contextbench-holdout.json")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    with args.parquet.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual != manifest["parquet_sha256"]:
        raise ValueError(f"Parquet SHA-256 mismatch: {actual}")
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("Run with `uv run --with pyarrow`") from exc
    columns = ["instance_id", "repo", "base_commit", "source", "problem_statement",
               "gold_context"]
    rows = pq.read_table(args.parquet, columns=columns).to_pylist()
    by_id = {row["instance_id"]: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("Dataset contains duplicate instance IDs")
    selected = [qid for ids in manifest["case_ids"].values() for qid in ids]
    if "base_commits" in manifest and set(manifest["base_commits"]) != set(selected):
        raise ValueError("Frozen base commits do not match selected cases")
    cases = []
    for repo, ids in manifest["case_ids"].items():
        if _selected_ids(rows, repo, len(ids)) != ids:
            raise ValueError(f"Frozen selection differs for {repo}")
        for qid in ids:
            row = by_id[qid]
            if ("base_commits" in manifest
                    and manifest["base_commits"][qid] != row["base_commit"]):
                raise ValueError(f"Frozen base commit differs for {qid}")
            cases.append({
                "id": qid, "repo": repo, "base_commit": row["base_commit"],
                "source": row["source"], "query": row["problem_statement"],
                "gold": [{"path": span["file"],
                          "start_line": span["start_line"],
                          "end_line": span["end_line"]} for span in _gold(row)],
            })
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_suffix(args.out.suffix + ".tmp")
    temporary.write_text(json.dumps({
        "dataset": manifest["dataset"], "revision": manifest["revision"],
        "parquet_sha256": actual,
        "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "cases": cases,
    }, indent=2), encoding="utf-8")
    temporary.replace(args.out)
    print(f"Frozen {len(cases)} cases across {len(manifest['case_ids'])} repositories: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
