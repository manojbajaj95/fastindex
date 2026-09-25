"""Score post-study path controls against the archived 82-case result."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import defaultdict
from itertools import product
from pathlib import Path, PurePosixPath

import tiktoken

from evals import RESULTS
from evals.baselines.bm25 import tokenize
from evals.baselines.ripgrep import STOP_WORDS
from evals.contextbench_context import _clipped_gold, _score
from evals.contextbench_study import _cluster_interval, _file_metrics
from fastindex.bundle import load_lazy_bundle


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _basename_rank(query: str, paths: list[str]) -> list[str]:
    """Rank path names by issue-token overlap, boosting basename matches."""
    terms = {term for term in tokenize(query[:500])
             if term not in STOP_WORDS and len(term) > 2}
    scores = {}
    for path in paths:
        basename = set(tokenize(PurePosixPath(path).stem))
        full_path = set(tokenize(path))
        score = 2 * len(terms & basename) + len(terms & full_path)
        if score:
            scores[path] = score
    return sorted(scores, key=lambda path: (-scores[path], path))[:16]


def _summary(rows: list[dict]) -> dict:
    result = {}
    for arm in sorted({row["arm"] for row in rows}):
        for k in (8, 16):
            selected = [row for row in rows if row["arm"] == arm and row["k"] == k]
            aligned = [row for row in selected if row["gold_text_aligned"]]
            result[f"{arm}@{k}"] = {
                "n": len(selected),
                "macro_file_recall": statistics.mean(row["file_recall"] for row in selected),
                "micro_file_recall": sum(row["gold_found"] for row in selected)
                / sum(row["gold_files"] for row in selected),
                "complete_files": sum(row["complete_files"] for row in selected),
                "line_recall_16k": statistics.mean(row["line_recall_16k"] for row in selected),
                "aligned_n": len(aligned),
                "aligned_line_recall_16k": statistics.mean(
                    row["line_recall_16k"] for row in aligned
                ),
                "aligned_complete_lines_16k": sum(
                    row["complete_lines_16k"] for row in aligned
                ),
            }
    return result


def _cluster_sign_flip(rows: list[dict]) -> dict:
    """Descriptive exact paired sign flip of tree--FTS5 at eight files."""
    paired: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        if row["k"] == 8 and row["arm"] in {"tree", "fts5"}:
            paired[row["id"]][row["arm"]] = row
    by_repo: dict[str, float] = defaultdict(float)
    for pair in paired.values():
        by_repo[pair["tree"]["repo"]] += (pair["tree"]["file_recall"]
                                               - pair["fts5"]["file_recall"])
    sums = list(by_repo.values())
    observed = abs(sum(sums))
    total = 2 ** len(sums)
    extreme = sum(abs(sum(value * sign for value, sign in zip(sums, signs)))
                  >= observed - 1e-12
                  for signs in product((-1, 1), repeat=len(sums)))
    return {"n_repositories": len(sums), "two_sided_p": extreme / total,
            "assumption": "exchangeable signs of repository-level paired contrasts",
            "status": "post-study sensitivity, not a population generalization test"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--path-fts5", type=Path, required=True)
    parser.add_argument("--flat-jev", type=Path, required=True)
    parser.add_argument("--rg-feedback", type=Path, required=True)
    parser.add_argument("--gold-audit", type=Path,
                        default=Path("paper/data/contextbench-paper-gold-audit.json"))
    parser.add_argument("--original", type=Path,
                        default=Path("paper/data/contextbench-paper-study.json"))
    parser.add_argument("--out", type=Path,
                        default=RESULTS / "contextbench-poststudy.json")
    args = parser.parse_args()
    cases = json.loads(args.holdout.read_text())["cases"]
    ids = {row["id"] for row in cases}
    roots = {row["id"]: Path(row["root"]) for row in json.loads(
        args.snapshots.read_text()
    )["cases"]}
    path_payload = json.loads(args.path_fts5.read_text())
    flat_payload = json.loads(args.flat_jev.read_text())
    feedback_payload = json.loads(args.rg_feedback.read_text())
    holdout_hash = _sha(args.holdout)
    for payload in (path_payload, flat_payload, feedback_payload):
        if payload["config"]["holdout_sha256"] != holdout_hash:
            parser.error("New ranking belongs to a different holdout")
    path_rows = {row["id"]: row for row in path_payload["results"]
                 if row["query_mode"] == "first500"}
    flat_rows = {row["id"]: row for row in flat_payload["results"]}
    feedback_rows = {row["id"]: row for row in feedback_payload["results"]}
    if (set(path_rows) != ids or set(flat_rows) != ids or set(feedback_rows) != ids
        or any("error" in row for row in [*flat_rows.values(), *feedback_rows.values()])
    ):
        parser.error("New ranking is incomplete")
    alignment: dict[str, list[bool]] = defaultdict(list)
    for row in json.loads(args.gold_audit.read_text())["results"]:
        alignment[row["id"]].append(row["aligned"])
    original = json.loads(args.original.read_text())
    if {row["id"] for row in original["results"]} != ids:
        parser.error("Original study belongs to a different cohort")
    encoder = tiktoken.get_encoding("cl100k_base")
    rows = []
    basename_rankings = []
    for case in cases:
        qid = case["id"]
        files = {concept.path: concept.raw for concept in load_lazy_bundle(
            roots[qid]
        ).iter_concepts()}
        gold, _ = _clipped_gold(case["gold"], files)
        gold_paths = {span.path for span in gold}
        basename_paths = _basename_rank(case["query"], list(files))
        basename_rankings.append({"id": qid, "repo": case["repo"],
                                  "paths": basename_paths})
        for arm, paths in (("path-fts5", path_rows[qid]["paths"]),
                           ("flat-jev", flat_rows[qid]["paths"]),
                           ("rg-feedback", feedback_rows[qid]["paths"]),
                           ("basename", basename_paths)):
            for k in (8, 16):
                metrics = _file_metrics(gold_paths, paths, k)
                context = next(row for row in _score(
                    case, files, paths[:k], source=arm, encoder=encoder,
                    clipped_gold=gold, packing="merge4-200"
                ) if row["budget_tokens"] == 16_000)
                rows.append({"id": qid, "repo": case["repo"], "arm": arm,
                             "k": k, "gold_files": len(gold_paths),
                             "gold_text_aligned": int(all(alignment[qid])),
                             **metrics, "line_recall_16k": context["line_recall"],
                             "complete_lines_16k": context["complete_lines"]})
        print(f"{len(rows)//8}/{len(cases)} {case['repo']} scored", flush=True)
    all_rows = original["results"] + rows
    pairs = {}
    for k in (8, 16):
        at_k = [row for row in all_rows if row["k"] == k]
        for left, right in (("flat-jev", "tree"), ("path-fts5", "fts5"),
                            ("flat-jev", "fts5"), ("rg-feedback", "rg"),
                            ("rg-feedback", "tree"), ("basename", "fts5")):
            for metric in ("file_recall", "line_recall_16k"):
                pairs[f"{left}-{right}@{k}/{metric}"] = _cluster_interval(
                    at_k, left, right, metric
                )
                if metric == "line_recall_16k":
                    pairs[f"{left}-{right}@{k}/{metric}/aligned"] = _cluster_interval(
                        [row for row in at_k if row["gold_text_aligned"]],
                        left, right, metric,
                    )
    output = {
        "status": "post-study, not prespecified with the original 82-case analysis",
        "input_sha256": {name: _sha(path) for name, path in vars(args).items()
                         if name != "out"},
        "summary": _summary(all_rows), "paired": pairs, "results": rows,
        "basename_rankings": basename_rankings,
        "cluster_sign_flip_tree_fts5_at8": _cluster_sign_flip(original["results"]),
        "flat_operations": {
            "mean_calls": statistics.mean(row["model_calls"] for row in flat_rows.values()),
            "mean_input_tokens": statistics.mean(row["input_tokens"]
                                                   for row in flat_rows.values()),
            "mean_latency_ms": statistics.mean(row["latency_ms"]
                                                for row in flat_rows.values()),
            "mean_estimated_cost_usd": statistics.mean(row["cost_usd"]
                                                       for row in flat_rows.values()),
        },
        "rg_feedback_operations": {
            "mean_search_ms": statistics.mean(row["search_ms"]
                                               for row in feedback_rows.values()),
            "mean_latency_ms": statistics.mean(row["latency_ms"]
                                                for row in feedback_rows.values()),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print({key: output["summary"][key]["macro_file_recall"]
           for key in ("flat-jev@8", "path-fts5@8", "tree@8", "fts5@8")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
