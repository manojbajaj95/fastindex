"""Cold SQLite FTS5 file-ranking control on frozen ContextBench snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import time
from pathlib import Path

from evals import RESULTS
from evals.baselines.bm25 import tokenize
from evals.baselines.ripgrep import STOP_WORDS
from evals.contextbench_snapshots import HOLDOUT
from fastindex.bundle import load_lazy_bundle

SNAPSHOTS = RESULTS / "contextbench-snapshots.json"


def _rank(connection: sqlite3.Connection, query: str, k: int = 16, *,
          path_only: bool = False) -> list[str]:
    terms = sorted(term for term in set(tokenize(query)) - STOP_WORDS if len(term) > 2)
    if not terms:
        return []
    expression = " OR ".join(f'"{term}"' for term in terms)
    weights = "bm25(files)" if path_only else "bm25(files, 2.0, 1.0)"
    return [row[0] for row in connection.execute(
        "SELECT path FROM files WHERE files MATCH ? "
        f"ORDER BY {weights}, path LIMIT ?", (expression, k),
    )]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", type=Path, default=HOLDOUT)
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOTS)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--path-only", action="store_true",
                        help="Post-study path-name-only lexical control")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.out is None:
        args.out = RESULTS / ("contextbench-path-fts5.json" if args.path_only else
                              "contextbench-fts5-paired.json")
    cases = json.loads(args.holdout.read_text(encoding="utf-8"))["cases"]
    if args.limit:
        cases = cases[:args.limit]
    snapshots = {row["id"]: row for row in json.loads(
        args.snapshots.read_text(encoding="utf-8"))["cases"]}
    config = {"holdout_sha256": hashlib.sha256(args.holdout.read_bytes()).hexdigest(),
              "query_modes": ("full", "first500"), "output_k": 16,
              "index": ("in-memory SQLite FTS5 unicode61, path only" if args.path_only else
                        "in-memory SQLite FTS5 unicode61, path/content weights 2/1"),
              "query": "OR of unique non-stopword tokens of length >2"}
    previous = {}
    if args.resume and args.out.exists():
        saved = json.loads(args.out.read_text(encoding="utf-8"))
        if saved["config"] != config:
            parser.error("saved run settings differ; choose another --out")
        previous = {(row["id"], row["query_mode"]): row for row in saved["results"]}
    rows = []
    for case in cases:
        qid = case["id"]
        gold = {span["path"] for span in case["gold"]}
        needed = [mode for mode in config["query_modes"] if (qid, mode) not in previous]
        if needed:
            started = time.perf_counter()
            connection = sqlite3.connect(":memory:")
            if args.path_only:
                connection.execute("CREATE VIRTUAL TABLE files USING fts5(path)")
            else:
                connection.execute("CREATE VIRTUAL TABLE files USING fts5(path, content)")
            concepts = load_lazy_bundle(Path(snapshots[qid]["root"])).iter_concepts()
            if args.path_only:
                connection.executemany("INSERT INTO files (path) VALUES (?)",
                                       ((concept.path,) for concept in concepts))
            else:
                connection.executemany("INSERT INTO files (path, content) VALUES (?, ?)",
                                       ((concept.path, concept.raw) for concept in concepts))
            file_count = connection.execute("SELECT count(*) FROM files").fetchone()[0]
            build_ms = (time.perf_counter() - started) * 1000
            if missing := [path for path in gold if connection.execute(
                "SELECT 1 FROM files WHERE path = ? LIMIT 1", (path,),
            ).fetchone() is None]:
                raise ValueError(f"Gold files not indexed for {qid}: {sorted(missing)}")
        for mode in config["query_modes"]:
            if (qid, mode) in previous:
                row = previous[qid, mode]
            else:
                query = case["query"] if mode == "full" else case["query"][:500]
                started = time.perf_counter()
                paths = _rank(connection, query, path_only=args.path_only)
                query_ms = (time.perf_counter() - started) * 1000
                row = {"id": qid, "repo": case["repo"], "query_mode": mode,
                       "file_count": file_count, "build_ms": build_ms,
                       "query_ms": query_ms, "paths": paths,
                       "file_recall@8": len(gold & set(paths[:8])) / len(gold),
                       "file_recall@16": len(gold & set(paths[:16])) / len(gold)}
            rows.append(row)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.out.with_suffix(args.out.suffix + ".tmp")
            temporary.write_text(json.dumps({"config": config, "results": rows},
                                            indent=2), encoding="utf-8")
            temporary.replace(args.out)
            print(f"{len(rows)}/{len(cases)*2} {case['repo']} {mode}: "
                  f"{row['file_recall@8']:.3f} recall@8", flush=True)
        if needed:
            connection.close()
    for mode in config["query_modes"]:
        selected = [row for row in rows if row["query_mode"] == mode]
        print(f"{mode}: recall@8={statistics.mean(row['file_recall@8'] for row in selected):.3f} "
              f"build={statistics.mean(row['build_ms'] for row in selected):.0f} ms "
              f"query={statistics.mean(row['query_ms'] for row in selected):.0f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
