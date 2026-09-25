import json

from evals.arb_edit_ablation import _path_neighbors, _without_given
from evals.arb_trace_ablation import _files


def test_loads_edit_task_file_chunks(tmp_path) -> None:
    corpus = tmp_path / "corpus/v2_edit2ripple/org__repo"
    corpus.mkdir(parents=True)
    (corpus / "abc.chunks.jsonl").write_text(
        json.dumps({"kind": "file", "path": "src/a.py", "text": "code"}) + "\n",
        encoding="utf-8",
    )
    assert _files(tmp_path, "org/repo", "abc", task="v2_edit2ripple") == {
        "src/a.py": "code",
    }


def test_excludes_given_file_without_changing_other_ranks() -> None:
    rankings = {
        "literal": ["anchor.py", "a.py", "b.py"],
        "literal+path": ["anchor.py", "a.py", "b.py"],
        "bm25": ["anchor.py", "b.py", "a.py"],
        "fusion": ["anchor.py", "a.py", "b.py"],
    }
    selected = _without_given(rankings, {"anchor.py"})
    assert selected["literal"] == ["a.py", "b.py"]
    assert set(selected["fusion"]) == {"a.py", "b.py"}
    assert rankings["fusion"][0] == "anchor.py"


def test_path_neighbors_prefers_same_directory() -> None:
    files = {"src/a.py": "", "src/b.py": "", "src/sub/c.py": "", "else/x.py": ""}
    assert _path_neighbors("src/a.py", files) == [
        "src/b.py", "src/sub/c.py", "else/x.py",
    ]
