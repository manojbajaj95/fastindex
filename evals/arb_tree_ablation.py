"""Run path-only tree routing on ARB's frozen task snapshots."""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path, PurePosixPath

from evals import RESULTS
from evals.arb_trace_ablation import DEFAULT_RELEASE, _files
from fastindex.bundle import RESERVED, load_lazy_bundle
from fastindex.strategies import StrategyConfig
from fastindex.strategies.tree_decision import TreeDecisionStrategy

DEFAULT_TREES = DEFAULT_RELEASE.parent / "path-only-trees"


def materialize_path_tree(
    release: Path, repo: str, commit: str, target_root: Path, *, task: str = "v2_trace2code"
) -> tuple[Path, int, int]:
    """Rebuild a source tree from ARB whole-file chunks and generate name-only indexes."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError(f"Unsafe repository name: {repo}")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError(f"Unsafe base commit: {commit}")
    root = target_root / repo.replace("/", "__") / commit
    files = _files(release, repo, commit, task=task)
    directories = {root}
    written = 0
    for raw_path, content in files.items():
        rel = PurePosixPath(raw_path)
        if (
            not rel.parts or rel.is_absolute() or "\\" in raw_path
            or any(part in {".", ".."} for part in rel.parts)
        ):
            raise ValueError(f"Unsafe corpus path: {raw_path}")
        if rel.name in RESERVED:
            continue
        dest = root.joinpath(*rel.parts)
        if not dest.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Corpus path escapes snapshot: {raw_path}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            if dest.read_text(encoding="utf-8") != content:
                raise ValueError(f"Materialized file differs: {dest}")
        else:
            dest.write_text(content, encoding="utf-8")
        written += 1
        directories.update((dest.parent, *dest.parent.parents))

    indexes = 0
    for directory in sorted(d for d in directories if d == root or root in d.parents):
        children = sorted(directory.iterdir())
        options = []
        for child in children:
            if child.is_dir():
                options.append(f"- [{child.name}/]({child.name}/)")
            elif child.is_file() and child.name not in RESERVED:
                # Lint's wiki links resolve Markdown concepts, not arbitrary code files.
                # Keep non-Markdown paths visible to the router without fake links.
                options.append(
                    f"- [{child.name}]({child.name})"
                    if child.suffix == ".md" else f"- {child.name}"
                )
        rel_dir = directory.relative_to(root).as_posix()
        text = f"# {rel_dir if rel_dir != '.' else '/'}\n\n" + "\n".join(options) + "\n"
        index = directory / "index.md"
        if index.exists():
            if index.read_text(encoding="utf-8") != text:
                raise ValueError(f"Materialized index differs: {index}")
        else:
            index.write_text(text, encoding="utf-8")
        indexes += 1
    return root, written, indexes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, default=DEFAULT_RELEASE)
    parser.add_argument("--trees", type=Path, default=DEFAULT_TREES)
    parser.add_argument("--task", choices=("v2_trace2code", "v2_edit2ripple"),
                        default="v2_trace2code")
    parser.add_argument("--min-probability", type=float, default=0.04)
    parser.add_argument("--relative-probability", type=float, default=0.05)
    parser.add_argument("--call-budget", type=int, default=32)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--out", type=Path, default=RESULTS / "arb-tree-path-only.json")
    args = parser.parse_args()
    if not 0 <= args.min_probability <= 1 or not 0 <= args.relative_probability <= 1:
        parser.error("routing probabilities must be between 0 and 1")
    if args.call_budget <= 0:
        parser.error("call budget must be positive")
    samples = [
        json.loads(line)
        for line in (args.release / "benchmark" / args.task / "samples.jsonl")
        .read_text(encoding="utf-8").splitlines()
    ]
    if args.limit is not None:
        samples = samples[:args.limit]

    previous: dict[str, dict] = {}
    if args.resume and args.out.exists():
        saved = json.loads(args.out.read_text(encoding="utf-8"))
        expected = {
            "task": args.task,
            "release": str(args.release),
            "min_probability": args.min_probability,
            "relative_probability": args.relative_probability,
            "call_budget": args.call_budget,
        }
        if any(saved.get(key) != value for key, value in expected.items()):
            parser.error("saved run settings do not match; choose another --out")
        previous = {row["id"]: row for row in saved["results"] if "error" not in row}

    rows: list[dict] = []
    for sample in samples:
        qid = sample["id"]
        if qid in previous:
            rows.append(previous[qid])
            print(f"REUSE {qid}", flush=True)
            continue
        try:
            setup_started = time.perf_counter()
            root, file_count, index_count = materialize_path_tree(
                args.release, sample["repo"], sample["base_commit"], args.trees,
                task=args.task,
            )
            setup_ms = (time.perf_counter() - setup_started) * 1000
            if args.task == "v2_edit2ripple":
                fields = sample["query"]
                # The router currently sees only the first 500 characters.
                query = "\n".join((fields["anchor_file"], fields["intent"]))
                given = set(sample["gold"]["given_files"])
                gold = set(sample["gold"]["files"])
            else:
                query = sample["query"]["failure_excerpt"]
                given = set()
                gold = set(sample["gold"]["root_cause_files"])
            started = time.perf_counter()
            result = TreeDecisionStrategy().retrieve(
                query,
                load_lazy_bundle(root),
                StrategyConfig(
                    top_k=8 + len(given),
                    wall_time_budget_s=180,
                    model_call_budget=args.call_budget,
                    extra={
                        "decision_return_files": True,
                        "decision_min_probability": args.min_probability,
                        "decision_relative_probability": args.relative_probability,
                    },
                ),
            )
            latency_ms = (time.perf_counter() - started) * 1000
            paths = [span.path for span in result.spans if span.path not in given][:8]
            row = {
                "id": qid,
                "repo": sample["repo"],
                "base_commit": sample["base_commit"],
                "gold_paths": sorted(gold),
                "paths": paths,
                "file_recall": len(gold & set(paths)) / len(gold),
                "setup_ms": setup_ms,
                "source_files": file_count,
                "path_indexes": index_count,
                "latency_ms": latency_ms,
                "model_calls": result.stats.model_calls,
                "model_id": result.stats.model_id,
                "input_tokens": result.stats.input_tokens,
                "cost_usd": result.stats.estimated_cost_usd,
            }
            print(f"{qid} {sample['repo']} recall={row['file_recall']:.2f}", flush=True)
        except Exception as exc:
            row = {"id": qid, "repo": sample["repo"], "error": str(exc)}
            print(f"FAIL {qid}: {exc}", flush=True)
        rows.append(row)
        # Keep earlier successful rows if a resumed run is interrupted again.
        retained = [previous[item["id"]] for item in samples[len(rows):]
                    if item["id"] in previous]
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({
            "release": str(args.release),
            "tree_mode": "path-only indexes",
            "task": args.task,
            "query_field": "query.failure_excerpt" if args.task == "v2_trace2code"
                           else "query.anchor_file + intent (first 500 chars)",
            "gold_field": "gold.root_cause_files" if args.task == "v2_trace2code"
                          else "gold.files",
            "min_probability": args.min_probability,
            "relative_probability": args.relative_probability,
            "call_budget": args.call_budget,
            "results": rows + retained,
        }, indent=2), encoding="utf-8")
    print(f"Wrote {args.out} (local only; evals/results/ is gitignored)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
