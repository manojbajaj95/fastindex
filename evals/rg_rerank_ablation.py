"""Rerank fixed saved candidate pools with and without ripgrep."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from evals import RESULTS
from evals.arb_oracle_span_ablation import _merged_match_spans
from evals.baselines.ripgrep import rank_rg
from evals.bench import load_fixtures
from evals.hybrid import (
    CODEBASE_FIXTURES,
    DEFAULT_BUNDLE,
    _file_summary,
    _rrf,
    _unique_lexical_files,
    build_context,
    select_window_spans,
)
from evals.metrics import GoldSpan, build_metrics
from fastindex.bundle import load_lazy_bundle
from fastindex.models import ModelUsage
from fastindex.strategies.tree_decision import TypeSafeModel
from fastindex.types import RunStats, Span


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--fixtures", type=Path, default=CODEBASE_FIXTURES)
    parser.add_argument(
        "--candidate-results", type=Path,
        default=RESULTS / "rg-candidate-ablation.json",
    )
    parser.add_argument("--descriptor-chars", type=int, default=1600)
    parser.add_argument("--summary-chars", type=int, default=1000)
    parser.add_argument("--variants", default="base,base+rg")
    parser.add_argument("--ids", help="Comma-separated fixture IDs")
    parser.add_argument("--out", type=Path, default=RESULTS / "rg-rerank-ablation.json")
    args = parser.parse_args()
    if min(args.descriptor_chars, args.summary_chars) < 1:
        parser.error("descriptor and summary caps must be positive")
    chosen_variants = tuple(part.strip() for part in args.variants.split(",") if part.strip())
    if not chosen_variants or set(chosen_variants) - {
        "base", "base+rg", "rg-preview", "window-preview", "rrf"
    }:
        parser.error("variants must contain base, base+rg, rg-preview, window-preview, or rrf")

    fixtures = {fixture["id"]: fixture for fixture in load_fixtures(args.fixtures)}
    candidate_data = json.loads(args.candidate_results.read_text(encoding="utf-8"))
    source_rows = candidate_data["results"]
    if args.ids:
        ids = {part.strip() for part in args.ids.split(",") if part.strip()}
        unknown = ids - fixtures.keys()
        if not ids or unknown:
            parser.error(f"unknown or empty fixture IDs: {', '.join(sorted(unknown))}")
        source_rows = [row for row in source_rows if row["id"] in ids]
    bundle = load_lazy_bundle(args.bundle)
    model = TypeSafeModel()
    results: list[dict] = []
    for source_row in source_rows:
        qid = source_row["id"]
        fixture = fixtures[qid]
        query = fixture["query"]
        previews: dict[str, str] = {}
        for name in ("bm25", "fts"):
            _, excerpts, _ = _unique_lexical_files(name, query, bundle, 8)
            previews.update(excerpts)

        hits = {path: set(terms) for path, terms in source_row["rg_hits"].items()}
        rg_rank = rank_rg(hits, candidate_data["searchable_files"])[:8]
        saved_base = source_row["pools"]["base"]
        saved_rg = source_row["pools"]["base+rg8-path2"]
        source_rankings = source_row["source_rankings"]
        variants = {
            "base": source_rankings,
            "base+rg": {**source_rankings, "rg": rg_rank},
            "rg-preview": {**source_rankings, "rg": rg_rank},
            "window-preview": {**source_rankings, "rg": rg_rank},
            "rrf": {**source_rankings, "rg": rg_rank},
        }
        for name in chosen_variants:
            rankings = variants[name]
            try:
                pool, rrf_scores = _rrf(rankings)
                pool = pool[:16]
                if pool != (saved_base if name == "base" else saved_rg):
                    raise ValueError(f"Candidate pool changed for {qid} {name}")
                candidate_previews = previews.copy()
                if name == "rg-preview":
                    for path in rg_rank:
                        concept = bundle.get_concept(path)
                        if concept is None:
                            continue
                        window = select_window_spans(
                            query,
                            [Span(path, 1, len(concept.lines), concept.raw)],
                            whole_file_lines=1,
                            window_lines=80,
                            windows_per_file=1,
                        )[0]
                        candidate_previews[path] = (
                            f"Lines {window.start_line}-{window.end_line}:\n{window.text}"
                        )
                if name == "window-preview":
                    for path in pool:
                        concept = bundle.get_concept(path)
                        if concept is None:
                            continue
                        window = _merged_match_spans(
                            query, [Span(path, 1, len(concept.lines), concept.raw)],
                            radius=20, anchors_per_file=1,
                        )[0]
                        candidate_previews[path] = (
                            f"Lines {window.start_line}-{window.end_line}:\n{window.text}"
                        )
                descriptors = [
                    (
                        f"Path: {path}\nIndex summary: "
                        f"{_file_summary(bundle, path)[:args.summary_chars]}\n"
                        f"Lexical evidence:\n{candidate_previews.get(path, '(none)')}"
                    )[: args.descriptor_chars]
                    for path in pool
                ]
                started = time.perf_counter()
                values, usage = (
                    ([rrf_scores[path] for path in pool], ModelUsage())
                    if name == "rrf" else model.score_relevance(query, descriptors)
                )
                rerank_ms = (time.perf_counter() - started) * 1000
                scores = dict(zip(pool, values, strict=True))
                ordered = sorted(pool, key=lambda path: (-scores[path], -rrf_scores[path], path))
                files = [
                    Span(path, 1, len(concept.lines), concept.raw, scores[path])
                    for path in ordered[:8]
                    if (concept := bundle.get_concept(path)) is not None
                ]
                context, included, dropped = build_context(files, 200_000)
                gold = [
                    GoldSpan(item["path"], item["start_line"], item["end_line"])
                    for item in fixture["gold"]
                ]
                stats = RunStats(
                    model_calls=usage.calls,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    estimated_cost_usd=(
                        usage.input_tokens * model.input_cost_per_million / 1_000_000
                    ),
                )
                metrics = build_metrics(stats, included, gold, latency_ms=rerank_ms)
                result = {
                    "id": qid,
                    "variant": name,
                    "pool": pool,
                    "rerank_scores": scores,
                    "ranked_paths": ordered[:8],
                    "included_paths": [span.path for span in included],
                    "dropped_paths": dropped,
                    "context_chars": len(context),
                    **metrics.to_bench_fields(),
                }
                print(
                    f"{qid} {name} coverage={metrics.span_recall:.2f} "
                    f"cost=${metrics.cost_usd:.5f}",
                    flush=True,
                )
            except Exception as exc:
                result = {"id": qid, "variant": name, "error": str(exc)}
                print(f"FAIL {qid} {name}: {exc}", flush=True)
            results.append(result)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps({
                "candidate_results": str(args.candidate_results),
                "descriptor_chars": args.descriptor_chars,
                "summary_chars": args.summary_chars,
                "variants": chosen_variants,
                "results": results,
            }, indent=2), encoding="utf-8")
    print(f"Wrote {args.out} (local only; evals/results/ is gitignored)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
