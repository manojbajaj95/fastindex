"""Replay frozen Flask candidates through wide lexical windows and one Luna pass."""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import time
from pathlib import Path

import tiktoken

from evals import RESULTS
from evals.arb_oracle_span_ablation import _merged_match_spans, _pack, _round_robin
from evals.baselines.pi_agent import _assistant_messages, _json_object
from evals.bench import load_fixtures
from evals.hybrid import CODEBASE_FIXTURES, DEFAULT_BUNDLE
from evals.metrics import GoldSpan, span_set_metrics
from fastindex.bundle import load_lazy_bundle
from fastindex.types import Span


def _render(spans: list[Span]) -> str:
    return "".join(
        f"\n===== {span.path} =====\n" + "".join(
            f"{number:05d}: {line}"
            for number, line in enumerate(
                span.text.splitlines(keepends=True), start=span.start_line
            )
        )
        for span in spans
    )


def _model_ranges(stdout: str, visible: list[Span], files: dict[str, Span], top_k: int
                  ) -> tuple[list[Span], int, dict[str, float]]:
    messages = _assistant_messages(stdout)
    texts = [part.get("text", "") for message in messages
             for part in message.get("content", []) if part.get("type") == "text"]
    payload = _json_object(texts[-1] if texts else "")
    coverage: dict[str, list[tuple[int, int]]] = {}
    for span in visible:
        coverage.setdefault(span.path, []).append((span.start_line, span.end_line))
    selected: list[Span] = []
    invalid = 0
    for item in payload.get("spans", [])[:top_k]:
        try:
            path = str(item["path"]).removeprefix("./")
            start, end = int(item["start_line"]), int(item["end_line"])
            file = files[path]
            lines = file.text.splitlines(keepends=True)
            covered = {line for begin, finish in coverage.get(path, [])
                       for line in range(begin, finish + 1)}
            if start < 1 or end < start or end > len(lines) or any(
                line not in covered for line in range(start, end + 1)
            ):
                raise ValueError("out-of-window range")
        except (KeyError, TypeError, ValueError):
            invalid += 1
            continue
        selected.append(Span(path, start, end, "".join(lines[start - 1:end])))

    # Coalescing is deterministic; the model chooses evidence, not source text.
    merged: list[Span] = []
    for path in dict.fromkeys(span.path for span in selected):
        file = files[path]
        lines = file.text.splitlines(keepends=True)
        intervals = sorted((span.start_line, span.end_line) for span in selected
                           if span.path == path)
        for start, end in intervals:
            if merged and merged[-1].path == path and start <= merged[-1].end_line + 1:
                prior = merged[-1]
                merged[-1] = Span(path, prior.start_line, max(prior.end_line, end),
                                  "".join(lines[prior.start_line - 1:max(prior.end_line, end)]))
            else:
                merged.append(Span(path, start, end, "".join(lines[start - 1:end])))
    usage = {"calls": 0, "input": 0, "cache_read": 0, "cache_write": 0,
             "output": 0, "cost": 0.0}
    for message in messages:
        raw = message.get("usage") or {}
        if raw.get("totalTokens"):
            usage["calls"] += 1
        usage["input"] += int(raw.get("input") or 0)
        usage["cache_read"] += int(raw.get("cacheRead") or 0)
        usage["cache_write"] += int(raw.get("cacheWrite") or 0)
        usage["output"] += int(raw.get("output") or 0)
        usage["cost"] += float((raw.get("cost") or {}).get("total") or 0)
    return merged, invalid, usage


def _score(spans: list[Span], gold: list[GoldSpan]) -> dict[str, float | int]:
    sr, _, _, lr, lp, _ = span_set_metrics(spans, gold)
    return {"span_recall": sr, "line_recall": lr, "line_precision": lp,
            "complete_spans": int(sr == 1), "complete_lines": int(lr == 1)}


