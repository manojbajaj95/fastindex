import json
import sqlite3

import tiktoken

from evals.contextbench_audit import _audit_case
from evals.contextbench_context import _clipped_gold, _paired_bootstrap, _score
from evals.contextbench_fts import _rank
from evals.contextbench_holdout import _gold, _selected_ids
from evals.contextbench_pipeline_runtime import _run_case
from evals.contextbench_prepare_inventory import _inventory, _reduction_calls
from evals.contextbench_reuse_tree import _reduce
from evals.contextbench_snapshots import _check_gold
from evals.contextbench_study import (
    _case_features,
    _cluster_interval,
    _file_metrics,
    _summary,
)
from evals.contextbench_tree import _interleave
from fastindex.types import RetrieveResult, RunStats, Span


def test_holdout_selection_rejects_non_repo_gold_and_is_deterministic() -> None:
    def row(qid: str, path: str) -> dict:
        return {
            "instance_id": qid, "repo": "example/repo", "base_commit": "a" * 40,
            "problem_statement": "Find the bug",
            "gold_context": json.dumps([{"file": path, "start_line": 2, "end_line": 3}]),
        }

    rows = [row("a", "a.py"), row("b", "b.py"), row("c", "c.py"),
            row("d", "/workspace/outside.py")]

    assert _selected_ids(rows, "example/repo", 2) == ["c", "b"]
    assert _gold(rows[-1]) == []
    assert _gold(row("e", "../escape.py")) == []


def test_snapshot_check_rejects_missing_and_out_of_range_gold(tmp_path) -> None:
    (tmp_path / "answer.py").write_text("one\ntwo\n", encoding="utf-8")

    assert _check_gold(tmp_path, [{"path": "answer.py", "start_line": 1,
                                   "end_line": 2}]) == ([], [])
    errors, clipped = _check_gold(tmp_path, [
        {"path": "answer.py", "start_line": 1, "end_line": 3},
        {"path": "missing.py", "start_line": 1, "end_line": 1},
    ])
    assert len(errors) == 1
    assert len(clipped) == 1


def test_tree_cases_interleave_repositories() -> None:
    cases = [{"repo": "a", "id": 1}, {"repo": "a", "id": 2},
             {"repo": "b", "id": 3}, {"repo": "b", "id": 4}]

    assert [case["id"] for case in _interleave(cases)] == [1, 3, 2, 4]


def test_context_scoring_clips_gold_and_respects_token_budget() -> None:
    files = {"a.py": "one\ntwo\n", "b.py": "other\n"}
    gold, clipped = _clipped_gold(
        [{"path": "a.py", "start_line": 2, "end_line": 3}], files,
    )
    assert clipped == 1
    assert (gold[0].start_line, gold[0].end_line) == (2, 2)
    rows = _score(
        {"id": "q", "repo": "example/repo"}, files, ["b.py", "a.py"],
        source="test", encoder=tiktoken.get_encoding("cl100k_base"),
        clipped_gold=gold,
    )
    assert len(rows) == 3
    assert all(row["candidate_complete_files"] == 1 for row in rows)
    assert all(row["complete_lines"] == 1 for row in rows)
    windows = _score(
        {"id": "q", "repo": "example/repo", "query": "two"}, files,
        ["b.py", "a.py"], source="test",
        encoder=tiktoken.get_encoding("cl100k_base"), clipped_gold=gold,
        packing="merge4-200",
    )
    assert all(row["complete_lines"] == 1 for row in windows)


def test_paired_bootstrap_uses_case_deltas() -> None:
    rows = [
        {"id": "a", "source": "tree", "budget_tokens": 16_000, "line_recall": 1.0},
        {"id": "a", "source": "search", "budget_tokens": 16_000, "line_recall": 0.0},
        {"id": "b", "source": "tree", "budget_tokens": 16_000, "line_recall": 0.0},
        {"id": "b", "source": "search", "budget_tokens": 16_000, "line_recall": 0.0},
    ]

    result = _paired_bootstrap(rows, "tree", "search", "line_recall", repetitions=100)
    assert result["n"] == 2
    assert result["mean_delta"] == 0.5


def test_fts5_ranks_a_matching_file() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE VIRTUAL TABLE files USING fts5(path, content)")
    connection.executemany("INSERT INTO files VALUES (?, ?)", [
        ("src/blueprint.py", "register blueprint route"),
        ("src/template.py", "render template"),
    ])

    assert _rank(connection, "Where is blueprint registration?") == ["src/blueprint.py"]


def test_prepare_inventory_counts_file_and_child_index_calls(tmp_path) -> None:
    child = tmp_path / "child"
    child.mkdir()
    (tmp_path / "root.py").write_text("root\n", encoding="utf-8")
    (child / "answer.py").write_text("answer\n", encoding="utf-8")
    (child / "binary.bin").write_bytes(b"\x00")
    (child / "index.md").write_text("existing", encoding="utf-8")

    counts = _inventory(tmp_path)
    assert counts["directories"] == 2
    assert counts["source_files"] == 2
    assert counts["skipped_files"] == 1
    assert counts["existing_indexes"] == 1
    assert counts["model_calls_lower_bound"] == 3
    assert _reduction_calls(9) == 3


