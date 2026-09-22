#!/usr/bin/env python3
"""Evaluate cheap Jev file routing followed by one grounded answer call."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals import FIXTURES, RESULTS
from evals.baselines.bm25 import Bm25Strategy
from evals.bench import load_fixtures, validate_fixtures
from evals.metrics import GoldSpan, build_metrics
from fastindex.bundle import Bundle, load_lazy_bundle
from fastindex.models import ModelUsage, RemoteModel, estimate_cost_usd
from fastindex.strategies import StrategyConfig
from fastindex.strategies.tree_decision import TreeDecisionStrategy
from fastindex.types import RetrieveResult, Span

CODEBASE_FIXTURES = FIXTURES / "queries" / "codebase_qa.jsonl"
DEFAULT_BUNDLE = FIXTURES / "external" / "codebase-qa-flask"
DEFAULT_GOLD = (
    Path(__file__).resolve().parents[2]
    / "agent-learning-bench/tasks/codebase-qa/environment/data/gold.json"
)


def candidate_files(
    query: str,
    bundle: Bundle,
    *,
    file_k: int,
    bm25_k: int,
    wall_budget: float,
    call_budget: int,
    min_probability: float,
    relative_probability: float,
) -> RetrieveResult:
    """Return whole candidate files, with Jev routes first and BM25 paths as fallback."""
    routed = TreeDecisionStrategy().retrieve(
        query,
        bundle,
        StrategyConfig(
            top_k=file_k,
            wall_time_budget_s=wall_budget,
            model_call_budget=call_budget,
            extra={
                "decision_return_files": True,
                "decision_min_probability": min_probability,
                "decision_relative_probability": relative_probability,
            },
        ),
    )
    spans = list(routed.spans)
    seen = {span.path for span in spans}
    if bm25_k:
        lexical = Bm25Strategy().retrieve(query, bundle, StrategyConfig(top_k=bm25_k))
        for hit in lexical.spans:
            if hit.path in seen:
                continue
            concept = bundle.get_concept(hit.path)
            if concept is None:
                continue
            seen.add(hit.path)
            spans.append(
                Span(
                    path=hit.path,
                    start_line=1,
                    end_line=len(concept.lines),
                    text=concept.raw,
                    score=hit.score,
                )
            )
    routed.stats.extra["bm25_k"] = bm25_k
    routed.stats.extra["candidate_paths"] = [span.path for span in spans]
    return RetrieveResult(spans=spans, stats=routed.stats)


def build_context(spans: list[Span], max_chars: int) -> tuple[str, list[Span], list[str]]:
    """Build line-numbered context, keeping Jev-ranked files before lexical fallbacks."""
    blocks: list[str] = []
    included: list[Span] = []
    dropped: list[str] = []
    used = 0
    for span in spans:
        numbered = "".join(
            f"{line_no:05d}: {line}"
            for line_no, line in enumerate(span.text.splitlines(keepends=True), start=1)
        )
        block = f"\n===== {span.path} =====\n{numbered}"
        if blocks and used + len(block) > max_chars:
            dropped.append(span.path)
            continue
        blocks.append(block)
        included.append(span)
        used += len(block)
    return "".join(blocks), included, dropped


def _json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("Model did not return a JSON object")


def _usage_fields(prefix: str, usage: ModelUsage) -> dict[str, Any]:
    cost = estimate_cost_usd(usage.input_tokens, usage.output_tokens, usage.model_id)
    return {
        f"{prefix}_model": usage.model_id,
        f"{prefix}_calls": usage.calls,
        f"{prefix}_input_tokens": usage.input_tokens,
        f"{prefix}_output_tokens": usage.output_tokens,
        f"{prefix}_cost_usd": cost,
    }


def _write_results(path: Path, args: argparse.Namespace, results: list[dict]) -> None:
    completed = [row for row in results if "error" not in row]
    scored = [row for row in results if "answer_score" in row]
    aggregate = {}
    if completed:
        aggregate = {
            "questions": len(completed),
            "file_recall": sum(row["file_recall"] for row in completed) / len(completed),
            "span_coverage": sum(row["span_recall"] for row in completed) / len(completed),
            "candidate_files": sum(row["candidate_files"] for row in completed)
            / len(completed),
            "context_chars": sum(row["context_chars"] for row in completed) / len(completed),
            "latency_ms": sum(row["latency_ms"] for row in completed) / len(completed),
            "cost_usd": sum(row["cost_usd"] for row in completed) / len(completed),
        }
    if scored:
        aggregate.update({
            "answer_score": sum(row["answer_score"] for row in scored) / len(scored),
            "judge_cost_usd": sum(row["judge_cost_usd"] for row in scored) / len(scored),
        })
    payload = {
        "recorded": (
            "Jev file routing + BM25 fallback"
            if args.retrieval_only
            else "Jev file routing + BM25 fallback + one grounded answer call"
        ),
        "bundle": str(args.bundle),
        "fixtures": str(args.fixtures),
        "answer_model": args.answer_model,
        "judge_model": args.judge_model,
        "file_k": args.file_k,
        "bm25_k": args.bm25_k,
        "decision_min_probability": args.decision_min_probability,
        "decision_relative_probability": args.decision_relative_probability,
        "max_context_chars": args.max_context_chars,
        "aggregate": aggregate,
        "results": results,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--fixtures", type=Path, default=CODEBASE_FIXTURES)
    parser.add_argument("--gold-answers", type=Path, default=DEFAULT_GOLD)
    parser.add_argument(
        "--answer-model",
        default=os.getenv("FASTINDEX_HYBRID_MODEL") or "gpt-5.6-luna",
    )
    parser.add_argument(
        "--judge-model",
        default=os.getenv("FASTINDEX_JUDGE_MODEL") or "gpt-5.6-luna",
    )
    parser.add_argument("--file-k", type=int, default=8)
    parser.add_argument("--bm25-k", type=int, default=2)
    parser.add_argument("--decision-min-probability", type=float, default=0.12)
    parser.add_argument("--decision-relative-probability", type=float, default=0.2)
    parser.add_argument("--max-context-chars", type=int, default=200_000)
    parser.add_argument("--wall-budget", type=float, default=180.0)
    parser.add_argument("--call-budget", type=int, default=32)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if min(args.file_k, args.max_context_chars, args.call_budget) < 1 or args.bm25_k < 0:
        parser.error("file-k, context, and call budget must be positive; bm25-k cannot be negative")
    if not 0 <= args.decision_min_probability <= 1:
        parser.error("decision-min-probability must be between 0 and 1")
    if not 0 <= args.decision_relative_probability <= 1:
        parser.error("decision-relative-probability must be between 0 and 1")
    if not args.retrieval_only and not args.gold_answers.is_file():
        parser.error(f"gold answers not found: {args.gold_answers}")

    fixtures = load_fixtures(args.fixtures)
    if args.limit is not None:
        fixtures = fixtures[: args.limit]
    validate_fixtures(fixtures, args.bundle)
    gold_answers = {}
    if not args.retrieval_only:
        gold_answers = {
            f"q{int(row['question_id']):03d}": row["answer"]
            for row in json.loads(args.gold_answers.read_text(encoding="utf-8"))
        }
    bundle = load_lazy_bundle(args.bundle)
    answer_model = RemoteModel(chat_model=args.answer_model, timeout=args.wall_budget)
    judge_model = RemoteModel(chat_model=args.judge_model, timeout=args.wall_budget)
    if args.out is None:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        args.out = RESULTS / f"codebase-qa-hybrid-{timestamp}.json"

    results: list[dict] = []
    for fixture in fixtures:
        qid = fixture["id"]
        query = fixture["query"]
        gold = [
            GoldSpan(g["path"], int(g["start_line"]), int(g["end_line"]))
            for g in fixture["gold"]
        ]
        try:
            started = time.perf_counter()
            retrieval_started = time.perf_counter()
            candidates = candidate_files(
                query,
                bundle,
                file_k=args.file_k,
                bm25_k=args.bm25_k,
                wall_budget=args.wall_budget,
                call_budget=args.call_budget,
                min_probability=args.decision_min_probability,
                relative_probability=args.decision_relative_probability,
            )
            retrieval_ms = (time.perf_counter() - retrieval_started) * 1000
            context, included, dropped = build_context(
                candidates.spans, args.max_context_chars
            )
            metrics = build_metrics(candidates.stats, included, gold, latency_ms=retrieval_ms)

            if args.retrieval_only:
                row = {
                    "id": qid,
                    "query": query,
                    "candidate_paths": [span.path for span in included],
                    "dropped_paths": dropped,
                    "candidate_files": len(included),
                    "context_chars": len(context),
                    **metrics.to_bench_fields(),
                }
                results.append(row)
                print(
                    f"{qid} file_rec={metrics.file_recall:.2f} "
                    f"coverage={metrics.span_recall:.2f} files={len(included)} "
                    f"lat={retrieval_ms / 1000:.1f}s ${metrics.cost_usd:.4f}",
                    flush=True,
                )
                _write_results(args.out, args, results)
                continue

            answer_started = time.perf_counter()
            answer = answer_model.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "Answer codebase questions only from the supplied source files. "
                            "Ground the answer in concrete files, symbols, and behavior. "
                            "If the supplied files are insufficient, say what is missing."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Question:\n{query}\n\nCandidate source files:{context}",
                    },
                ]
            )
            answer_ms = (time.perf_counter() - answer_started) * 1000

            judge_started = time.perf_counter()
            judged = judge_model.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "Judge whether an answer agrees with the reference on the facts "
                            "that matter: files, symbols, and behavior. Paraphrase is allowed. "
                            "Use an integer score from 1 (wrong or missing) to 5 (fully correct). "
                            "Return JSON only: {\"score\": 1, \"reasoning\": \"...\"}."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Question:\n{query}\n\nReference:\n{gold_answers[qid]}"
                            f"\n\nCandidate answer:\n{answer.text}"
                        ),
                    },
                ]
            )
            judge_ms = (time.perf_counter() - judge_started) * 1000
            verdict = _json_object(judged.text)
            raw_score = int(verdict["score"])
            if not 1 <= raw_score <= 5:
                raise ValueError(f"Judge score outside 1..5: {raw_score}")
            answer_usage = _usage_fields("answer", answer.usage)
            judge_usage = _usage_fields("judge", judged.usage)
            end_to_end_ms = (time.perf_counter() - started) * 1000 - judge_ms
            row = {
                "id": qid,
                "query": query,
                "answer": answer.text,
                "answer_score_raw": raw_score,
                "answer_score": (raw_score - 1) / 4,
                "judge_reasoning": str(verdict.get("reasoning") or ""),
                "candidate_paths": [span.path for span in included],
                "dropped_paths": dropped,
                "candidate_files": len(included),
                "context_chars": len(context),
                **metrics.to_bench_fields(),
                "retrieval_latency_ms": retrieval_ms,
                "answer_latency_ms": answer_ms,
                "judge_latency_ms": judge_ms,
                "latency_ms": end_to_end_ms,
                **answer_usage,
                **judge_usage,
            }
            row["cost_usd"] = metrics.cost_usd + float(answer_usage["answer_cost_usd"])
            results.append(row)
            print(
                f"{qid} score={row['answer_score']:.2f} file_rec={metrics.file_recall:.2f} "
                f"coverage={metrics.span_recall:.2f} files={len(included)} "
                f"lat={end_to_end_ms / 1000:.1f}s ${row['cost_usd']:.4f}",
                flush=True,
            )
        except Exception as exc:
            results.append({"id": qid, "query": query, "error": str(exc)})
            print(f"FAIL {qid}: {exc}", flush=True)
        _write_results(args.out, args, results)

    print(f"Wrote {args.out} (local only; evals/results/ is gitignored)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