def _candidate_paths(row: dict, mode: str, count: int) -> list[str]:
    if mode == "pool":
        return row["pool"][:count]
    if mode == "ranked":
        return row["ranked_paths"][:count]
    rank = {path: index for index, path in enumerate(row["pool"])}
    scores = row["rerank_scores"]
    ordered = sorted(row["pool"], key=lambda path: (-scores[path], rank[path]))
    if ordered[:len(row["ranked_paths"])] != row["ranked_paths"]:
        raise ValueError(f"Cannot reconstruct saved rerank order for {row['id']}")
    return ordered[:count]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--fixtures", type=Path, default=CODEBASE_FIXTURES)
    parser.add_argument("--rerank", type=Path, default=RESULTS / "rg-rerank-ablation.json")
    parser.add_argument("--variant", default="base+rg")
    parser.add_argument("--candidates", choices=("pool", "ranked", "ranked-pool"),
                        default="pool")
    parser.add_argument("--candidate-k", type=int, default=16)
    parser.add_argument("--radius", type=int, default=200)
    parser.add_argument("--anchors-per-file", type=int, default=4)
    parser.add_argument("--pack-order", choices=("round-robin", "sequential"),
                        default="round-robin")
    parser.add_argument("--input-tokens", type=int, default=32_000)
    parser.add_argument("--final-tokens", type=int, default=8_000)
    parser.add_argument("--top-k", type=int, default=16)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--ids", help="Comma-separated fixture IDs for a paired repeat")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Score windows without a model call")
    parser.add_argument("--model", default="openai/gpt-5.6-luna")
    parser.add_argument("--out", type=Path, default=RESULTS / "llm-merge-flask.json")
    args = parser.parse_args()
    if min(args.candidate_k, args.input_tokens, args.final_tokens, args.top_k) < 1:
        parser.error("candidate, token, and output limits must be positive")
    pi = shutil.which("pi") if not args.offline else None
    if not args.offline and not pi:
        parser.error("Pi CLI is needed for the no-tools model pass")
    fixtures = {row["id"]: row for row in load_fixtures(args.fixtures)}
    rankings = [row for row in json.loads(args.rerank.read_text())["results"]
                if row["variant"] == args.variant and "error" not in row]
    if args.ids:
        requested = {part.strip() for part in args.ids.split(",") if part.strip()}
        if not requested:
            parser.error("--ids must name at least one fixture")
        if missing := requested - {row["id"] for row in rankings}:
            parser.error(f"IDs missing from the saved ranking: {', '.join(sorted(missing))}")
        rankings = [row for row in rankings if row["id"] in requested]
    if args.limit:
        rankings = rankings[:args.limit]
    bundle = load_lazy_bundle(args.bundle)
    encoder = tiktoken.get_encoding("cl100k_base")
    rows = []
    if args.resume and args.out.exists():
        saved = json.loads(args.out.read_text())
        for key in ("bundle", "fixtures", "rerank", "variant", "candidates", "candidate_k",
                    "radius", "anchors_per_file", "pack_order", "input_tokens",
                    "final_tokens", "top_k",
                    "model", "offline"):
            if str(saved["config"][key]) != str(getattr(args, key)):
                parser.error(f"Cannot resume with changed {key}")
        rows = saved["results"]
    done = {row["id"] for row in rows}
    for row in rankings:
        if row["id"] in done:
            continue
        started = time.perf_counter()
        fixture = fixtures[row["id"]]
        gold = [GoldSpan(g["path"], g["start_line"], g["end_line"])
                for g in fixture["gold"]]
        paths = _candidate_paths(row, args.candidates, args.candidate_k)
        files = {path: Span(path, 1, len(concept.lines), concept.raw)
                 for path in paths if (concept := bundle.get_concept(path)) is not None}
        windows = _merged_match_spans(
            fixture["query"], list(files.values()), radius=args.radius,
            anchors_per_file=args.anchors_per_file,
        )
        if args.pack_order == "round-robin":
            windows = _round_robin(windows)
        visible, input_tokens, _ = _pack(windows, args.input_tokens, encoder)
        direct, direct_tokens, _ = _pack(windows, args.final_tokens, encoder)
        prepare_ms = (time.perf_counter() - started) * 1000
        prompt = (
            "Select source evidence needed to answer the question. Do not answer it. "
            "Return only JSON: {\"spans\":[{\"path\":\"relative/path\","
            "\"start_line\":1,\"end_line\":10}]}. "
            f"Select at most {args.top_k} ranges. Cite only lines shown below. "
            "Include enough surrounding implementation, tests, and configuration to "
            "explain the behavior; do not clip a relevant block to one matching line. "
            "Combine overlapping or adjacent useful ranges in the same file.\n\n"
            f"Question: {fixture['query']}\n\nSource windows:\n{_render(visible)}"
        )
        completed = None
        model_ms = 0.0
        if not args.offline:
            command = [pi, "--print", "--mode", "json", "--no-session", "--no-extensions",
                       "--no-skills", "--no-prompt-templates", "--no-context-files",
                       "--no-tools", "--thinking", "low", "--model", args.model,
                       "--system-prompt", "You are a precise code-evidence selector.", prompt]
            model_started = time.perf_counter()
            completed = subprocess.run(command, cwd=args.bundle, capture_output=True,
                                       text=True, timeout=120, check=False)
            model_ms = (time.perf_counter() - model_started) * 1000
        result = {
            "id": row["id"], "candidate_paths": paths,
            "visible_paths": list(dict.fromkeys(span.path for span in visible)),
            "input_context_tokens": input_tokens, "direct_context_tokens": direct_tokens,
            "prepare_ms": prepare_ms, "model_ms": model_ms,
            "visible": _score(visible, gold), "direct": _score(direct, gold),
        }
        if completed is not None:
            try:
                if completed.returncode:
                    raise RuntimeError((completed.stderr or completed.stdout)[-1000:])
                selected, invalid, usage = _model_ranges(
                    completed.stdout, visible, files, args.top_k
                )
                final, final_tokens, _ = _pack(selected, args.final_tokens, encoder)
                result.update({"selected": _score(final, gold), "selected_tokens": final_tokens,
                               "selected_spans": len(final), "invalid_ranges": invalid,
                               "selected_ranges": [{"path": span.path,
                                                    "start_line": span.start_line,
                                                    "end_line": span.end_line}
                                                   for span in final],
                               "usage": usage})
            except (RuntimeError, ValueError) as error:
                result["error"] = str(error)
        rows.append(result)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        output = json.dumps({"config": vars(args) | {"bundle": str(args.bundle),
                                                      "fixtures": str(args.fixtures),
                                                      "rerank": str(args.rerank),
                                                      "out": str(args.out)},
                             "results": rows}, indent=2)
        temporary = args.out.with_suffix(args.out.suffix + ".tmp")
        temporary.write_text(output)
        temporary.replace(args.out)
        model_score = result.get("selected", {}).get(
            "line_recall", "skipped" if args.offline else "error"
        )
        print(f"{len(rows)}/{len(rankings)} {row['id']} direct="
              f"{result['direct']['line_recall']:.3f} model={model_score}", flush=True)
    valid = [row for row in rows if "selected" in row]
    summarized = rows if args.offline else valid
    if summarized:
        for stage in (("visible", "direct") if args.offline else ("visible", "direct", "selected")):
            complete = sum(row[stage]["complete_lines"] for row in summarized)
            print(f"{stage}: complete_lines={complete}"
                  f"/{len(summarized)} mean_line_recall="
                  f"{statistics.mean(row[stage]['line_recall'] for row in summarized):.3f}")
    if valid:
        print(f"cost/query=${statistics.mean(row['usage']['cost'] for row in valid):.5f} "
              f"model_seconds/query={statistics.mean(row['model_ms'] for row in valid)/1000:.2f}")
    return 0 if args.offline or len(valid) == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