def test_confirmatory_file_metrics_do_not_hide_impossible_complete_sets() -> None:
    metrics = _file_metrics({"a.py", "b.py", "c.py"}, ["a.py", "x.py"], 2)

    assert metrics == {
        "file_recall": 1 / 3, "complete_files": 0,
        "file_precision": 1 / 2, "gold_found": 1,
    }


def test_confirmatory_uncertainty_resamples_repositories_not_cases() -> None:
    rows = [
        {"id": "a", "repo": "one", "arm": "tree", "file_recall": 1.0},
        {"id": "a", "repo": "one", "arm": "fts5", "file_recall": 0.0},
        {"id": "b", "repo": "one", "arm": "tree", "file_recall": 1.0},
        {"id": "b", "repo": "one", "arm": "fts5", "file_recall": 0.0},
        {"id": "c", "repo": "two", "arm": "tree", "file_recall": 0.0},
        {"id": "c", "repo": "two", "arm": "fts5", "file_recall": 1.0},
    ]

    interval = _cluster_interval(rows, "tree", "fts5", "file_recall",
                                 repetitions=1000)
    assert interval["n_repositories"] == 2
    assert interval["n_cases"] == 3
    assert interval["mean_delta"] == 1 / 3
    assert interval["lower_95"] == -1
    assert interval["upper_95"] == 1


def test_confirmatory_audit_preserves_unsearchable_gold_reason(tmp_path) -> None:
    (tmp_path / "visible.py").write_text("answer\n", encoding="utf-8")
    case = {"id": "q", "repo": "example/repo", "base_commit": "a" * 40,
            "gold": [{"path": "missing.py", "start_line": 1, "end_line": 1}]}
    snapshot = {"repo": "example/repo", "base_commit": "a" * 40,
                "root": str(tmp_path), "errors": []}

    assert _audit_case(case, snapshot) == [
        "Gold file outside common searchable corpus: missing.py",
    ]
    snapshot["errors"] = ["Missing or unsafe gold file: missing.py"]
    assert _audit_case(case, snapshot) == snapshot["errors"]


def test_confirmatory_subgroups_are_defined_without_retrieval_output() -> None:
    features = _case_features({
        "query": "Fix src/auth/middleware.py and its login behavior",
        "gold": [
            {"path": "src/auth/middleware.py"},
            {"path": "src/session/store.py"},
        ],
    })

    assert features["issue_mentions_gold_path"] == 1
    assert features["issue_mentions_gold_basename"] == 1
    assert features["gold_spans_multiple_directories"] == 1


def test_confirmatory_line_summary_keeps_alignment_sensitivity_separate() -> None:
    def row(qid: str, aligned: int, recall: float) -> dict:
        return {
            "id": qid, "repo": "example/repo", "arm": "tree", "k": 8,
            "gold_files": 1, "gold_text_aligned": aligned,
            "file_recall": recall, "complete_files": int(recall == 1),
            "file_precision": recall, "line_recall_16k": recall,
            "complete_lines_16k": int(recall == 1), "context_tokens_16k": 100,
        }

    summary = _summary([row("a", 1, 1.0), row("b", 0, 0.0)])

    assert summary["tree@8/all"]["line_recall_16k"] == 0.5
    assert summary["tree@8/aligned"]["line_recall_16k"] == 1.0
    assert summary["tree@8/unaligned"]["line_recall_16k"] == 0.0


def test_combined_runtime_executes_real_fts5_and_fusion(tmp_path) -> None:
    (tmp_path / "answer.py").write_text("register blueprint route\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("render template\n", encoding="utf-8")

    class Route:
        def retrieve(self, query, bundle, config):
            assert query == "Find blueprint registration"
            assert config.top_k == 16
            return RetrieveResult(
                spans=[Span("answer.py", 1, 1, "register blueprint route\n")],
                stats=RunStats(model_calls=1, input_tokens=10),
            )

    row = _run_case({"id": "q", "repo": "example/repo",
                     "query": "Find blueprint registration"}, tmp_path, Route())

    assert row["tree_paths"] == ["answer.py"]
    assert row["fts5_paths"] == ["answer.py"]
    assert row["fused_paths"] == ["answer.py"]
    assert row["file_count"] == 2
    assert row["total_ms"] >= row["tree_ms"]


def test_reduced_study_reuses_only_matching_successful_tree_rows() -> None:
    source = {"config": {"holdout_sha256": "old", "top_k": 16},
              "results": [{"id": "a", "variant": "wide", "paths": ["a.py"]},
                          {"id": "b", "variant": "wide", "paths": ["b.py"]}]}
    holdout = {"cases": [{"id": "a"}, {"id": "b"}]}
    target = {"cases": [{"id": "b"}]}

    reduced = _reduce(source, holdout, target, "source-sha", "target-sha")

    assert reduced["results"] == [source["results"][1]]
    assert reduced["config"]["holdout_sha256"] == "target-sha"
    assert reduced["config"]["derived_from_tree_sha256"] == "source-sha"
