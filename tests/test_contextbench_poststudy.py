"""Check the new controls' ranking mechanics without external services."""

from __future__ import annotations

import sqlite3

from evals.contextbench_flat_paths import _rank
from evals.contextbench_fts import _rank as fts_rank
from evals.contextbench_poststudy import _basename_rank, _cluster_sign_flip
from evals.contextbench_rg_feedback import _feedback_terms
from fastindex.models import ModelUsage


class _ChoiceStub:
    max_request_chars = 48_000

    def choose(self, state: str, options: list[str]):
        assert state == "User issue: issue"
        return [1 / (i + 1) for i in range(len(options))] + [0], ModelUsage(calls=1)


def test_flat_tournament_sees_every_path_and_keeps_sixteen() -> None:
    ranked, usage, calls, retries = _rank(
        _ChoiceStub(), "issue", [f"f{i}.py" for i in range(401)]
    )
    assert len(ranked) == len(set(ranked)) == 16
    assert calls == usage.calls == 4  # three first-round menus, one final menu
    assert retries == 0


def test_flat_tournament_retries_transient_read_timeout() -> None:
    class Once(_ChoiceStub):
        attempts = 0

        def choose(self, state: str, options: list[str]):
            self.attempts += 1
            if self.attempts == 1:
                raise TimeoutError("read timed out")
            return super().choose(state, options)

    model = Once()
    ranked, usage, calls, retries = _rank(model, "issue", [f"f{i}.py" for i in range(20)])
    assert len(ranked) == 16
    assert (calls, retries, usage.calls, model.attempts) == (1, 1, 1, 2)


def test_path_only_fts_ignores_file_content() -> None:
    db = sqlite3.connect(":memory:")
    db.execute("CREATE VIRTUAL TABLE files USING fts5(path)")
    db.executemany("INSERT INTO files (path) VALUES (?)", [("src/login.py",),
                                                           ("src/session.py",)])
    assert fts_rank(db, "login", path_only=True) == ["src/login.py"]
    assert fts_rank(db, "password", path_only=True) == []


def test_feedback_terms_respond_to_first_search_frequency() -> None:
    hits = {"a.py": {"rare", "common"}, "b.py": {"common"}}
    assert _feedback_terms(hits) == ["rare", "common"]


def test_basename_heuristic_prefers_filename_match() -> None:
    paths = ["src/login/session.py", "src/auth/login.py", "docs/other.md"]
    assert _basename_rank("login failure", paths) == ["src/auth/login.py",
                                                      "src/login/session.py"]


def test_sign_flip_keeps_repository_pairs_together() -> None:
    rows = [
        {"id": qid, "repo": repo, "k": 8, "arm": arm, "file_recall": score}
        for qid, repo in (("a", "r1"), ("b", "r2"))
        for arm, score in (("tree", 1.0), ("fts5", 0.0))
    ]
    assert _cluster_sign_flip(rows)["two_sided_p"] == 0.5
