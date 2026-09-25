"""Test find-and-expand on verified ARB full files at fixed token budgets."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from collections import Counter
from itertools import zip_longest
from math import log
from pathlib import Path

import tiktoken
from rank_bm25 import BM25Okapi

from evals import RESULTS
from evals.arb_full_source import DEFAULT_SOURCE_ROOT, _candidate_paths
from evals.arb_trace_ablation import DEFAULT_RELEASE
from evals.baselines.bm25 import tokenize
from evals.baselines.ripgrep import STOP_WORDS
from evals.hybrid import select_window_spans
from evals.metrics import GoldSpan, span_set_metrics
from fastindex.types import Span

VARIANTS = (
    ("whole", 0), ("mixed", 80), ("mixed", 160), ("mixed", 320),
    ("all", 80), ("all", 160), ("all", 320), ("all", 640),
    ("merge5", 20), ("merge10", 20), ("merge20", 20),
    ("merge40", 20), ("merge80", 20), ("merge80", 40), ("merge80", 80),
    ("merge4", 200),
    ("merge80skip", 20), ("merge80skip", 40),
)


def _merged_match_spans(
    query: str, files: list[Span], *, radius: int, anchors_per_file: int,
    skip_unmatched: bool = False, hit_score: str = "overlap",
) -> list[Span]:
    """Expand top lexical hit lines, then coalesce touching intervals per file."""
    if hit_score not in {"overlap", "idf"}:
        raise ValueError(f"Unknown hit score: {hit_score}")
    terms = {term for term in tokenize(query) if term not in STOP_WORDS and len(term) > 2}
    selected: list[Span] = []
    for file in files:
        lines = file.text.splitlines(keepends=True)
        matches = [terms.intersection(tokenize(line)) for line in lines]
        if hit_score == "idf":
            frequency = Counter(term for matched in matches for term in matched)
            weights = {term: log((len(lines) + 1) / (count + 1))
                       for term, count in frequency.items()}
        else:
            weights = {term: 1.0 for term in terms}
        ranked = sorted(
            (-sum(weights[term] for term in matched), index)
            for index, matched in enumerate(matches) if matched
        )[:anchors_per_file]
        if not ranked:
            if not skip_unmatched:
                selected.append(file)
            continue
        intervals = sorted(
            (max(0, index - radius), min(len(lines), index + radius + 1), -score)
            for score, index in ranked
        )
        merged: list[tuple[int, int, float]] = []
        for begin, end, score in intervals:
            if merged and begin <= merged[-1][1]:
                old_begin, old_end, old_score = merged[-1]
                merged[-1] = (old_begin, max(old_end, end), max(old_score, score))
            else:
                merged.append((begin, end, score))
        for begin, end, _ in sorted(merged, key=lambda item: (-item[2], item[0])):
            selected.append(Span(
                file.path, begin + 1, end, "".join(lines[begin:end]), file.score
            ))
    return selected


def _pack(
    spans: list[Span], budget: int, encoder: tiktoken.Encoding
) -> tuple[list[Span], int, int]:
    included: list[Span] = []
    tokens = chars = 0
    for span in spans:
        block = f"\n===== {span.path} =====\n" + "".join(
            f"{number:05d}: {line}" for number, line in enumerate(
                span.text.splitlines(keepends=True), start=span.start_line
            )
        )
        block_tokens = len(encoder.encode(block, disallowed_special=()))
        if tokens + block_tokens > budget:
            continue
        included.append(span)
        tokens += block_tokens
        chars += len(block)
    return included, tokens, chars


def _round_robin(spans: list[Span]) -> list[Span]:
    """Offer one interval per file before second intervals, retaining file rank."""
    by_file: dict[str, list[Span]] = {}
    for span in spans:
        by_file.setdefault(span.path, []).append(span)
    return [span for row in zip_longest(*by_file.values())
            for span in row if span is not None]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, default=DEFAULT_RELEASE)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--fusion", "--rankings", dest="rankings", type=Path,
                        help="Use frozen candidate rankings instead of oracle gold files")
    parser.add_argument("--candidate-source", choices=("rrf", "tree", "literal", "bm25"),
                        default="rrf")
    parser.add_argument("--candidate-k", type=int, default=8)
    parser.add_argument("--file-order", choices=("frozen", "bm25"), default="frozen")
    parser.add_argument("--hit-score", choices=("overlap", "idf"), default="overlap")
    parser.add_argument("--pack-order", choices=("sequential", "round-robin"),
                        default="sequential")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.candidate_k < 1 or args.candidate_k > 16:
        parser.error("--candidate-k must be between 1 and 16")
    if args.file_order != "frozen" and not args.rankings:
        parser.error("--file-order requires --rankings")
    if args.candidate_source != "rrf" and not args.rankings:
        parser.error("--candidate-source requires --rankings")
    if args.manifest is None:
        name = (f"arb-full-source-{args.candidate_source}{args.candidate_k}.json"
                if args.rankings
                else "arb-full-source-gold.json")
        args.manifest = RESULTS / name
    if args.out is None:
        name = (f"arb-candidate-span-{args.candidate_source}{args.candidate_k}.json"
                if args.rankings
                else "arb-oracle-span-ablation.json")
        if args.file_order != "frozen":
            name = name.removesuffix(".json") + f"-{args.file_order}.json"
        if args.hit_score != "overlap":
            name = name.removesuffix(".json") + f"-{args.hit_score}.json"
        if args.pack_order != "sequential":
            name = name.removesuffix(".json") + f"-{args.pack_order}.json"
        args.out = RESULTS / name
    samples = [json.loads(line) for line in (
        args.release / "benchmark/v2_trace2code/samples.jsonl"
    ).read_text(encoding="utf-8").splitlines()]
    manifest = json.loads(args.manifest.read_text())
    expected_selection = (f"{args.candidate_source}@{args.candidate_k}"
                          if args.rankings else "gold")
    if manifest.get("selection", "gold") != expected_selection:
        raise ValueError(f"Manifest selection is not {expected_selection}")
    rankings = None
    if args.rankings:
        rankings = {
            row["id"]: _candidate_paths(row, args.candidate_source)[:args.candidate_k]
            for row in json.loads(args.rankings.read_text(encoding="utf-8"))["results"]
        }
        if set(rankings) != {sample["id"] for sample in samples}:
            raise ValueError("Fusion ranking and release have different query IDs")
    verified = {
        (row["repo"], row["commit"], row["path"]): row
        for row in manifest["results"] if row["status"] == "verified"
    }
    needed = {
        (sample["repo"], sample["base_commit"], path)
        for sample in samples
        for path in (rankings[sample["id"]] if rankings is not None
                     else {g["path"] for g in sample["gold_spans"]})
    }
    if missing := needed - verified.keys():
        raise ValueError(f"Missing {len(missing)} verified full selected files")
    contents = {}
    for repo, commit, path in needed:
        source = args.source_root / repo.replace("/", "__") / commit / path
        payload = source.read_bytes()
        if hashlib.sha256(payload).hexdigest() != verified[repo, commit, path]["sha256"]:
            raise ValueError(f"Source file changed after verification: {source}")
        contents[repo, commit, path] = payload.decode("utf-8")

    encoder = tiktoken.get_encoding("cl100k_base")
    rows: list[dict] = []
    for sample in samples:
        qid = sample["id"]
        repo, commit = sample["repo"], sample["base_commit"]
        gold = [GoldSpan(g["path"], g["start_line"], g["end_line"])
                for g in sample["gold_spans"]]
        gold_paths = {g.path for g in gold}
        paths = (rankings[qid] if rankings is not None else sorted(gold_paths))
        file_recall = len(gold_paths.intersection(paths)) / len(gold_paths)
        order_started = time.perf_counter()
        if args.file_order == "bm25":
            documents = [tokenize(path + "\n" + contents[repo, commit, path])
                         for path in paths]
            terms = sorted({term for term in tokenize(sample["query"]["failure_excerpt"])
                            if term not in STOP_WORDS and len(term) > 2})
            scores = BM25Okapi(documents).get_scores(terms)
            paths = [paths[index] for index in sorted(
                range(len(paths)), key=lambda index: (-scores[index], index)
            )]
        order_ms = (time.perf_counter() - order_started) * 1000
        whole = [Span(path, 1, len(text.splitlines()), text)
                 for path in paths if (text := contents[repo, commit, path])]
        for mode, width in VARIANTS:
            started = time.perf_counter()
            if mode == "whole":
                selected = whole
            elif mode.startswith("merge"):
                selected = _merged_match_spans(
                    sample["query"]["failure_excerpt"], whole,
                    radius=width,
                    anchors_per_file=int(mode.removeprefix("merge").removesuffix("skip")),
                    skip_unmatched=mode.endswith("skip"),
                    hit_score=args.hit_score,
                )
            else:
                selected = select_window_spans(
                    sample["query"]["failure_excerpt"], whole,
                    whole_file_lines=1200 if mode == "mixed" else 0,
                    window_lines=width, windows_per_file=3,
                )
            if args.pack_order == "round-robin":
                selected = _round_robin(selected)
            select_ms = (time.perf_counter() - started) * 1000
            for budget in (8_000, 16_000, 32_000):
                started = time.perf_counter()
                included, tokens, chars = _pack(selected, budget, encoder)
                sr, _, _, lr, lp, _ = span_set_metrics(included, gold)
                rows.append({
                    "id": qid, "repo": repo, "mode": mode, "window_lines": width,
                    "hit_score": args.hit_score,
                    "budget_tokens": budget, "span_recall": sr,
                    "line_recall": lr, "line_precision": lp,
                    "candidate_file_recall": file_recall,
                    "candidate_file_count": len(paths),
                    "file_order_ms": order_ms,
                    "context_tokens": tokens, "context_chars": chars,
                    "selected_spans": len(included),
                    "select_ms": select_ms,
                    "pack_ms": (time.perf_counter() - started) * 1000,
                })
        print(f"{len(rows)//(len(VARIANTS)*3)}/{len(samples)} {repo} {qid}", flush=True)

    summary = {}
    for mode, width in VARIANTS:
        for budget in (8_000, 16_000, 32_000):
            selected = [row for row in rows if row["mode"] == mode
                        and row["window_lines"] == width and row["budget_tokens"] == budget]
            summary[f"{mode}/{width}/{budget}"] = {
                "span_recall": statistics.mean(row["span_recall"] for row in selected),
                "complete_spans": sum(row["span_recall"] == 1 for row in selected),
                "line_recall": statistics.mean(row["line_recall"] for row in selected),
                "complete_lines": sum(row["line_recall"] == 1 for row in selected),
                "line_precision": statistics.mean(row["line_precision"] for row in selected),
                "candidate_file_recall": statistics.mean(
                    row["candidate_file_recall"] for row in selected
                ),
                "complete_candidate_files": sum(
                    row["candidate_file_recall"] == 1 for row in selected
                ),
                "file_order_ms": statistics.mean(row["file_order_ms"] for row in selected),
                "context_tokens": statistics.mean(row["context_tokens"] for row in selected),
                "select_ms": statistics.mean(row["select_ms"] for row in selected),
                "pack_ms": statistics.mean(row["pack_ms"] for row in selected),
            }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "scope": (f"frozen {args.candidate_source} top-{args.candidate_k} candidate files"
                  if args.rankings else "oracle gold files; no file discovery"),
        "fusion": str(args.rankings) if args.rankings and args.candidate_source == "rrf"
                  else None,
        "rankings": str(args.rankings) if args.rankings else None,
        "candidate_source": args.candidate_source if args.rankings else "oracle",
        "file_order": args.file_order,
        "hit_score": args.hit_score,
        "pack_order": args.pack_order,
        "encoding": "cl100k_base", "manifest": str(args.manifest),
        "summary": summary, "results": rows,
    }, indent=2))
    for key, result in summary.items():
        print(f"{key}: span={result['span_recall']:.3f} "
              f"complete={result['complete_spans']}/{len(samples)} "
              f"complete_lines={result['complete_lines']}/{len(samples)} "
              f"tokens={result['context_tokens']:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
