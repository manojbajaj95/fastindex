#!/usr/bin/env python3
"""Analyze bench result JSON (retrieval quality + ops summary + misses).

Usage:
  uv run python -m evals.analyze
  uv run python -m evals.analyze evals/results/bench-….json
  uv run python -m evals.analyze --misses --threshold 1.0
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from evals import REPO_ROOT, RESULTS, SAMPLE_QUERIES
from evals.metrics import row_get


def latest_result() -> Path | None:
    results = sorted(RESULTS.glob("bench-*.json"))
    return results[-1] if results else None


def mean(xs: list[float]) -> float:
    return statistics.mean(xs) if xs else 0.0


def load_gold(fixtures_path: str | Path) -> dict[str, list[dict]]:
    path = Path(fixtures_path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.exists():
        return {}
    gold_by_id: dict[str, list[dict]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        row = json.loads(line)
        qid = row.get("id") or row["query"][:40]
        gold_by_id[qid] = row.get("gold") or []
    return gold_by_id


def fnum(row: dict, key: str, *legacy: str) -> float:
    return float(row_get(row, key, *legacy, default=0) or 0)


def analyze(path: Path, *, threshold: float, show_misses: bool, top_misses: int) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("results") or []
    gold_by_id = load_gold(data.get("fixtures") or SAMPLE_QUERIES)

    print(f"File: {path}")
    print(f"Bundle: {data.get('bundle')}")
    print(f"Strategies: {', '.join(data.get('strategies') or [])}")
    print()

    by_strat: dict[str, list[dict]] = defaultdict(list)
    skipped: dict[str, int] = defaultdict(int)
    errors: dict[str, int] = defaultdict(int)

    for r in rows:
        name = r.get("strategy") or "?"
        if r.get("skipped"):
            skipped[name] += 1
            continue
        if r.get("error"):
            errors[name] += 1
            continue
        by_strat[name].append(r)

    print("## Strategy summary (means over scored queries)")
    print(
        f"{'strategy':12} {'n':>3} {'rec':>6} {'prec':>6} {'f1':>6} "
        f"{'lat_ms':>8} {'tok_in':>7} {'$':>8} {'track':>6} {'skip':>4} {'err':>3}"
    )
    summary_rows = []
    for name in sorted(set(by_strat) | set(skipped) | set(errors)):
        scored = by_strat.get(name, [])
        tracks = {row_get(r, "track", "D_track", default=None) for r in scored}
        track = ",".join(sorted(str(t) for t in tracks if t)) or "-"
        summary_rows.append(
            {
                "strategy": name,
                "n": len(scored),
                "rec": mean([fnum(r, "span_recall", "C_span_recall") for r in scored]),
                "prec": mean(
                    [fnum(r, "span_precision", "C_span_precision") for r in scored]
                ),
                "f1": mean([fnum(r, "span_f1", "C_span_f1") for r in scored]),
                "lat": mean([fnum(r, "latency_ms", "A_wall_ms") for r in scored]),
                "tok_in": mean(
                    [fnum(r, "input_tokens", "B_input_tokens") for r in scored]
                ),
                "cost": mean([fnum(r, "cost_usd", "B_cost_usd") for r in scored]),
                "track": track,
                "skip": skipped.get(name, 0),
                "err": errors.get(name, 0),
            }
        )

    summary_rows.sort(key=lambda s: (-s["rec"], s["lat"] if s["n"] else 1e18))
    for s in summary_rows:
        print(
            f"{s['strategy']:12} {s['n']:3d} {s['rec']:6.2f} {s['prec']:6.2f} "
            f"{s['f1']:6.2f} {s['lat']:8.1f} {s['tok_in']:7.0f} {s['cost']:8.4f} "
            f"{s['track']:>6} {s['skip']:4d} {s['err']:3d}"
        )

    print()
    print(
        "Ranking: higher mean span_recall, then lower mean latency_ms "
        "(compare ops within same track + model_id)."
    )
    print(
        "Retrieval quality: span/line recall·precision·F1  |  "
        "Ops: latency_ms, tokens, cost_usd, calls, hops  |  Setup: track, model_id"
    )
    print()

    misses: list[dict] = []
    for _name, scored in by_strat.items():
        for r in scored:
            if fnum(r, "span_recall", "C_span_recall") < threshold:
                misses.append(r)

    misses.sort(
        key=lambda r: (
            fnum(r, "span_recall", "C_span_recall"),
            -fnum(r, "latency_ms", "A_wall_ms"),
        )
    )
    print(f"## Misses (span_recall < {threshold:g}): {len(misses)}")
    if not misses:
        print("None — every scored query hit all gold spans (at this threshold).")
    else:
        print(f"{'strategy':12} {'id':22} {'rec':>5} {'f1':>5} {'lat_ms':>8}  query")
        for r in misses[:top_misses]:
            q = (r.get("query") or "")[:50]
            print(
                f"{r.get('strategy', '?'):12} {str(r.get('id', '')):22} "
                f"{fnum(r, 'span_recall', 'C_span_recall'):5.2f} "
                f"{fnum(r, 'span_f1', 'C_span_f1'):5.2f} "
                f"{fnum(r, 'latency_ms', 'A_wall_ms'):8.1f}  {q}"
            )
        if len(misses) > top_misses:
            print(f"… {len(misses) - top_misses} more")

    if show_misses and misses:
        print()
        print("## Miss detail (gold vs predicted paths)")
        for r in misses[:top_misses]:
            qid = r.get("id")
            print(f"\n### {r.get('strategy')} / {qid}")
            print(f"Q: {r.get('query')}")
            print(
                f"recall={fnum(r, 'span_recall', 'C_span_recall'):.2f} "
                f"precision={fnum(r, 'span_precision', 'C_span_precision'):.2f} "
                f"cost_usd={fnum(r, 'cost_usd', 'B_cost_usd'):.4f}"
            )
            gold = gold_by_id.get(str(qid), [])
            if gold:
                print("gold:")
                for g in gold:
                    print(f"  {g['path']}:{g['start_line']}-{g['end_line']}")
            else:
                print("gold: (not found in fixtures)")
            print("predicted:")
            for s in r.get("spans") or []:
                print(f"  {s['path']}:{s['start_line']}-{s['end_line']}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze fastindex bench JSON results")
    parser.add_argument(
        "result",
        nargs="?",
        type=Path,
        default=None,
        help="Path to evals/results/bench-*.json (default: latest)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=1.0,
        help="Flag miss when span_recall < this (default: 1.0 = any incomplete gold hit)",
    )
    parser.add_argument(
        "--misses",
        action="store_true",
        help="Print gold vs predicted paths for misses",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Max misses to list (default: 20)",
    )
    args = parser.parse_args()

    path = args.result
    if path is None:
        path = latest_result()
        if path is None:
            print(
                "No evals/results/bench-*.json found. Run: uv run python -m evals.bench",
                file=sys.stderr,
            )
            return 1
    elif not path.is_absolute():
        cand = Path(path)
        if not cand.exists():
            cand = REPO_ROOT / path
        path = cand

    if not path.exists():
        print(f"Not found: {path}", file=sys.stderr)
        return 1

    return analyze(path, threshold=args.threshold, show_misses=args.misses, top_misses=args.top)


if __name__ == "__main__":
    raise SystemExit(main())
