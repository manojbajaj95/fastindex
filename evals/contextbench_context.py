"""Score frozen ContextBench file rankings under identical context budgets."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
from pathlib import Path

import tiktoken

from evals import RESULTS
from evals.arb_oracle_span_ablation import _merged_match_spans, _pack
from evals.contextbench_snapshots import HOLDOUT
from evals.hybrid import _rrf
from evals.metrics import GoldSpan, span_set_metrics
from fastindex.bundle import load_lazy_bundle
from fastindex.types import Span

SNAPSHOTS = RESULTS / "contextbench-snapshots.json"
TREE = RESULTS / "contextbench-tree-names-paired.json"
DESCENDANTS = RESULTS / "contextbench-tree-descendants-paired.json"
LEXICAL = RESULTS / "contextbench-lexical-paired.json"
FTS5 = RESULTS / "contextbench-fts5-paired.json"
BUDGETS = (8_000, 16_000, 32_000)


def _clipped_gold(gold: list[dict], files: dict[str, str]) -> tuple[list[GoldSpan], int]:
    clipped = []
    count = 0
    for item in gold:
        path = item["path"]
        if path not in files:
            raise ValueError(f"Gold file is not searchable: {path}")
        line_count = len(files[path].splitlines())
        if item["start_line"] > line_count:
            raise ValueError(f"Gold start exceeds file: {path}:{item['start_line']}")
        end = min(item["end_line"], line_count)
        count += end < item["end_line"]
        clipped.append(GoldSpan(path, item["start_line"], end))
    return clipped, count


def _score(
    case: dict, files: dict[str, str], paths: list[str], *, source: str,
    encoder: tiktoken.Encoding, clipped_gold: list[GoldSpan],
    packing: str = "whole",
) -> list[dict]:
    if missing := set(paths) - files.keys():
        raise ValueError(f"Selected files not readable for {case['id']}: {sorted(missing)}")
    unique_paths = list(dict.fromkeys(paths))
    gold_paths = {span.path for span in clipped_gold}
    whole = [Span(path, 1, len(files[path].splitlines()), files[path])
             for path in unique_paths]
    if packing == "merge4-200":
        selected = _merged_match_spans(case["query"], whole, radius=200,
                                       anchors_per_file=4)
    elif packing == "whole":
        selected = whole
    else:
        raise ValueError(f"Unknown packing: {packing}")
    rows = []
    for budget in BUDGETS:
        included, tokens, chars = _pack(selected, budget, encoder)
        sr, sp, sf, lr, lp, lf = span_set_metrics(included, clipped_gold)
        included_paths = {span.path for span in included}
        rows.append({
            "id": case["id"], "repo": case["repo"], "source": source,
            "packing": packing,
            "budget_tokens": budget, "candidate_k": len(unique_paths),
            "candidate_file_recall": len(gold_paths & set(unique_paths)) / len(gold_paths),
            "candidate_complete_files": int(gold_paths <= set(unique_paths)),
            "context_file_recall": len(gold_paths & included_paths) / len(gold_paths),
            "context_complete_files": int(gold_paths <= included_paths),
            "span_recall": sr, "span_precision": sp, "span_f1": sf,
            "line_recall": lr, "line_precision": lp, "line_f1": lf,
            "complete_lines": int(lr == 1), "context_tokens": tokens,
            "context_chars": chars, "context_files": len(included),
            "gold_files": len(gold_paths), "gold_spans": len(clipped_gold),
        })
    return rows


def _summarize(rows: list[dict]) -> dict:
    result = {}
    for source in sorted({row["source"] for row in rows}):
        for budget in BUDGETS:
            for repo in ("all", "django/django", "sveltejs/svelte"):
                selected = [row for row in rows if row["source"] == source
                            and row["budget_tokens"] == budget
                            and (repo == "all" or row["repo"] == repo)]
                if not selected:
                    continue
                result[f"{source}/{budget}/{repo}"] = {
                    "n": len(selected),
                    "candidate_file_recall": statistics.mean(
                        row["candidate_file_recall"] for row in selected),
                    "candidate_complete_files": sum(
                        row["candidate_complete_files"] for row in selected),
                    "context_file_recall": statistics.mean(
                        row["context_file_recall"] for row in selected),
                    "context_complete_files": sum(
                        row["context_complete_files"] for row in selected),
                    "span_recall": statistics.mean(row["span_recall"] for row in selected),
                    "line_recall": statistics.mean(row["line_recall"] for row in selected),
                    "complete_lines": sum(row["complete_lines"] for row in selected),
                    "context_tokens": statistics.mean(row["context_tokens"] for row in selected),
                }
    return result


def _paired_bootstrap(
    rows: list[dict], left: str, right: str, metric: str, *,
    budget: int = 16_000, repetitions: int = 10_000,
) -> dict:
    selected = [row for row in rows if row["budget_tokens"] == budget
                and row["source"] in {left, right}]
    by_id: dict[str, dict[str, dict]] = {}
    for row in selected:
        by_id.setdefault(row["id"], {})[row["source"]] = row
    if any(set(pair) != {left, right} for pair in by_id.values()):
        raise ValueError("Incomplete paired results")
    deltas = [pair[left][metric] - pair[right][metric] for pair in by_id.values()]
    rng = random.Random(0)
    samples = sorted(statistics.mean(rng.choices(deltas, k=len(deltas)))
                     for _ in range(repetitions))
    return {"n": len(deltas), "mean_delta": statistics.mean(deltas),
            "lower_95": samples[int(.025 * repetitions)],
            "upper_95": samples[int(.975 * repetitions) - 1],
            "repetitions": repetitions, "seed": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", type=Path, default=HOLDOUT)
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOTS)
    parser.add_argument("--tree", type=Path, default=TREE)
    parser.add_argument("--tree-experiment", choices=("thresholds", "descendants"),
                        default="thresholds")
    parser.add_argument("--lexical", type=Path, default=LEXICAL)
    parser.add_argument("--fts5", type=Path, default=FTS5)
    parser.add_argument("--packing", choices=("whole", "merge4-200"), default="whole")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.tree_experiment == "descendants" and args.tree == TREE:
        args.tree = DESCENDANTS
    variants = (("wide", "strict") if args.tree_experiment == "thresholds"
                else ("names0", "names12"))
    primary = variants[0] if args.tree_experiment == "thresholds" else variants[1]
    if args.out is None:
        suffix = "paired" if args.packing == "whole" else args.packing
        experiment = "" if args.tree_experiment == "thresholds" else "descendants-"
        args.out = RESULTS / f"contextbench-context-{experiment}{suffix}.json"
    cases = json.loads(args.holdout.read_text(encoding="utf-8"))["cases"]
    snapshots = {row["id"]: row for row in json.loads(
        args.snapshots.read_text(encoding="utf-8"))["cases"]}
    tree = json.loads(args.tree.read_text(encoding="utf-8"))
    lexical = json.loads(args.lexical.read_text(encoding="utf-8"))
    fts5 = json.loads(args.fts5.read_text(encoding="utf-8"))
    holdout_hash = hashlib.sha256(args.holdout.read_bytes()).hexdigest()
    if tree["config"]["holdout_sha256"] != holdout_hash or (
        lexical["config"]["holdout_sha256"] != holdout_hash
    ) or fts5["config"]["holdout_sha256"] != holdout_hash:
        parser.error("retrieval results are not from this frozen holdout")
    if tree["config"]["top_k"] != 8:
        parser.error("this comparison expects tree top_k=8")
    tree_rows = {(row["id"], row["variant"]): row for row in tree["results"]}
    lexical_rows = {(row["id"], row["query_mode"]): row
                    for row in lexical["results"]}
    fts5_rows = {(row["id"], row["query_mode"]): row
                 for row in fts5["results"]}
    encoder = tiktoken.get_encoding("cl100k_base")
    rows = []
    clipped_total = 0
    for case in cases:
        qid = case["id"]
        if snapshots[qid]["errors"]:
            raise ValueError(f"Invalid snapshot: {qid}")
        bundle = load_lazy_bundle(Path(snapshots[qid]["root"]))
        # The lexical control indexes this same UTF-8 searchable-file set.
        files = {concept.path: concept.raw for concept in bundle.iter_concepts()}
        gold, clipped = _clipped_gold(case["gold"], files)
        clipped_total += clipped
        tree_paths = {}
        for variant in variants:
            route = tree_rows[qid, variant]
            if "error" in route:
                raise ValueError(f"Tree run failed on {qid}: {route['error']}")
            tree_paths[variant] = route["paths"]
            rows.extend(_score(case, files, route["paths"], source=f"tree-{variant}@8",
                               encoder=encoder, clipped_gold=gold,
                               packing=args.packing))
        for mode in ("full", "first500"):
            ranked = lexical_rows[qid, mode]
            if "error" in ranked:
                raise ValueError(f"Lexical run failed on {qid}: {ranked['error']}")
            for name in ("literal", "literal+path", "bm25", "fusion"):
                for k in (8, 16):
                    paths = ranked["rankings"][name][:k]
                    rows.extend(_score(
                        case, files, paths, source=f"{name}-{mode}@{k}",
                        encoder=encoder, clipped_gold=gold, packing=args.packing,
                    ))
            fts_ranked = fts5_rows[qid, mode]
            for k in (8, 16):
                rows.extend(_score(
                    case, files, fts_ranked["paths"][:k],
                    source=f"fts5-{mode}@{k}", encoder=encoder,
                    clipped_gold=gold, packing=args.packing,
                ))
        bm25_paths = lexical_rows[qid, "full"]["rankings"]["bm25"][:8]
        fused, _ = _rrf({"tree": tree_paths[primary], "bm25": bm25_paths})
        for k in (8, 16):
            rows.extend(_score(
                case, files, fused[:k], source=f"tree+bm25-full@{k}",
                encoder=encoder, clipped_gold=gold, packing=args.packing,
            ))
        print(f"{len(rows)//(24*len(BUDGETS))}/{len(cases)} {case['repo']} {qid}",
              flush=True)
    summary = _summarize(rows)
    paired_bootstrap = {
        metric: _paired_bootstrap(rows, f"tree-{primary}@8", "bm25-full@8", metric)
        for metric in ("candidate_file_recall", "line_recall")
    }
    ablation_bootstrap = (
        {metric: _paired_bootstrap(rows, "tree-names12@8", "tree-names0@8", metric)
         for metric in ("candidate_file_recall", "line_recall")}
        if args.tree_experiment == "descendants" else None
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "config": {"holdout_sha256": holdout_hash, "budgets": BUDGETS,
                   "packing": args.packing,
                   "tree_experiment": args.tree_experiment,
                   "tokenizer": "cl100k_base", "clipped_gold_ends": clipped_total,
                   "union": f"RRF of tree-{primary}@8 and full-issue BM25@8",
                   "fts5": fts5["config"]["index"]},
        "summary": summary, "paired_bootstrap": paired_bootstrap,
        "ablation_bootstrap": ablation_bootstrap, "results": rows,
    }, indent=2), encoding="utf-8")
    for source in (f"tree-{variant}@8" for variant in variants):
        score = summary[f"{source}/16000/all"]
        print(f"{source}: candidate={score['candidate_file_recall']:.3f}, "
              f"context-lines={score['line_recall']:.3f}, "
              f"complete={score['complete_lines']}/{score['n']}")
    for source in ("literal-full@8",
                   "literal+path-full@8", "bm25-full@8", "fusion-full@8"):
        score = summary[f"{source}/16000/all"]
        print(f"{source}: candidate={score['candidate_file_recall']:.3f}, "
              f"context-lines={score['line_recall']:.3f}, "
              f"complete={score['complete_lines']}/{score['n']}")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
