"""Replay a saved hybrid candidate trace with literal ripgrep file candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals import RESULTS
from evals.baselines.ripgrep import rank_rg, rg_hits
from evals.bench import load_fixtures
from evals.hybrid import CODEBASE_FIXTURES, DEFAULT_BUNDLE, _rrf
from fastindex.bundle import load_lazy_bundle


def _summary(rows: list[dict], name: str) -> dict:
    recalls = []
    for row in rows:
        gold = set(row["gold_paths"])
        recalls.append(len(gold & set(row["pools"][name])) / len(gold))
    return {
        "file_recall": sum(recalls) / len(recalls),
        "complete": sum(recall == 1 for recall in recalls),
        "mean_candidates": sum(len(row["pools"][name]) for row in rows) / len(rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--fixtures", type=Path, default=CODEBASE_FIXTURES)
    parser.add_argument(
        "--hybrid-results",
        type=Path,
        default=RESULTS / "hybrid-ablation-jev-bm25-fts-noul.json",
    )
    parser.add_argument("--out", type=Path, default=RESULTS / "rg-candidate-ablation.json")
    args = parser.parse_args()

    fixtures = {fixture["id"]: fixture for fixture in load_fixtures(args.fixtures)}
    saved = json.loads(args.hybrid_results.read_text(encoding="utf-8"))
    files = [concept.path for concept in load_lazy_bundle(args.bundle).iter_concepts()]
    rows: list[dict] = []
    for saved_row in saved["results"]:
        qid = saved_row["id"]
        source_rankings = saved_row["extra"]["source_rankings"]
        base, _ = _rrf(source_rankings)
        if base[:16] != saved_row["extra"]["candidate_pool"]:
            raise ValueError(f"Saved candidate ranking differs for {qid}")
        hits, search_ms = rg_hits(saved_row["query"], args.bundle.resolve(), files)
        rankings = {boost: rank_rg(hits, len(files), boost) for boost in (0, 2)}
        pools: dict[str, list[str]] = {"base": base[:16]}
        for boost, ranked in rankings.items():
            for k in (4, 8, 16):
                name = f"base+rg{k}-path{boost}"
                pools[name] = _rrf({**source_rankings, "rg": ranked[:k]})[0][:16]
            pools[f"rg8-path{boost}"] = ranked[:8]
        pools["tree+bm25+rg8"] = _rrf({
            "jev": source_rankings["jev"],
            "bm25": source_rankings["bm25"],
            "rg": rankings[2][:8],
        })[0][:16]
        pools["bm25+fts+rg8"] = _rrf({
            "bm25": source_rankings["bm25"],
            "fts": source_rankings["fts"],
            "rg": rankings[2][:8],
        })[0][:16]
        rows.append({
            "id": qid,
            "gold_paths": sorted({gold["path"] for gold in fixtures[qid]["gold"]}),
            "source_rankings": source_rankings,
            "rg_search_ms": search_ms,
            "rg_hits": {path: sorted(terms) for path, terms in hits.items()},
            "pools": pools,
        })

    summary = {name: _summary(rows, name) for name in rows[0]["pools"]}
    summary["mean_rg_search_ms"] = sum(row["rg_search_ms"] for row in rows) / len(rows)
    payload = {
        "bundle": str(args.bundle),
        "fixtures": str(args.fixtures),
        "hybrid_results": str(args.hybrid_results),
        "searchable_files": len(files),
        "pool_k": 16,
        "summary": summary,
        "results": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    for name, result in summary.items():
        if name != "mean_rg_search_ms":
            print(f"{name}: {result['file_recall']:.3f} recall, {result['complete']}/48 complete")
    print(f"mean rg search: {summary['mean_rg_search_ms']:.1f} ms/query")
    print(f"Wrote {args.out} (local only; evals/results/ is gitignored)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
