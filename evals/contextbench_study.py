"""Fixed equal-budget ContextBench file-discovery study.

All input files must belong to the same frozen holdout. This script never
chooses the best arm from the reduced held-out cases.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path, PurePosixPath

import tiktoken

from evals import RESULTS
from evals.contextbench_context import _clipped_gold, _score
from evals.hybrid import _rrf
from fastindex.bundle import load_lazy_bundle

K_VALUES = (8, 16)
PRIMARY_BUDGET = 16_000
PRIMARY_PACKING = "merge4-200"
TREE_VARIANT = "wide"
LEXICAL_MODE = "first500"
BOOTSTRAP_REPETITIONS = 10_000
BOOTSTRAP_SEED = 0


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _by_id(payload: dict, key: str | None = None) -> dict:
    rows = payload["results"]
    lookup = {(row["id"], row[key]) if key else row["id"]: row for row in rows}
    if len(lookup) != len(rows):
        raise ValueError("Duplicate retrieval rows")
    return lookup


def _file_metrics(gold: set[str], paths: list[str], k: int) -> dict:
    selected = set(paths[:k])
    return {
        "file_recall": len(gold & selected) / len(gold),
        "complete_files": int(gold <= selected),
        "file_precision": len(gold & selected) / len(selected) if selected else 0.0,
        "gold_found": len(gold & selected),
    }


def _case_features(case: dict) -> dict:
    gold_paths = {span["path"] for span in case["gold"]}
    query = case["query"].casefold()
    return {
        "issue_mentions_gold_path": int(any(path.casefold() in query
                                             for path in gold_paths)),
        "issue_mentions_gold_basename": int(any(
            len(name) >= 5 and name.casefold() in query
            for name in (PurePosixPath(path).name for path in gold_paths)
        )),
        "gold_spans_multiple_directories": int(len({
            PurePosixPath(path).parent for path in gold_paths
        }) > 1),
        "query_chars": len(case["query"]),
    }


def _cluster_interval(
    rows: list[dict], left: str, right: str, metric: str, *,
    repetitions: int = BOOTSTRAP_REPETITIONS, seed: int = BOOTSTRAP_SEED,
) -> dict:
    paired: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        if row["arm"] in {left, right}:
            if row["arm"] in paired[row["id"]]:
                raise ValueError(f"Duplicate {row['arm']} row for {row['id']}")
            paired[row["id"]][row["arm"]] = row
    if not paired or any(set(pair) != {left, right} for pair in paired.values()):
        raise ValueError("Missing paired arm")
    by_repo: dict[str, list[float]] = defaultdict(list)
    for pair in paired.values():
        a, b = pair[left], pair[right]
        if a["repo"] != b["repo"]:
            raise ValueError("Paired rows have different repositories")
        by_repo[a["repo"]].append(a[metric] - b[metric])
    repos = sorted(by_repo)
    deltas = [delta for repo in repos for delta in by_repo[repo]]
    rng = random.Random(seed)
    samples = sorted(
        statistics.mean(delta for repo in rng.choices(repos, k=len(repos))
                        for delta in by_repo[repo])
        for _ in range(repetitions)
    )
    return {
        "n_cases": len(deltas), "n_repositories": len(repos),
        "mean_delta": statistics.mean(deltas),
        "lower_95": samples[int(.025 * repetitions)],
        "upper_95": samples[int(.975 * repetitions) - 1],
        "method": "paired repository-cluster bootstrap, equal case weight",
        "repetitions": repetitions, "seed": seed,
    }


def _summary(rows: list[dict]) -> dict:
    output = {}
    for k in K_VALUES:
        at_k = [row for row in rows if row["k"] == k]
        for arm in sorted({row["arm"] for row in at_k}):
            arm_rows = [row for row in at_k if row["arm"] == arm]
            for repo in ("all", "aligned", "unaligned",
                         *sorted({row["repo"] for row in arm_rows})):
                selected = [row for row in arm_rows
                            if repo == "all" or row["repo"] == repo
                            or (repo == "aligned" and row["gold_text_aligned"])
                            or (repo == "unaligned" and not row["gold_text_aligned"])]
                if not selected:
                    continue
                output[f"{arm}@{k}/{repo}"] = {
                    "n": len(selected),
                    "file_recall": statistics.mean(row["file_recall"] for row in selected),
                    "complete_files": sum(row["complete_files"] for row in selected),
                    "complete_feasible": sum(row["gold_files"] <= k for row in selected),
                    "file_precision": statistics.mean(
                        row["file_precision"] for row in selected),
                    "line_recall_16k": statistics.mean(
                        row["line_recall_16k"] for row in selected),
                    "complete_lines_16k": sum(row["complete_lines_16k"] for row in selected),
                    "context_tokens_16k": statistics.mean(
                        row["context_tokens_16k"] for row in selected),
                }
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--fts5", type=Path, required=True)
    parser.add_argument("--rg", type=Path, required=True)
    parser.add_argument("--gold-audit", type=Path, required=True)
    parser.add_argument("--out", type=Path,
                        default=RESULTS / "contextbench-confirmatory-study.json")
    args = parser.parse_args()
    holdout = json.loads(args.holdout.read_text(encoding="utf-8"))
    cases = holdout["cases"]
    ids = {case["id"] for case in cases}
    if len(ids) != len(cases):
        parser.error("Duplicate holdout case IDs")
    snapshots = _by_id({"results": json.loads(args.snapshots.read_text(
        encoding="utf-8"))["cases"]})
    gold_audit = json.loads(args.gold_audit.read_text(encoding="utf-8"))
    alignment: dict[str, list[bool]] = defaultdict(list)
    for row in gold_audit["results"]:
        alignment[row["id"]].append(row["aligned"])
    if set(alignment) != ids or any(
        len(alignment[case["id"]]) != len(case["gold"]) for case in cases
    ):
        parser.error("Gold-text audit does not match the scoring cases")
    tree = json.loads(args.tree.read_text(encoding="utf-8"))
    fts = json.loads(args.fts5.read_text(encoding="utf-8"))
    rg = json.loads(args.rg.read_text(encoding="utf-8"))
    holdout_hash = _sha(args.holdout)
    for name, payload in (("tree", tree), ("fts5", fts), ("rg", rg)):
        if payload["config"]["holdout_sha256"] != holdout_hash:
            parser.error(f"{name} belongs to a different holdout")
    if tree["config"]["top_k"] != 16 or tree["config"]["variants"] != {
        TREE_VARIANT: [0.01, 0.01]
    }:
        parser.error("Tree result does not match the locked wide@16 route")
    tree_rows = _by_id(tree, "variant")
    fts_rows = _by_id(fts, "query_mode")
    rg_rows = _by_id(rg, "query_mode")
    if not ids <= set(snapshots) or set(tree_rows) != {
        (qid, TREE_VARIANT) for qid in ids
    } or any(set(mapping) != {(qid, mode) for qid in ids for mode in
             ("full", "first500")} for mapping in (fts_rows, rg_rows)):
        parser.error("One or more retrieval inputs have incomplete or extra cases")
    encoder = tiktoken.get_encoding("cl100k_base")
    scored = []
    unique = []
    for case in cases:
        qid = case["id"]
        snapshot = snapshots[qid]
        if snapshot["errors"] or snapshot["base_commit"] != case["base_commit"]:
            raise ValueError(f"Invalid snapshot for {qid}")
        files = {concept.path: concept.raw for concept in load_lazy_bundle(
            Path(snapshot["root"])).iter_concepts()}
        gold, clipped = _clipped_gold(case["gold"], files)
        gold_paths = {span.path for span in gold}
        features = _case_features(case)
        features["gold_text_aligned"] = int(all(alignment[qid]))
        route = tree_rows[qid, TREE_VARIANT]
        if "error" in route:
            raise ValueError(f"Tree failed on {qid}: {route['error']}")
        tree_paths = route["paths"]
        fts_paths = fts_rows[qid, LEXICAL_MODE]["paths"]
        rg_paths = rg_rows[qid, "full"]["paths"]
        fused, _ = _rrf({"tree": tree_paths, "fts5": fts_paths})
        rankings = {
            "tree": tree_paths, "fts5": fts_paths,
            "rg": rg_paths, "tree+fts5": fused,
        }
        for k in K_VALUES:
            tree_found = set(tree_paths[:k]) & gold_paths
            lexical_found = set(fts_paths[:k]) & gold_paths
            rg_found = set(rg_paths[:k]) & gold_paths
            unique.append({
                "id": qid, "repo": case["repo"], "k": k,
                "tree_only_gold": len(tree_found - lexical_found),
                "fts5_only_gold": len(lexical_found - tree_found),
                "both_gold": len(tree_found & lexical_found),
                "gold_files": len(gold_paths),
                "tree_adds_gold": int(bool(tree_found - lexical_found)),
                "tree_only_vs_rg_gold": len(tree_found - rg_found),
                "rg_only_gold": len(rg_found - tree_found),
                "tree_adds_vs_rg": int(bool(tree_found - rg_found)),
            })
            for arm, paths in rankings.items():
                if len(paths) != len(set(paths)):
                    raise ValueError(f"Duplicate paths in {arm} for {qid}")
                metrics = _file_metrics(gold_paths, paths, k)
                context = next(row for row in _score(
                    case, files, paths[:k], source=arm, encoder=encoder,
                    clipped_gold=gold, packing=PRIMARY_PACKING,
                ) if row["budget_tokens"] == PRIMARY_BUDGET)
                scored.append({
                    "id": qid, "repo": case["repo"], "arm": arm, "k": k,
                    "gold_files": len(gold_paths), "gold_spans": len(gold),
                    "clipped_gold_ends": clipped, **features, **metrics,
                    "line_recall_16k": context["line_recall"],
                    "complete_lines_16k": context["complete_lines"],
                    "context_tokens_16k": context["context_tokens"],
                    "context_file_recall_16k": context["context_file_recall"],
                })
        print(f"{len(unique)//2}/{len(cases)} {case['repo']} {qid}", flush=True)
    paired = {}
    for k in K_VALUES:
        at_k = [row for row in scored if row["k"] == k]
        for left, right in (("tree", "fts5"), ("tree", "rg"),
                            ("tree+fts5", "fts5"),
                            ("tree+fts5", "tree")):
            for metric in ("file_recall", "line_recall_16k"):
                paired[f"{left}-{right}@{k}/{metric}"] = _cluster_interval(
                    at_k, left, right, metric)
                if metric == "line_recall_16k":
                    paired[f"{left}-{right}@{k}/{metric}/aligned"] = (
                        _cluster_interval(
                            [row for row in at_k if row["gold_text_aligned"]],
                            left, right, metric,
                        )
                    )
    output = {
        "config": {
            "input_sha256": {name: _sha(path) for name, path in vars(args).items()
                             if name != "out"},
            "primary_lexical": f"FTS5 {LEXICAL_MODE}",
            "tree_variant": TREE_VARIANT, "candidate_k": K_VALUES,
            "fusion": "RRF k=60 over tree@16 and FTS5@16, then truncate",
            "context": f"{PRIMARY_PACKING} at {PRIMARY_BUDGET} cl100k_base tokens",
            "bootstrap": {"repetitions": BOOTSTRAP_REPETITIONS,
                          "seed": BOOTSTRAP_SEED},
        },
        "cohort": {
            "selected_n": holdout.get("selected_n", len(cases)),
            "audited_n": len(cases),
            "gold_text_aligned_n": sum(all(alignment[qid]) for qid in ids),
            "excluded": holdout.get("excluded", []),
        },
        "summary": _summary(scored),
        "unique": {
            str(k): {
                "n": len([row for row in unique if row["k"] == k]),
                "tree_only_gold": sum(row["tree_only_gold"] for row in unique
                                      if row["k"] == k),
                "fts5_only_gold": sum(row["fts5_only_gold"] for row in unique
                                      if row["k"] == k),
                "tree_adds_gold_cases": sum(row["tree_adds_gold"] for row in unique
                                            if row["k"] == k),
                "tree_only_vs_rg_gold": sum(
                    row["tree_only_vs_rg_gold"] for row in unique if row["k"] == k),
                "rg_only_gold": sum(row["rg_only_gold"] for row in unique
                                    if row["k"] == k),
                "tree_adds_vs_rg_cases": sum(
                    row["tree_adds_vs_rg"] for row in unique if row["k"] == k),
            } for k in K_VALUES
        },
        "paired": paired, "results": scored, "unique_results": unique,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
