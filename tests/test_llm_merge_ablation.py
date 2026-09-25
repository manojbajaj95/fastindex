import json

from evals.llm_merge_ablation import _candidate_paths, _model_ranges
from fastindex.types import Span


def test_model_ranges_merge_only_cited_lines_and_count_cache_writes() -> None:
    file = Span("answer.py", 1, 6, "a\nb\nc\nd\ne\nf\n")
    visible = [Span("answer.py", 2, 3, "b\nc\n"), Span("answer.py", 4, 5, "d\ne\n")]
    response = {"spans": [
        {"path": "answer.py", "start_line": 2, "end_line": 3},
        {"path": "answer.py", "start_line": 4, "end_line": 5},
        {"path": "answer.py", "start_line": 1, "end_line": 2},
        {"path": "missing.py", "start_line": 1, "end_line": 1},
    ]}
    event = {"type": "message_end", "message": {"role": "assistant",
             "content": [{"type": "text", "text": json.dumps(response)}],
             "usage": {"totalTokens": 110, "input": 3, "cacheRead": 7,
                       "cacheWrite": 90, "output": 10, "cost": {"total": 0.001}}}}

    merged, invalid, usage = _model_ranges(json.dumps(event), visible, {file.path: file}, 4)

    assert [(span.path, span.start_line, span.end_line, span.text) for span in merged] == [
        ("answer.py", 2, 5, "b\nc\nd\ne\n")
    ]
    assert invalid == 2
    assert usage == {"calls": 1, "input": 3, "cache_read": 7,
                     "cache_write": 90, "output": 10, "cost": 0.001}


def test_ranked_pool_extends_saved_top_paths() -> None:
    row = {"id": "q001", "pool": ["a.py", "b.py", "c.py"],
           "rerank_scores": {"a.py": 0.3, "b.py": 0.8, "c.py": 0.3},
           "ranked_paths": ["b.py", "a.py"]}

    assert _candidate_paths(row, "ranked-pool", 3) == ["b.py", "a.py", "c.py"]
