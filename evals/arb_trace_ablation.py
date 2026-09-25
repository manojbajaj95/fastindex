"""Evaluate simple lexical file candidates on ARB's frozen trace2code subset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rank_bm25 import BM25Okapi

from evals import RESULTS
from evals.baselines.bm25 import tokenize
from evals.baselines.ripgrep import STOP_WORDS, rank_rg
from evals.hybrid import _rrf

DEFAULT_RELEASE = Path(__file__).resolve().parent / "fixtures/external/arb-trace2code/data"


def _files(
    release: Path, repo: str, commit: str, *, task: str = "v2_trace2code"
) -> dict[str, str]:
    slug = repo.replace("/", "__")
    path = release / "corpus" / task / slug / f"{commit}.chunks.jsonl"
    files: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        chunk = json.loads(line)
        if chunk["kind"] == "file":
            files[chunk["path"]] = chunk["text"]
    return files


def _rankings(query: str, files: dict[str, str]) -> dict[str, list[str]]:
    terms = sorted(term for term in set(tokenize(query)) - STOP_WORDS if len(term) > 2)
    hits = {
        path: {term for term in terms if term in content.lower()}
        for path, content in files.items()
    }
    hits = {path: matched for path, matched in hits.items() if matched}
    rg0 = rank_rg(hits, len(files), path_boost=0)
    rg2 = rank_rg(hits, len(files), path_boost=2)

    paths = list(files)
    bm25 = BM25Okapi([tokenize(path + "\n" + files[path]) for path in paths])
    scores = bm25.get_scores(terms)
    bm25_rank = sorted(
        (i for i in range(len(paths)) if scores[i] > 0),
        key=lambda i: (-scores[i], paths[i]),
    )
    bm25_paths = [paths[i] for i in bm25_rank]
    fused, _ = _rrf({"bm25": bm25_paths[:8], "literal": rg2[:8]})
    return {"literal": rg0, "literal+path": rg2, "bm25": bm25_paths, "fusion": fused}


def _metrics(rows: list[dict], name: str, k: int) -> dict:
    recalls = []
    reciprocal_ranks = []
    for row in rows:
        gold = set(row["gold_paths"])
        ranking = row["rankings"][name][:k]
        recalls.append(len(gold & set(ranking)) / len(gold))
        reciprocal_ranks.append(next(
            (1 / (i + 1) for i, path in enumerate(ranking) if path in gold), 0
        ))
    return {
        "file_recall": sum(recalls) / len(recalls),
        "complete": sum(value == 1 for value in recalls),
        "mrr": sum(reciprocal_ranks) / len(reciprocal_ranks),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, default=DEFAULT_RELEASE)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--out", type=Path, default=RESULTS / "arb-trace-ablation.json")
    args = parser.parse_args()
    samples = [
        json.loads(line)
        for line in (args.release / "benchmark/v2_trace2code/samples.jsonl")
        .read_text(encoding="utf-8").splitlines()
    ]
    if args.limit is not None:
        samples = samples[:args.limit]

    rows: list[dict] = []
    for sample in samples:
        files = _files(args.release, sample["repo"], sample["base_commit"])
        gold = sorted(set(sample["gold"]["root_cause_files"]))
        if not set(gold).issubset(files):
            raise ValueError(f"Gold file missing from corpus for {sample['id']}")
        query = sample["query"]["failure_excerpt"]
        rankings = _rankings(query, files)
        rows.append({
            "id": sample["id"],
            "repo": sample["repo"],
            "base_commit": sample["base_commit"],
            "gold_paths": gold,
            "file_count": len(files),
            "query_chars": len(query),
            "rankings": {name: ranked[:16] for name, ranked in rankings.items()},
        })
        print(f"{len(rows)}/{len(samples)} {sample['repo']} {sample['id']}", flush=True)

    summary = {}
    for repo in ["all", *sorted({row["repo"] for row in rows})]:
        selected = [row for row in rows if repo == "all" or row["repo"] == repo]
        summary[repo] = {
            f"{name}@{k}": _metrics(selected, name, k)
            for name in ("literal", "literal+path", "bm25", "fusion")
            for k in (8, 16)
        }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "release": str(args.release),
        "query_field": "query.failure_excerpt",
        "gold_field": "gold.root_cause_files",
        "summary": summary,
        "results": rows,
    }, indent=2), encoding="utf-8")
    for name, result in summary["all"].items():
        print(
            f"{name}: recall={result['file_recall']:.3f}, "
            f"complete={result['complete']}/{len(rows)}"
        )
    print(f"Wrote {args.out} (local only; evals/results/ is gitignored)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
