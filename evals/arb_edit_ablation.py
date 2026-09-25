"""Compare lexical file retrieval on ARB's pinned edit2ripple release."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from evals import RESULTS
from evals.arb_trace_ablation import _files, _metrics, _rankings
from evals.hybrid import _rrf

TASK = "v2_edit2ripple"
DEFAULT_RELEASE = Path(__file__).resolve().parent / "fixtures/external/arb-edit2ripple/data"


def _without_given(rankings: dict[str, list[str]], given: set[str]) -> dict[str, list[str]]:
    selected = {name: [path for path in paths if path not in given]
                for name, paths in rankings.items() if name != "fusion"}
    selected["fusion"], _ = _rrf({
        "bm25": selected["bm25"][:8],
        "literal": selected["literal+path"][:8],
    })
    return selected


def _path_neighbors(anchor: str, files: dict[str, str]) -> list[str]:
    """Rank by shared directory prefix, without inspecting query or file text."""
    anchor_dirs = anchor.split("/")[:-1]

    def rank(path: str) -> tuple[int, int, str]:
        dirs = path.split("/")[:-1]
        shared = 0
        for left, right in zip(anchor_dirs, dirs, strict=False):
            if left != right:
                break
            shared += 1
        return -shared, abs(len(dirs) - len(anchor_dirs)), path

    return sorted((path for path in files if path != anchor), key=rank)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, default=DEFAULT_RELEASE)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--out", type=Path, default=RESULTS / "arb-edit-ablation.json")
    args = parser.parse_args()
    samples = [json.loads(line) for line in
               (args.release / "benchmark" / TASK / "samples.jsonl")
               .read_text(encoding="utf-8").splitlines()]
    if args.limit is not None:
        samples = samples[:args.limit]

    rows: list[dict] = []
    for sample in samples:
        load_started = time.perf_counter()
        files = _files(args.release, sample["repo"], sample["base_commit"], task=TASK)
        corpus_load_ms = (time.perf_counter() - load_started) * 1000
        corpus_path = (args.release / "corpus" / TASK
                       / sample["repo"].replace("/", "__")
                       / f"{sample['base_commit']}.chunks.jsonl")
        gold = sorted(set(sample["gold"]["files"]))
        given = set(sample["gold"]["given_files"])
        if not gold or not set(gold).issubset(files) or not given.issubset(files):
            raise ValueError(f"Missing gold/given file in corpus for {sample['id']}")
        if given.intersection(gold):
            raise ValueError(f"Given file is also gold for {sample['id']}")
        query = sample["query"]
        prompts = {
            "intent": query["intent"],
            "anchor+intent": "\n".join((query["anchor_file"], query["intent"])),
            "full": "\n".join((query["anchor_file"], query["intent"], query["anchor_diff"])),
        }
        rankings: dict[str, list[str]] = {}
        ranking_ms: dict[str, float] = {}
        rankings["path-neighbor"] = _path_neighbors(query["anchor_file"], files)[:16]
        for mode, prompt in prompts.items():
            started = time.perf_counter()
            raw = _rankings(prompt, files)
            ranking_ms[mode] = (time.perf_counter() - started) * 1000
            rankings.update({f"{mode}/{name}": paths[:16]
                             for name, paths in _without_given(raw, given).items()})
        rows.append({
            "id": sample["id"], "repo": sample["repo"],
            "base_commit": sample["base_commit"],
            "gold_paths": gold, "given_paths": sorted(given),
            "file_count": len(files),
            "corpus_bytes": corpus_path.stat().st_size,
            "corpus_load_ms": corpus_load_ms,
            "index_bytes": 0,
            "query_chars": {mode: len(prompt) for mode, prompt in prompts.items()},
            "ranking_ms": ranking_ms, "rankings": rankings,
        })
        print(f"{len(rows)}/{len(samples)} {sample['repo']} {sample['id']}", flush=True)

    names = [f"{mode}/{name}" for mode in ("intent", "anchor+intent", "full")
             for name in ("literal", "literal+path", "bm25", "fusion")]
    names.append("path-neighbor")
    summary = {}
    for repo in ["all", *sorted({row["repo"] for row in rows})]:
        selected = [row for row in rows if repo == "all" or row["repo"] == repo]
        summary[repo] = {
            f"{name}@{k}": _metrics(selected, name, k)
            for name in names for k in (8, 16)
        }
    summary["all"]["ranking_ms"] = {
        mode: {
            "median": statistics.median(row["ranking_ms"][mode] for row in rows),
            "p95": sorted(row["ranking_ms"][mode] for row in rows)[int(.95 * (len(rows) - 1))],
        }
        for mode in ("intent", "anchor+intent", "full")
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "release": str(args.release), "query_fields": ["anchor_file", "intent", "anchor_diff"],
        "gold_field": "gold.files", "excluded_field": "gold.given_files",
        "candidate_text": "released kind=file chunks (truncated)",
        "summary": summary, "results": rows,
    }, indent=2), encoding="utf-8")
    for name in names:
        result = summary["all"][f"{name}@8"]
        print(f"{name}@8: recall={result['file_recall']:.3f}, "
              f"complete={result['complete']}/{len(rows)}")
    print(f"Wrote {args.out} (local only; evals/results/ is gitignored)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
