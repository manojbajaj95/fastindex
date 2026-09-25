"""Post-study flat, name-only Jev control on the audited ContextBench cohort.

Every eligible path is shown once in a SHA-256 ordered Choice menu. The top 16
from each menu advance to another flat menu until one top-16 list remains.
This is a batched path tournament, not a directory walk or a calibrated global
probability ranking. Its API cost is measured rather than matched to the tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path
from urllib.error import URLError

from evals import RESULTS
from fastindex.bundle import load_lazy_bundle
from fastindex.models import ModelUsage
from fastindex.strategies.tree_decision import make_decision_model

MODEL = "typesafe/jev-1.13.0"
MENU_LIMIT = 200
TOP_K = 16
INSTRUCTIONS = (
    "Choose the source file path most likely to contain direct evidence for the issue. "
    "Judge paths only; no file contents or directory summaries are available. "
    "Choose none only if no listed path could contain relevant evidence."
)


def _groups(paths: list[str], max_chars: int) -> list[list[str]]:
    groups: list[list[str]] = []
    group: list[str] = []
    size = 600  # state, instructions, and JSON envelope
    for path in paths:
        length = len(path) + 20
        if group and (len(group) >= MENU_LIMIT or size + length > max_chars):
            groups.append(group)
            group, size = [], 600
        group.append(path)
        size += length
    if group:
        groups.append(group)
    return groups


def _rank(model, query: str, paths: list[str]) -> tuple[list[str], ModelUsage, int, int]:
    usage = ModelUsage()
    calls = 0
    retries = 0
    contenders = paths
    while True:
        survivors: list[str] = []
        for group in _groups(contenders, model.max_request_chars):
            for attempt in range(4):
                try:
                    scores, used = model.choose(
                        f"User issue: {query[:500]}",
                        [f"Source file: {path}" for path in group],
                    )
                    break
                except (TimeoutError, URLError):
                    retries += 1
                    if attempt == 3:
                        raise
                    time.sleep(2 ** attempt)
            usage.add(used)
            calls += 1
            survivors.extend(path for _, path in sorted(
                zip(scores[:-1], group, strict=True), key=lambda pair: (-pair[0], pair[1])
            )[:TOP_K])
        if len(survivors) <= TOP_K:
            return survivors, usage, calls, retries
        if len(survivors) >= len(contenders):
            raise RuntimeError("Flat tournament failed to reduce its candidate set")
        contenders = survivors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--fts5", type=Path,
                        default=Path("paper/data/contextbench-paper-fts5.json"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--out", type=Path, default=RESULTS / "contextbench-paper-flat-paths.json")
    args = parser.parse_args()
    cases = json.loads(args.holdout.read_text(encoding="utf-8"))["cases"]
    snapshots = {row["id"]: row for row in json.loads(args.snapshots.read_text(
        encoding="utf-8"
    ))["cases"]}
    fts = {(row["id"], row["query_mode"]): row for row in json.loads(
        args.fts5.read_text(encoding="utf-8"
    ))["results"]}
    config = {
        "holdout_sha256": hashlib.sha256(args.holdout.read_bytes()).hexdigest(),
        "model": MODEL, "instructions": INSTRUCTIONS,
        "menu_limit": MENU_LIMIT, "top_k": TOP_K,
        "ordering": "SHA-256 of issue ID plus NUL plus path",
        "method": "Choice top-16 tournament over all eligible path names",
        "timeout_retries": "up to 3 on TimeoutError/URLError; delays 1, 2, 4 seconds",
    }
    previous = {}
    if args.resume and args.out.exists():
        saved = json.loads(args.out.read_text(encoding="utf-8"))
        if saved["config"] != config:
            parser.error("saved run settings differ")
        previous = {row["id"]: row for row in saved["results"] if "error" not in row}
    model = make_decision_model(MODEL)
    model.instructions = INSTRUCTIONS
    rows = []
    for case in cases[:args.limit or None]:
        qid = case["id"]
        if qid in previous:
            row = previous[qid]
            row.setdefault("retries", 0)
        else:
            start = time.perf_counter()
            try:
                paths = [concept.path for concept in load_lazy_bundle(
                    Path(snapshots[qid]["root"])
                ).iter_concepts()]
                if len(paths) != fts[qid, "first500"]["file_count"]:
                    raise ValueError("Flat and FTS5 arms have different eligible-file counts")
                paths.sort(key=lambda path: hashlib.sha256(
                    f"{qid}\0{path}".encode()
                ).digest())
                ranked, usage, calls, retries = _rank(model, case["query"], paths)
                gold = {span["path"] for span in case["gold"]}
                row = {"id": qid, "repo": case["repo"], "paths": ranked,
                       "file_count": len(paths), "model_calls": calls,
                       "retries": retries,
                       "input_tokens": usage.input_tokens,
                       "output_tokens": usage.output_tokens,
                       "cost_usd": usage.input_tokens * model.input_cost_per_million / 1e6,
                       "latency_ms": (time.perf_counter() - start) * 1000,
                       "file_recall@8": len(gold & set(ranked[:8])) / len(gold),
                       "file_recall@16": len(gold & set(ranked[:16])) / len(gold)}
            except Exception as exc:
                row = {"id": qid, "repo": case["repo"], "error": str(exc)}
        rows.append(row)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.out.with_suffix(args.out.suffix + ".tmp")
        temporary.write_text(json.dumps({"config": config, "results": rows}, indent=2),
                             encoding="utf-8")
        temporary.replace(args.out)
        print(f"{len(rows)}/{min(args.limit or len(cases), len(cases))} {case['repo']} "
              f"flat: {row.get('file_recall@8', row.get('error'))}", flush=True)
    complete = [row for row in rows if "error" not in row]
    if complete:
        print(f"Recall@8={statistics.mean(row['file_recall@8'] for row in complete):.3f}; "
              f"calls/issue={statistics.mean(row['model_calls'] for row in complete):.1f}")
    return int(len(complete) != len(rows))


if __name__ == "__main__":
    raise SystemExit(main())
