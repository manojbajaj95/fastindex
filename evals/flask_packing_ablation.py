"""Replay fixed Flask file rankings through wider lexical context windows."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from evals import RESULTS
from evals.bench import load_fixtures
from evals.hybrid import CODEBASE_FIXTURES, DEFAULT_BUNDLE, build_context, select_window_spans
from evals.metrics import GoldSpan, span_set_metrics
from fastindex.bundle import load_lazy_bundle
from fastindex.types import Span


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--fixtures", type=Path, default=CODEBASE_FIXTURES)
    parser.add_argument("--rerank", type=Path, default=RESULTS / "rg-rerank-ablation.json")
    parser.add_argument("--variant", default="base+rg")
    parser.add_argument("--out", type=Path, default=RESULTS / "flask-packing-ablation.json")
    args = parser.parse_args()
    fixtures = {row["id"]: row for row in load_fixtures(args.fixtures)}
    ranked = [row for row in json.loads(args.rerank.read_text())["results"]
              if row["variant"] == args.variant and "error" not in row]
    bundle = load_lazy_bundle(args.bundle)
    rows = []
    for row in ranked:
        fixture = fixtures[row["id"]]
        gold = [GoldSpan(g["path"], g["start_line"], g["end_line"])
                for g in fixture["gold"]]
        files = [Span(path, 1, len(concept.lines), concept.raw)
                 for path in row["ranked_paths"]
                 if (concept := bundle.get_concept(path)) is not None]
        for budget in (64_000, 128_000, 200_000):
            for width in (0, 80, 160, 320, 640):
                started = time.perf_counter()
                selected = files if width == 0 else select_window_spans(
                    fixture["query"], files, whole_file_lines=1200,
                    window_lines=width, windows_per_file=3,
                )
                context, included, _ = build_context(selected, budget)
                packing_ms = (time.perf_counter() - started) * 1000
                sr, _, _, lr, lp, _ = span_set_metrics(included, gold)
                rows.append({
                    "id": row["id"], "budget_chars": budget,
                    "window_lines": width, "span_recall": sr,
                    "line_recall": lr, "line_precision": lp,
                    "context_chars": len(context), "included_spans": len(included),
                    "packing_ms": packing_ms,
                })
    summary = {}
    for budget in (64_000, 128_000, 200_000):
        for width in (0, 80, 160, 320, 640):
            selected = [row for row in rows if row["budget_chars"] == budget
                        and row["window_lines"] == width]
            summary[f"{budget}/{width}"] = {
                "n": len(selected),
                "span_recall": statistics.mean(row["span_recall"] for row in selected),
                "complete_spans": sum(row["span_recall"] == 1 for row in selected),
                "line_recall": statistics.mean(row["line_recall"] for row in selected),
                "line_precision": statistics.mean(row["line_precision"] for row in selected),
                "context_chars": statistics.mean(row["context_chars"] for row in selected),
                "packing_ms": statistics.mean(row["packing_ms"] for row in selected),
            }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "rerank": str(args.rerank), "variant": args.variant,
        "whole_file_lines": 1200, "windows_per_file": 3,
        "summary": summary, "results": rows,
    }, indent=2))
    for key, result in summary.items():
        print(f"{key}: span={result['span_recall']:.3f} "
              f"complete={result['complete_spans']}/{result['n']} "
              f"context={result['context_chars']:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
