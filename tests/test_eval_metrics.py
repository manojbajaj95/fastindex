import json
from argparse import Namespace
from pathlib import Path

import pytest

from evals import FIXTURES, SAMPLE_BUNDLE
from evals.baselines.pi_agent import _parse_pi_output
from evals.bench import load_fixtures, parse_cutoffs, validate_fixtures
from evals.hybrid import _json_object, _rrf, _write_results, build_context
from evals.index import build_index, is_fresh, load_bm25_docs
from evals.metrics import GoldSpan, file_set_metrics
from fastindex.types import Span


def test_file_metrics_deduplicate_paths() -> None:
    predicted = [
        Span("a.py", 1, 2, ""),
        Span("a.py", 10, 12, ""),
        Span("extra.py", 1, 1, ""),
    ]
    gold = [GoldSpan("a.py", 2, 3), GoldSpan("b.py", 4, 5)]

    assert file_set_metrics(predicted, gold) == (0.5, 0.5, 0.5)


def test_codebase_qa_fixture_has_all_48_questions() -> None:
    rows = load_fixtures(FIXTURES / "queries" / "codebase_qa.jsonl")

    assert [row["id"] for row in rows] == [f"q{number:03}" for number in range(1, 49)]
    assert all(row["query"] and row["gold"] for row in rows)


@pytest.mark.parametrize("name", ["sample.jsonl", "multi_hop.jsonl"])
def test_bundled_fixtures_match_sample_corpus(name: str) -> None:
    validate_fixtures(load_fixtures(FIXTURES / "queries" / name), SAMPLE_BUNDLE)


def test_fixture_validation_checks_ids_paths_and_ranges(tmp_path: Path) -> None:
    (tmp_path / "source.py").write_text("one\ntwo\n", encoding="utf-8")
    fixture = {
        "id": "q001",
        "query": "Where is two?",
        "gold": [{"path": "source.py", "start_line": 2, "end_line": 2}],
    }
    validate_fixtures([fixture], tmp_path)

    fixture["gold"][0]["end_line"] = 3
    with pytest.raises(ValueError, match="invalid range"):
        validate_fixtures([fixture], tmp_path)


def test_cutoffs_are_positive_sorted_and_deduplicated() -> None:
    assert parse_cutoffs("8,2,4,2", top_k=8) == [2, 4, 8]

    with pytest.raises(ValueError, match="positive"):
        parse_cutoffs("0,2", top_k=8)


def test_eval_index_includes_generic_source_files(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text("# Index\n", encoding="utf-8")
    source = tmp_path / "source.py"
    source.write_text("def answer():\n    return 42\n", encoding="utf-8")

    build_index(tmp_path, with_embeddings=False)

    docs = load_bm25_docs(tmp_path)
    assert docs is not None
    assert {doc["path"] for doc in docs} == {"source.py"}
    assert is_fresh(tmp_path)

    source.write_text("def answer():\n    return 43\n", encoding="utf-8")
    assert not is_fresh(tmp_path)


def test_pi_output_parser_reads_ranked_spans_and_usage(tmp_path: Path) -> None:
    (tmp_path / "answer.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    events = [
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": '{"spans":[{"path":"answer.py","start_line":2,"end_line":3}]}',
                    }
                ],
                "usage": {
                    "input": 10,
                    "cacheRead": 4,
                    "output": 2,
                    "totalTokens": 16,
                    "cost": {"total": 0.01},
                },
            },
        },
        {"type": "tool_execution_start"},
    ]

    spans, usage = _parse_pi_output("\n".join(json.dumps(event) for event in events), tmp_path, 8)

    assert [(span.path, span.start_line, span.end_line, span.text) for span in spans] == [
        ("answer.py", 2, 3, "two\nthree")
    ]
    assert usage == {"calls": 1, "input": 14, "output": 2, "cost": 0.01, "tools": 1}


def test_pi_output_parser_ignores_invalid_spans(tmp_path: Path) -> None:
    (tmp_path / "answer.py").write_text("one\ntwo\n", encoding="utf-8")
    message = {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "spans": [
                                {"path": "../outside.py", "start_line": 1, "end_line": 1},
                                {"path": "answer.py", "start_line": "bad", "end_line": 2},
                                {"path": "answer.py", "start_line": 1, "end_line": 1},
                            ]
                        }
                    ),
                }
            ],
            "usage": {},
        },
    }

    spans, _ = _parse_pi_output(json.dumps(message), tmp_path, 8)

    assert [(span.path, span.start_line, span.end_line) for span in spans] == [
        ("answer.py", 1, 1)
    ]


def test_hybrid_context_preserves_ranked_whole_files_with_a_budget() -> None:
    spans = [
        Span("first.py", 1, 2, "one\ntwo\n"),
        Span("second.py", 1, 1, "three\n"),
    ]

    context, included, dropped = build_context(spans, max_chars=45)

    assert "===== first.py =====" in context
    assert "00002: two" in context
    assert [span.path for span in included] == ["first.py"]
    assert dropped == ["second.py"]
    assert _json_object('prefix {"score": 5, "reasoning": "ok"}') == {
        "score": 5,
        "reasoning": "ok",
    }


def test_hybrid_rrf_rewards_files_found_by_multiple_sources() -> None:
    ordered, scores = _rrf({
        "jev": ["tree.py", "shared.py"],
        "bm25": ["lexical.py", "shared.py"],
    })

    assert ordered[0] == "shared.py"
    assert scores["shared.py"] > scores["tree.py"]


def test_hybrid_aggregate_includes_answer_usage(tmp_path: Path) -> None:
    out = tmp_path / "result.json"
    args = Namespace(
        retrieval_only=False,
        jev_rerank=True,
        bundle=tmp_path,
        fixtures=tmp_path / "queries.jsonl",
        answer_model="answer",
        judge_model="judge",
        sources="jev,bm25",
        file_k=4,
        jev_k=8,
        candidate_k=16,
        bm25_k=8,
        fts_k=0,
        decision_min_probability=0.04,
        decision_relative_probability=0.05,
        max_context_chars=200_000,
    )
    row = {
        "file_recall": 1.0,
        "span_recall": 1.0,
        "candidate_files": 4,
        "context_chars": 100,
        "latency_ms": 20,
        "model_calls": 2,
        "input_tokens": 100,
        "output_tokens": 10,
        "cost_usd": 0.01,
        "answer_score": 1.0,
        "answer_calls": 1,
        "answer_input_tokens": 50,
        "answer_output_tokens": 5,
        "judge_cost_usd": 0.001,
    }

    _write_results(out, args, [row])

    aggregate = json.loads(out.read_text())["aggregate"]
    assert aggregate["retrieval_model_calls"] == 2
    assert aggregate["model_calls"] == 3
    assert aggregate["input_tokens"] == 150
    assert aggregate["output_tokens"] == 15
