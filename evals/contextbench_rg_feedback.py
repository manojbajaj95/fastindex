"""Post-study two-search ripgrep control with deterministic relevance feedback.

The second query keeps the five rarest issue terms found by the first search.
This is a scripted adaptive lexical control, not a coding agent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from collections import Counter
from pathlib import Path

from evals import RESULTS
from evals.baselines.ripgrep import rank_rg, rg_hits
from evals.hybrid import _rrf
from fastindex.bundle import load_lazy_bundle

BATCH = 256


def _search(query: str, root: Path, paths: list[str]) -> tuple[dict[str, set[str]], float]:
    hits: dict[str, set[str]] = {}
    elapsed = 0.0
    for begin in range(0, len(paths), BATCH):
        part, ms = rg_hits(query, root, paths[begin:begin + BATCH])
        hits.update(part)
        elapsed += ms
    return hits, elapsed


def _feedback_terms(hits: dict[str, set[str]]) -> list[str]:
    frequency = Counter(term for terms in hits.values() for term in terms)
    return sorted(frequency, key=lambda term: (frequency[term], term))[:5]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--out", type=Path,
                        default=RESULTS / "contextbench-paper-rg-feedback.json")
    args = parser.parse_args()
    cases = json.loads(args.holdout.read_text())["cases"]
    roots = {row["id"]: Path(row["root"]) for row in json.loads(
        args.snapshots.read_text()
    )["cases"]}
    config = {"holdout_sha256": hashlib.sha256(args.holdout.read_bytes()).hexdigest(),
              "method": "full-issue rg, then rg on five rarest matched issue terms; RRF@16",
              "searches": 2, "batch_size": BATCH, "rank_constant": 60}
    previous = {}
    if args.resume and args.out.exists():
        saved = json.loads(args.out.read_text())
        if saved["config"] != config:
            parser.error("saved run settings differ")
        previous = {row["id"]: row for row in saved["results"] if "error" not in row}
    rows = []
    for case in cases:
        qid = case["id"]
        if qid in previous:
            row = previous[qid]
        else:
            started = time.perf_counter()
            try:
                root = roots[qid]
                paths = [concept.path for concept in load_lazy_bundle(root).iter_concepts()]
                first_hits, first_ms = _search(case["query"], root, paths)
                terms = _feedback_terms(first_hits)
                second_hits, second_ms = _search(" ".join(terms), root, paths)
                first = rank_rg(first_hits, len(paths), path_boost=2)
                second = rank_rg(second_hits, len(paths), path_boost=2)
                ranked, _ = _rrf({"first": first, "feedback": second})
                gold = {span["path"] for span in case["gold"]}
                row = {"id": qid, "repo": case["repo"], "paths": ranked[:16],
                       "feedback_terms": terms, "file_count": len(paths),
                       "search_ms": first_ms + second_ms,
                       "latency_ms": (time.perf_counter() - started) * 1000,
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
        print(f"{len(rows)}/{len(cases)} {case['repo']} feedback: "
              f"{row.get('file_recall@8', row.get('error'))}", flush=True)
    complete = [row for row in rows if "error" not in row]
    if complete:
        print(f"Recall@8={statistics.mean(row['file_recall@8'] for row in complete):.3f}")
    return int(len(complete) != len(rows))


if __name__ == "__main__":
    raise SystemExit(main())
