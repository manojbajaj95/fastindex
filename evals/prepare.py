"""External benchmark registry + opt-in download/prepare (local fixtures stay default).

Usage (no network unless download/prepare):
  uv run python -m evals.prepare list
  uv run python -m evals.prepare peers
  uv run python -m evals.prepare download multihop_rag --limit 50
  uv run python -m evals.prepare prepare multihop_rag --limit 50

Downloads land under evals/fixtures/external/ (gitignored). Conversion to OKF bundle +
gold JSONL is opt-in via `prepare`. Default lab work stays on evals/fixtures/sample-bundle.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from evals import FIXTURES, REPO_ROOT

ROOT = REPO_ROOT
EXTERNAL = FIXTURES / "external"
CACHE = EXTERNAL / ".cache"
CODEBASE_QA_BUNDLE = EXTERNAL / "codebase-qa-flask"
DEFAULT_CODEBASE_QA_SOURCE = (
    REPO_ROOT.parent
    / "agent-learning-bench"
    / "tasks"
    / "codebase-qa"
    / "environment"
    / "data"
    / "repo"
)


@dataclass(frozen=True)
class BenchSource:
    """Public dataset we may later convert into OKF + gold JSONL."""

    id: str
    title: str
    tracks: tuple[str, ...]
    gold: str
    size: str
    urls: dict[str, str]
    notes: str
    # How to fetch (implemented later / when user runs download)
    fetch: str  # hf | github | manual
    hf_id: str | None = None
    hf_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class PeerEval:
    """What a related system actually reports in papers / docs."""

    name: str
    paper_or_docs: str
    benchmarks: tuple[str, ...]
    metrics: str
    relevance: str  # how it relates to this lab
    urls: dict[str, str] = field(default_factory=dict)


BENCHMARKS: dict[str, BenchSource] = {
    "multihop_rag": BenchSource(
        id="multihop_rag",
        title="MultiHop-RAG",
        tracks=("OKF wiki", "multi_hop"),
        gold="Evidence docs/facts across 2–4 articles (retrieval-first)",
        size="2,556 queries · ~609 corpus docs",
        urls={
            "paper": "https://arxiv.org/abs/2401.15391",
            "github": "https://github.com/yixuantt/MultiHop-RAG",
            "hf": "https://huggingface.co/datasets/yixuantt/MultiHopRAG",
        },
        notes="Closest public twin of evals/fixtures/queries/multi_hop.jsonl.",
        fetch="hf",
        hf_id="yixuantt/MultiHopRAG",
        hf_files=("MultiHopRAG.json", "corpus.json"),
    ),
    "musique": BenchSource(
        id="musique",
        title="MuSiQue",
        tracks=("multi_hop",),
        gold="Supporting paragraphs + hop decomposition (hard for shortcuts)",
        size="~25k composed 2–4 hop questions",
        urls={
            "paper": "https://aclanthology.org/2022.tacl-1.31/",
            "hf": "https://huggingface.co/datasets/dgslibisey/MuSiQue",
        },
        notes="Best academic bar that flat one-shot retrieval should miss hops.",
        fetch="hf",
        hf_id="dgslibisey/MuSiQue",
    ),
    "hotpotqa": BenchSource(
        id="hotpotqa",
        title="HotpotQA",
        tracks=("multi_hop", "ir_baseline"),
        gold="Supporting sentences + answer EM/F1",
        size="113k Qs (use distractor or fullwiki validation)",
        urls={
            "paper": "https://arxiv.org/abs/1809.09600",
            "site": "https://hotpotqa.github.io/",
            "hf": "https://huggingface.co/datasets/hotpot_qa",
        },
        notes="Industry cite; many items cheatable — prefer MuSiQue for hardness.",
        fetch="hf",
        hf_id="hotpot_qa",
    ),
    "enterprise_rag": BenchSource(
        id="enterprise_rag",
        title="EnterpriseRAG-Bench",
        tracks=("OKF wiki", "multi_hop"),
        gold="expected_doc_ids + gold answers (Confluence/wiki-shaped)",
        size="~500k enterprise docs · 500 questions",
        urls={
            "paper": "https://arxiv.org/abs/2605.05253",
            "github": "https://github.com/onyx-dot-app/EnterpriseRAG-Bench",
            "hf": "https://huggingface.co/datasets/onyx-dot-app/EnterpriseRAG-Bench",
        },
        notes="Phase-2 corpus analogue; prefer Confluence slice when preparing.",
        fetch="hf",
        hf_id="onyx-dot-app/EnterpriseRAG-Bench",
        hf_files=("data/questions/test.parquet", "data/documents/test.parquet"),
    ),
    "financebench": BenchSource(
        id="financebench",
        title="FinanceBench",
        tracks=("long_doc",),
        gold="Evidence text + page number (near span gold)",
        size="10k Qs (150 open-source with full page text)",
        urls={
            "paper": "https://arxiv.org/abs/2311.11944",
            "github": "https://github.com/patronus-ai/financebench",
        },
        notes="Long-document evaluation with evidence text and page-level provenance.",
        fetch="github",
    ),
    "contextbench": BenchSource(
        id="contextbench",
        title="ContextBench",
        tracks=("code",),
        gold="Human gold contexts at file / block / line (interval F1)",
        size="1,136 tasks · 66 repos · 8 languages",
        urls={
            "paper": "https://arxiv.org/abs/2602.05892",
            "leaderboard": "https://contextbench.github.io/",
            "github": "https://github.com/EuniAI/ContextBench",
            "hf": "https://huggingface.co/datasets/Contextbench/ContextBench",
        },
        notes="Later codebase track (deferred).",
        fetch="hf",
        hf_id="Contextbench/ContextBench",
    ),
}


PEERS: list[PeerEval] = [
    PeerEval(
        name="Microsoft FastContext",
        paper_or_docs="FastContext: Training Efficient Repository Explorer (arXiv 2606.14066)",
        benchmarks=(
            "SWE-bench Multilingual",
            "SWE-bench Pro",
            "SWE-QA",
            "Standalone exploration: patch-relevant file/symbol citation quality",
        ),
        metrics=(
            "End-to-end resolve rate + main-agent tokens/turns; "
            "citation quality for file+line evidence"
        ),
        relevance=(
            "Code-track peer: returns path + line ranges (close to our Span unit). "
            "Agent explorer (Read/Glob/Grep), not OKF wiki RAG. "
            "Pairs with ContextBench later track."
        ),
        urls={
            "paper": "https://arxiv.org/abs/2606.14066",
            "github": "https://github.com/microsoft/fastcontext",
            "hf_rl": "https://huggingface.co/microsoft/FastContext-1.0-4B-RL",
        },
    ),
    PeerEval(
        name="Microsoft GraphRAG",
        paper_or_docs="From Local to Global (arXiv 2404.16130); follow-on HotpotQA work",
        benchmarks=(
            "Kevin Scott / Behind the Tech podcasts (~1M tokens, 125 global Qs)",
            "MultiHop-RAG news corpus (global summarization setting)",
            "HotpotQA (later MS benchmarking datasets / TREX comparisons)",
        ),
        metrics="LLM-as-judge win rates (comprehensiveness, diversity, empowerment); not span F1",
        relevance="Global/sensemaking ≠ our span-retrieval C. MultiHop-RAG overlap is useful.",
        urls={
            "paper": "https://arxiv.org/abs/2404.16130",
            "datasets": "https://github.com/microsoft/graphrag-benchmarking-datasets",
            "followon": "https://arxiv.org/abs/2503.02922",
        },
    ),
    PeerEval(
        name="Microsoft neural indexes (NCI, MEVI)",
        paper_or_docs=(
            "NCI NeurIPS 2022; MEVI NeurIPS 2023. "
            "Unrelated to FastContext / this lab’s name."
        ),
        benchmarks=(
            "NCI: NQ320k, TriviaQA",
            "MEVI: MSMARCO Passage, Natural Questions",
        ),
        metrics="Recall@k / R-Precision / MRR (classic IR)",
        relevance="Different problem (web/doc id generation). Name collision only with this lab.",
        urls={
            "nci": "https://arxiv.org/abs/2206.02743",
            "mevi": "https://www.microsoft.com/en-us/research/publication/model-enhanced-vector-index/",
        },
    ),
    PeerEval(
        name="Cognee",
        paper_or_docs=(
            "Eval framework docs + HotpotQA tuning paper (arXiv 2505.24478); "
            "BEAM memory bench"
        ),
        benchmarks=("HotpotQA", "MuSiQue", "2WikiMultiHop", "BEAM (agent memory)"),
        metrics="EM, F1, DeepEval correctness; BEAM rubric scores",
        relevance=(
            "Our local KG baseline’s own eval suite — "
            "align later multi-hop public runs here."
        ),
        urls={
            "docs": "https://docs.cognee.ai/integrations/eval-framework",
            "paper": "https://arxiv.org/abs/2505.24478",
            "github": "https://github.com/topoteretes/cognee",
        },
    ),
    PeerEval(
        name="ContextBench (code agents)",
        paper_or_docs="arXiv 2602.05892",
        benchmarks=("1,136 issue tasks with human gold contexts",),
        metrics="Context F1 / line-block-file overlap + efficiency/cost",
        relevance="Deferred codebase track; closest span-gold public code bench.",
        urls={
            "paper": "https://arxiv.org/abs/2602.05892",
            "leaderboard": "https://contextbench.github.io/",
        },
    ),
]


def _ensure_bench_extra() -> None:
    try:
        import huggingface_hub  # noqa: F401
    except ImportError as e:
        raise SystemExit(
            "Download requires optional deps. Run: uv sync --extra evals\n" + str(e)
        ) from e


def cmd_list(_: argparse.Namespace) -> int:
    rows = []
    for b in BENCHMARKS.values():
        rows.append(
            {
                "id": b.id,
                "title": b.title,
                "tracks": list(b.tracks),
                "gold": b.gold,
                "size": b.size,
                "fetch": b.fetch,
                "urls": b.urls,
                "notes": b.notes,
            }
        )
    print(
        json.dumps(
            {"default_corpus": "evals/fixtures/sample-bundle", "benchmarks": rows},
            indent=2,
        )
    )
    return 0


def cmd_peers(_: argparse.Namespace) -> int:
    print(json.dumps([asdict(p) for p in PEERS], indent=2))
    return 0


def cmd_stage_codebase_qa(args: argparse.Namespace) -> int:
    """Copy the frozen Codebase QA repository into local ignored fixtures."""
    source = args.source.resolve()
    if not source.is_dir():
        print(f"Codebase QA source not found: {source}", file=sys.stderr)
        return 2
    if CODEBASE_QA_BUNDLE.exists():
        print(
            f"Destination already exists: {CODEBASE_QA_BUNDLE}\n"
            "Remove it explicitly before restaging; existing prepared indexes are preserved.",
            file=sys.stderr,
        )
        return 2
    CODEBASE_QA_BUNDLE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        CODEBASE_QA_BUNDLE,
        symlinks=True,
        ignore=shutil.ignore_patterns(".git", ".fastindex", "__pycache__", "index.md"),
    )
    print(json.dumps({"source": str(source), "bundle": str(CODEBASE_QA_BUNDLE)}, indent=2))
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    bench = BENCHMARKS.get(args.name)
    if not bench:
        print(f"Unknown benchmark: {args.name}", file=sys.stderr)
        print("Known:", ", ".join(BENCHMARKS), file=sys.stderr)
        return 2

    dest = CACHE / bench.id
    if args.dry_run:
        print(
            json.dumps(
                {
                    "action": "download",
                    "dry_run": True,
                    "id": bench.id,
                    "dest": str(dest),
                    "fetch": bench.fetch,
                    "hf_id": bench.hf_id,
                    "hf_files": list(bench.hf_files),
                    "limit": args.limit,
                    "urls": bench.urls,
                },
                indent=2,
            )
        )
        return 0

    _ensure_bench_extra()
    dest.mkdir(parents=True, exist_ok=True)
    meta_path = dest / "download_meta.json"

    if bench.fetch == "hf":
        from huggingface_hub import hf_hub_download

        assert bench.hf_id
        saved: list[str] = []
        if bench.hf_files:
            for rel in bench.hf_files:
                path = hf_hub_download(
                    repo_id=bench.hf_id,
                    filename=rel,
                    repo_type="dataset",
                    local_dir=str(dest),
                )
                saved.append(path)
                print(f"downloaded {rel} → {path}", file=sys.stderr)
        else:
            # Whole-dataset snapshot is large; require explicit --full
            if not args.full:
                print(
                    f"{bench.id}: no default file list. Pass --full to snapshot the HF dataset, "
                    "or add files in the registry.",
                    file=sys.stderr,
                )
                return 2
            from huggingface_hub import snapshot_download

            path = snapshot_download(
                repo_id=bench.hf_id,
                repo_type="dataset",
                local_dir=str(dest / "snapshot"),
            )
            saved.append(path)
            print(f"snapshot → {path}", file=sys.stderr)
    elif bench.fetch == "github" and bench.id == "financebench":
        import httpx

        url = (
            "https://raw.githubusercontent.com/patronus-ai/financebench/"
            "main/data/financebench_open_source.jsonl"
        )
        out = dest / "financebench_open_source.jsonl"
        r = httpx.get(url, follow_redirects=True, timeout=120.0)
        r.raise_for_status()
        text = r.text
        if args.limit and args.limit > 0:
            lines = [ln for ln in text.splitlines() if ln.strip()][: args.limit]
            out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            out.write_text(text, encoding="utf-8")
        saved = [str(out)]
        print(f"downloaded → {out}", file=sys.stderr)
    else:
        print(f"No download handler for fetch={bench.fetch} id={bench.id}", file=sys.stderr)
        return 2

    meta: dict[str, Any] = {
        "id": bench.id,
        "hf_id": bench.hf_id,
        "files": saved,
        "limit": args.limit,
        "urls": bench.urls,
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "meta": str(meta_path), **meta}, indent=2))
    return 0


def cmd_prepare(args: argparse.Namespace) -> int:
    """Convert a downloaded cache into OKF bundle + queries JSONL (not implemented yet)."""
    bench = BENCHMARKS.get(args.name)
    if not bench:
        print(f"Unknown benchmark: {args.name}", file=sys.stderr)
        return 2
    out = EXTERNAL / bench.id
    if args.dry_run:
        print(
            json.dumps(
                {
                    "action": "prepare",
                    "dry_run": True,
                    "id": bench.id,
                    "would_write": {
                        "bundle": str(out / "bundle"),
                        "queries": str(out / "queries.jsonl"),
                    },
                    "status": "converter_not_implemented",
                    "hint": "Stay on evals/fixtures/sample-bundle until converters land.",
                },
                indent=2,
            )
        )
        return 0
    print(
        f"prepare/{bench.id}: converter not implemented yet. "
        "Use local evals/fixtures/sample-bundle + evals/bench.py for now.\n"
        f"Download cache (when ready): uv run python -m evals.prepare download {bench.id}",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List / download / prepare external retrieval benchmarks (opt-in)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List target public benchmarks (no network)")
    p_list.set_defaults(func=cmd_list)

    p_peers = sub.add_parser("peers", help="Show evaluation methods used by related systems")
    p_peers.set_defaults(func=cmd_peers)

    p_codebase = sub.add_parser(
        "stage-codebase-qa",
        help="Copy the sibling agent-learning-bench Flask corpus into ignored fixtures",
    )
    p_codebase.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_CODEBASE_QA_SOURCE,
        help=f"Frozen Flask repository (default: {DEFAULT_CODEBASE_QA_SOURCE})",
    )
    p_codebase.set_defaults(func=cmd_stage_codebase_qa)

    p_dl = sub.add_parser(
        "download",
        help="Download raw dataset into evals/fixtures/external/.cache/",
    )
    p_dl.add_argument("name", help="Benchmark id from `list`")
    p_dl.add_argument("--limit", type=int, default=50, help="Cap rows where applicable")
    p_dl.add_argument(
        "--full",
        action="store_true",
        help="Allow full HF snapshot when no file list is configured",
    )
    p_dl.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan only; do not download",
    )
    p_dl.set_defaults(func=cmd_download)

    p_prep = sub.add_parser(
        "prepare",
        help="Convert downloaded cache → OKF bundle + gold JSONL (stub until needed)",
    )
    p_prep.add_argument("name")
    p_prep.add_argument("--limit", type=int, default=50)
    p_prep.add_argument("--dry-run", action="store_true")
    p_prep.set_defaults(func=cmd_prepare)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
