"""Compare prepared summaries with name-only menus in the same tree router."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from evals import RESULTS
from evals.bench import load_fixtures, validate_fixtures
from evals.hybrid import CODEBASE_FIXTURES, DEFAULT_BUNDLE
from fastindex.bundle import Bundle, load_lazy_bundle
from fastindex.strategies import StrategyConfig
from fastindex.strategies.tree_decision import TreeDecisionStrategy, make_decision_model


class NameOnlyBundle:
    """Keep the source tree unchanged while removing generated index summaries."""

    def __init__(self, bundle: Bundle) -> None:
        self.bundle = bundle

    def get_node(self, path: str):
        return self.bundle.get_node(path)

    def get_concept(self, path: str):
        return self.bundle.get_concept(path)

    def get_index(self, path: str) -> str | None:
        node = self.get_node(path)
        if node is None:
            return None
        children = [*node.children_dirs, *node.concepts]
        return "# Name-only menu\n" + "\n".join(
            f"- [{name}]({name})"
            for child in children
            if (name := Path(child).name + ("/" if child in node.children_dirs else ""))
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--fixtures", type=Path, default=CODEBASE_FIXTURES)
    parser.add_argument("--model", default="typesafe/jev-1.13.0")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--min-probability", type=float, default=0.04)
    parser.add_argument("--relative-probability", type=float, default=0.05)
    parser.add_argument("--call-budget", type=int, default=32)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--out", type=Path, default=RESULTS / "flask-tree-index-ablation.json")
    args = parser.parse_args()
    if min(args.top_k, args.call_budget) < 1 or not all(
        0 <= value <= 1 for value in (args.min_probability, args.relative_probability)
    ):
        parser.error("top-k and call budget must be positive; probabilities must be in [0, 1]")

    fixtures = load_fixtures(args.fixtures)
    validate_fixtures(fixtures, args.bundle)
    bundle = load_lazy_bundle(args.bundle)
    variants = {"prepared": bundle, "names": NameOnlyBundle(bundle)}
    config = {
        "bundle": str(args.bundle.resolve()), "fixtures": str(args.fixtures.resolve()),
        "model": args.model, "top_k": args.top_k, "call_budget": args.call_budget,
        "min_probability": args.min_probability,
        "relative_probability": args.relative_probability,
    }
    previous = {}
    if args.resume and args.out.exists():
        saved = json.loads(args.out.read_text(encoding="utf-8"))
        if saved["config"] != config:
            parser.error("saved run settings differ; choose another --out")
        previous = {(row["id"], row["variant"]): row for row in saved["results"]
                    if "error" not in row}

    model = make_decision_model(args.model)
    rows = []
    selected = fixtures[:args.limit] if args.limit else fixtures
    for number, fixture in enumerate(selected):
        gold = {item["path"] for item in fixture["gold"]}
        # Reverse order on alternate questions so provider drift does not favor one menu.
        for variant in ("prepared", "names") if number % 2 == 0 else ("names", "prepared"):
            key = fixture["id"], variant
            if key in previous:
                row = previous[key]
            else:
                try:
                    started = time.perf_counter()
                    result = TreeDecisionStrategy(model).retrieve(
                        fixture["query"], variants[variant],
                        StrategyConfig(
                            top_k=args.top_k, wall_time_budget_s=180,
                            model_call_budget=args.call_budget,
                            extra={"decision_return_files": True,
                                   "decision_min_probability": args.min_probability,
                                   "decision_relative_probability": args.relative_probability},
                        ),
                    )
                    paths = [span.path for span in result.spans]
                    row = {
                        "id": fixture["id"], "variant": variant, "paths": paths,
                        "file_recall": len(gold & set(paths)) / len(gold),
                        "complete_files": int(gold <= set(paths)),
                        "latency_ms": (time.perf_counter() - started) * 1000,
                        "model_calls": result.stats.model_calls,
                        "input_tokens": result.stats.input_tokens,
                        "cost_usd": result.stats.estimated_cost_usd,
                        "truncated": result.stats.truncated,
                    }
                except Exception as exc:
                    row = {"id": fixture["id"], "variant": variant, "error": str(exc)}
            rows.append(row)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.out.with_suffix(args.out.suffix + ".tmp")
            temporary.write_text(json.dumps({"config": config, "results": rows}, indent=2),
                                 encoding="utf-8")
            temporary.replace(args.out)
            print(f"{fixture['id']} {variant}: "
                  f"{row.get('file_recall', row.get('error'))}", flush=True)

    for variant in variants:
        valid = [row for row in rows if row["variant"] == variant and "error" not in row]
        if valid:
            print(f"{variant}: recall={statistics.mean(row['file_recall'] for row in valid):.3f} "
                  f"complete={sum(row['complete_files'] for row in valid)}/{len(valid)} "
                  f"errors={len(selected) - len(valid)}")
    return int(any("error" in row for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
