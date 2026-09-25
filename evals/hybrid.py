#!/usr/bin/env python3
"""Evaluate fused file retrieval, optional Jev reranking, and one answer call."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals import FIXTURES, RESULTS
from evals.baselines.bm25 import Bm25Strategy, FtsStrategy, tokenize
from evals.baselines.ripgrep import STOP_WORDS, rank_rg, rg_hits
from evals.bench import load_fixtures, validate_fixtures
from evals.metrics import GoldSpan, build_metrics
from fastindex.bundle import Bundle, load_lazy_bundle
from fastindex.models import ModelUsage, RemoteModel, estimate_cost_usd
from fastindex.strategies import StrategyConfig
from fastindex.strategies.tree_decision import TreeDecisionStrategy, TypeSafeModel
from fastindex.types import RetrieveResult, RunStats, Span

CODEBASE_FIXTURES = FIXTURES / "queries" / "codebase_qa.jsonl"
DEFAULT_BUNDLE = FIXTURES / "external" / "codebase-qa-flask"
DEFAULT_GOLD = (
    Path(__file__).resolve().parents[2]
    / "agent-learning-bench/tasks/codebase-qa/environment/data/gold.json"
)


_SOURCE_STRATEGIES = {"bm25": Bm25Strategy, "fts": FtsStrategy}


def _unique_lexical_files(
    name: str, query: str, bundle: Bundle, unique_k: int
) -> tuple[list[str], dict[str, str], str]:
    """Return ranked unique paths and their best matching lexical excerpts."""
    if unique_k == 0:
        return [], {}, "warm"
    strategy = _SOURCE_STRATEGIES[name]()
    result = strategy.retrieve(
        query,
        bundle,
        StrategyConfig(top_k=max(32, unique_k * 8)),
    )
    paths: list[str] = []
    previews: dict[str, str] = {}
    for hit in result.spans:
        if hit.path in previews:
            continue
        paths.append(hit.path)
        previews[hit.path] = hit.text[:2400]
        if len(paths) == unique_k:
            break
    return paths, previews, result.stats.track


def _file_summary(bundle: Bundle, path: str) -> str:
    parent = Path(path).parent.as_posix()
    if parent == ".":
        parent = ""
    index = bundle.get_index(parent) or ""
    name = Path(path).name
    for line in index.splitlines():
        if f"]({name})" in line or f"](./{name})" in line:
            return line.strip()[:1000]
    return path


def _rrf(rankings: dict[str, list[str]]) -> tuple[list[str], dict[str, float]]:
    scores: dict[str, float] = {}
    for paths in rankings.values():
        for rank, path in enumerate(paths, start=1):
            scores[path] = scores.get(path, 0.0) + 1 / (60 + rank)
    ordered = sorted(scores, key=lambda path: (-scores[path], path))
    return ordered, scores


def candidate_files(
    query: str,
    bundle: Bundle,
    *,
    sources: tuple[str, ...],
    file_k: int,
    jev_k: int,
    candidate_k: int,
    bm25_k: int,
    fts_k: int,
    rg_k: int,
    jev_rerank: bool,
    descriptor_chars: int,
    wall_budget: float,
    call_budget: int,
    min_probability: float,
    relative_probability: float,
) -> RetrieveResult:
    """Fuse unique file rankings and optionally rerank the pool with Jev Nouls."""
    rankings: dict[str, list[str]] = {}
    previews: dict[str, str] = {}
    stats = RunStats(track="warm")
    if "jev" in sources:
        routed = TreeDecisionStrategy().retrieve(
            query,
            bundle,
            StrategyConfig(
                top_k=jev_k,
                wall_time_budget_s=wall_budget,
                model_call_budget=call_budget,
                extra={
                    "decision_return_files": True,
                    "decision_min_probability": min_probability,
                    "decision_relative_probability": relative_probability,
                },
            ),
        )
        rankings["jev"] = list(dict.fromkeys(span.path for span in routed.spans))
        stats = routed.stats

    for name, unique_k in (("bm25", bm25_k), ("fts", fts_k)):
        if name not in sources:
            continue
        paths, excerpts, track = _unique_lexical_files(
            name, query, bundle, unique_k
        )
        rankings[name] = paths
        previews.update(excerpts)
        if "jev" not in sources:
            stats.track = track

    rg_search_ms = 0.0
    if "rg" in sources and rg_k:
        files = [concept.path for concept in bundle.iter_concepts()]
        hits, rg_search_ms = rg_hits(query, bundle.root, files)
        rankings["rg"] = rank_rg(hits, len(files))[:rg_k]

    ordered, rrf_scores = _rrf(rankings)
    pool = ordered[:candidate_k]
    rerank_scores: dict[str, float] = {}
    rerank_usage = ModelUsage(model_id="typesafe/jev-latest")
    if jev_rerank and pool:
        model = TypeSafeModel()
        descriptors = [
            (
                f"Path: {path}\nIndex summary: {_file_summary(bundle, path)}\n"
                f"Lexical evidence:\n{previews.get(path, '(none)')}"
            )[:descriptor_chars]
            for path in pool
        ]
        values, rerank_usage = model.score_relevance(query, descriptors)
        rerank_scores = dict(zip(pool, values, strict=True))
        ordered = sorted(pool, key=lambda path: (-rerank_scores[path], -rrf_scores[path], path))
        stats.model_calls += rerank_usage.calls
        stats.input_tokens += rerank_usage.input_tokens
        stats.output_tokens += rerank_usage.output_tokens
        stats.estimated_cost_usd += (
            rerank_usage.input_tokens * model.input_cost_per_million / 1_000_000
        )
        stats.model_id = rerank_usage.model_id
    else:
        ordered = pool

    spans: list[Span] = []
    for path in ordered[:file_k]:
        concept = bundle.get_concept(path)
        if concept is None:
            continue
        spans.append(Span(
            path=path,
            start_line=1,
            end_line=len(concept.lines),
            text=concept.raw,
            score=rerank_scores.get(path, rrf_scores[path]),
        ))
    stats.extra.update({
        "sources": list(sources),
        "source_rankings": rankings,
        "candidate_pool": pool,
        "rrf_scores": rrf_scores,
        "jev_rerank": jev_rerank,
        "rerank_scores": rerank_scores,
        "rerank_calls": rerank_usage.calls,
        "rerank_input_tokens": rerank_usage.input_tokens,
        "rerank_output_tokens": rerank_usage.output_tokens,
        "candidate_paths": [span.path for span in spans],
        "rg_search_ms": rg_search_ms,
    })
    return RetrieveResult(spans=spans, stats=stats)


def select_window_spans(
    query: str,
    files: list[Span],
    *,
    whole_file_lines: int = 1200,
    window_lines: int = 160,
    windows_per_file: int = 3,
) -> list[Span]:
    """Keep small files whole; find lexical 80-line anchors in larger files."""
    chunks: list[tuple[int, int, list[str]]] = []
    file_lines = [span.text.splitlines(keepends=True) for span in files]
    for file_index, lines in enumerate(file_lines):
        if len(lines) <= whole_file_lines:
            continue
        for start in range(0, len(lines), 80):
            terms = [
                term
                for term in tokenize("".join(lines[start : start + 80]))
                if term not in STOP_WORDS
            ]
            chunks.append((file_index, start, terms))

    query_terms = set(tokenize(query)) - STOP_WORDS
    scores = [len(query_terms.intersection(terms)) for _, _, terms in chunks]
    selected: list[Span] = []
    for file_index, (file, lines) in enumerate(zip(files, file_lines, strict=True)):
        if len(lines) <= whole_file_lines:
            selected.append(file)
            continue
        ranked = sorted(
            (-scores[i], start)
            for i, (chunk_file, start, _) in enumerate(chunks)
            if chunk_file == file_index
        )
        if not ranked or ranked[0][0] == 0:
            selected.append(file)
            continue
        windows: list[tuple[int, int]] = []
        for _, start in ranked:
            begin = max(0, min(start + 40 - window_lines // 2, len(lines) - window_lines))
            end = min(len(lines), begin + window_lines)
            if any(begin < other_end and end > other_begin for other_begin, other_end in windows):
                continue
            windows.append((begin, end))
            if len(windows) == windows_per_file:
                break
        for begin, end in sorted(windows):
            selected.append(
                Span(
                    path=file.path,
                    start_line=begin + 1,
                    end_line=end,
                    text="".join(lines[begin:end]),
                    score=file.score,
                )
            )
    return selected


def build_context(spans: list[Span], max_chars: int) -> tuple[str, list[Span], list[str]]:
    """Build line-numbered context in fused or reranked file order."""
    blocks: list[str] = []
    included: list[Span] = []
    dropped: list[str] = []
    used = 0
    for span in spans:
        numbered = "".join(
            f"{line_no:05d}: {line}"
            for line_no, line in enumerate(
                span.text.splitlines(keepends=True), start=span.start_line
            )
        )
        block = f"\n===== {span.path} =====\n{numbered}"
        if used + len(block) > max_chars:
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
            "retrieval_model_calls": sum(row["model_calls"] for row in completed)
            / len(completed),
            "retrieval_input_tokens": sum(row["input_tokens"] for row in completed)
            / len(completed),
            "retrieval_output_tokens": sum(row["output_tokens"] for row in completed)
            / len(completed),
            "cost_usd": sum(row["cost_usd"] for row in completed) / len(completed),
        }
        aggregate["model_calls"] = aggregate["retrieval_model_calls"]
        aggregate["input_tokens"] = aggregate["retrieval_input_tokens"]
        aggregate["output_tokens"] = aggregate["retrieval_output_tokens"]
    if scored:
        aggregate.update({
            "answer_score": sum(row["answer_score"] for row in scored) / len(scored),
            "model_calls": sum(
                row["model_calls"] + row["answer_calls"] for row in scored
            ) / len(scored),
            "input_tokens": sum(
                row["input_tokens"] + row["answer_input_tokens"] for row in scored
            ) / len(scored),
            "output_tokens": sum(
                row["output_tokens"] + row["answer_output_tokens"] for row in scored
            ) / len(scored),
            "judge_cost_usd": sum(row["judge_cost_usd"] for row in scored) / len(scored),
        })
    payload = {
        "recorded": "Fused file retrieval"
        + (" + Jev Noul rerank" if args.jev_rerank else "")
        + ("" if args.retrieval_only else " + one grounded answer call"),
        "bundle": str(args.bundle),
        "fixtures": str(args.fixtures),
        "ids": args.ids,
        "answer_model": args.answer_model,
        "judge_model": args.judge_model,
        "sources": args.sources,
        "file_k": args.file_k,
        "jev_k": args.jev_k,
        "candidate_k": args.candidate_k,
        "bm25_k": args.bm25_k,
        "fts_k": args.fts_k,
        "rg_k": args.rg_k,
        "jev_rerank": args.jev_rerank,
        "descriptor_chars": args.descriptor_chars,
        "decision_min_probability": args.decision_min_probability,
        "decision_relative_probability": args.decision_relative_probability,
        "max_context_chars": args.max_context_chars,
        "context_mode": args.context_mode,
        "whole_file_lines": args.whole_file_lines,
        "window_lines": args.window_lines,
        "windows_per_file": args.windows_per_file,
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
    parser.add_argument(
        "--sources",
        default="jev,bm25",
        help="Comma-separated candidate sources: jev,bm25,fts,rg",
    )
    parser.add_argument("--file-k", type=int, default=8, help="Final files sent to context")
    parser.add_argument("--jev-k", type=int, default=8, help="Maximum Jev tree candidates")
    parser.add_argument("--candidate-k", type=int, default=16, help="Fused pool size")
    parser.add_argument("--bm25-k", type=int, default=2, help="Unique BM25 files")
    parser.add_argument("--fts-k", type=int, default=8, help="Unique FTS files")
    parser.add_argument("--rg-k", type=int, default=8, help="Unique ripgrep files")
    parser.add_argument(
        "--jev-rerank",
        action="store_true",
        help="Rerank the fused pool with one batched Jev Noul request",
    )
    parser.add_argument("--descriptor-chars", type=int, default=3200)
    parser.add_argument("--decision-min-probability", type=float, default=0.12)
    parser.add_argument("--decision-relative-probability", type=float, default=0.2)
    parser.add_argument("--max-context-chars", type=int, default=200_000)
    parser.add_argument("--context-mode", choices=("files", "windows"), default="files")
    parser.add_argument("--whole-file-lines", type=int, default=1200)
    parser.add_argument("--window-lines", type=int, default=160)
    parser.add_argument("--windows-per-file", type=int, default=3)
    parser.add_argument("--wall-budget", type=float, default=180.0)
    parser.add_argument("--call-budget", type=int, default=32)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--ids", help="Comma-separated fixture IDs, for rerunning selected questions"
    )
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    sources = tuple(dict.fromkeys(part.strip() for part in args.sources.split(",") if part.strip()))
    unknown_sources = set(sources) - {"jev", "bm25", "fts", "rg"}
    if not sources or unknown_sources:
        parser.error("sources must contain one or more of: jev,bm25,fts,rg")
    args.sources = ",".join(sources)
    if min(
        args.file_k,
        args.jev_k,
        args.candidate_k,
        args.max_context_chars,
        args.call_budget,
    ) < 1:
        parser.error("file-k, jev-k, candidate-k, context, and call budget must be positive")
    if min(args.bm25_k, args.fts_k, args.rg_k) < 0:
        parser.error("bm25-k, fts-k, and rg-k cannot be negative")
    if min(
        args.whole_file_lines, args.window_lines,
        args.windows_per_file, args.descriptor_chars,
    ) < 1:
        parser.error("window and descriptor sizes must be positive")
    if args.candidate_k < args.file_k:
        parser.error("candidate-k must be at least file-k")
    if not 0 <= args.decision_min_probability <= 1:
        parser.error("decision-min-probability must be between 0 and 1")
    if not 0 <= args.decision_relative_probability <= 1:
        parser.error("decision-relative-probability must be between 0 and 1")
    if not args.retrieval_only and not args.gold_answers.is_file():
        parser.error(f"gold answers not found: {args.gold_answers}")

    fixtures = load_fixtures(args.fixtures)
    if args.ids:
        selected_ids = {part.strip() for part in args.ids.split(",") if part.strip()}
        unknown_ids = selected_ids - {fixture["id"] for fixture in fixtures}
        if not selected_ids or unknown_ids:
            parser.error(f"unknown or empty fixture IDs: {', '.join(sorted(unknown_ids))}")
        fixtures = [fixture for fixture in fixtures if fixture["id"] in selected_ids]
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
                sources=sources,
                file_k=args.file_k,
                jev_k=args.jev_k,
                candidate_k=args.candidate_k,
                bm25_k=args.bm25_k,
                fts_k=args.fts_k,
                rg_k=args.rg_k,
                jev_rerank=args.jev_rerank,
                descriptor_chars=args.descriptor_chars,
                wall_budget=args.wall_budget,
                call_budget=args.call_budget,
                min_probability=args.decision_min_probability,
                relative_probability=args.decision_relative_probability,
            )
            selected = (
                select_window_spans(
                    query,
                    candidates.spans,
                    whole_file_lines=args.whole_file_lines,
                    window_lines=args.window_lines,
                    windows_per_file=args.windows_per_file,
                )
                if args.context_mode == "windows"
                else candidates.spans
            )
            context, included, dropped = build_context(selected, args.max_context_chars)
            retrieval_ms = (time.perf_counter() - retrieval_started) * 1000
            metrics = build_metrics(candidates.stats, included, gold, latency_ms=retrieval_ms)
            included_paths = list(dict.fromkeys(span.path for span in included))

            if args.retrieval_only:
                row = {
                    "id": qid,
                    "query": query,
                    "candidate_paths": included_paths,
                    "dropped_paths": dropped,
                    "candidate_files": len(included_paths),
                    "context_chars": len(context),
                    **metrics.to_bench_fields(),
                }
                results.append(row)
                print(
                    f"{qid} file_rec={metrics.file_recall:.2f} "
                    f"coverage={metrics.span_recall:.2f} files={len(included_paths)} "
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
                ],
                temperature=None,
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
                ],
                temperature=None,
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
                "candidate_paths": included_paths,
                "dropped_paths": dropped,
                "candidate_files": len(included_paths),
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
                f"coverage={metrics.span_recall:.2f} files={len(included_paths)} "
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
