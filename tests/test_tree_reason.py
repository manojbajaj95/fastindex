import json
import threading
import time

import pytest

from fastindex.bundle import load_lazy_bundle
from fastindex.models import ChatResult, ModelUsage
from fastindex.strategies import StrategyConfig
from fastindex.strategies.tree_reason import TreeReasonStrategy


class BranchModel:
    available = True
    chat_model = "fake"

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def chat(self, messages, *, temperature=0.0):
        prompt = json.loads(messages[-1]["content"])
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.02)
        with self.lock:
            self.active -= 1
        if "current_dir" in prompt:
            path = prompt["current_dir"]
            data = {
                "relevant": True,
                "open_dirs": ["left", "right"] if path == "/" else [],
                "open_concepts": [f"{path}/answer.py"] if path != "/" else [],
            }
        else:
            data = {"relevant": True, "sections": [{"start_line": 1, "end_line": 1}]}
        return ChatResult(json.dumps(data), ModelUsage(calls=1))


def test_parallel_branches_and_call_budget(tmp_path):
    (tmp_path / "index.md").write_text("# Root\n- [left](left/)\n- [right](right/)\n")
    for name in ("left", "right"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "index.md").write_text(f"# {name}\n- [answer](answer.py)\n")
        (folder / "answer.py").write_text(f"answer = '{name}'\n")

    model = BranchModel()
    bundle = load_lazy_bundle(tmp_path)
    result = TreeReasonStrategy(model).retrieve(
        "find both answers", bundle, StrategyConfig(top_k=2, parallelism=2)
    )
    assert {span.path for span in result.spans} == {"left/answer.py", "right/answer.py"}
    assert result.stats.model_calls == 5
    assert model.max_active == 2

    limited = TreeReasonStrategy(BranchModel()).retrieve(
        "find both answers", bundle, StrategyConfig(model_call_budget=3, parallelism=2)
    )
    assert limited.stats.model_calls == 3
    assert limited.stats.truncated
    assert not limited.spans


def test_query_requires_prepared_root(tmp_path):
    (tmp_path / "answer.py").write_text("answer = 1\n")
    with pytest.raises(RuntimeError, match="run fastindex prepare first"):
        TreeReasonStrategy(BranchModel()).retrieve(
            "answer", load_lazy_bundle(tmp_path), StrategyConfig()
        )


def test_deep_match_in_selected_source_file_is_visible(tmp_path):
    (tmp_path / "index.md").write_text("# Source\n- [long.py](long.py) — needle lookup\n")
    lines = [f"line {number}: padding text of no interest\n" for number in range(1, 161)]
    lines[149] = "line 150: needle = 'found'\n"
    (tmp_path / "long.py").write_text("".join(lines))

    class NeedleModel:
        available = True
        chat_model = "fake"

        def chat(self, messages, *, temperature=0.0):
            prompt = json.loads(messages[-1]["content"])
            if "current_dir" in prompt:
                data = {"relevant": True, "open_dirs": [], "open_concepts": ["long.py"]}
            else:
                visible = any("needle =" in section["preview"] for section in prompt["sections"])
                data = {
                    "relevant": visible,
                    "sections": [{"start_line": 150, "end_line": 150}] if visible else [],
                }
            return ChatResult(json.dumps(data), ModelUsage(calls=1))

    result = TreeReasonStrategy(NeedleModel()).retrieve(
        "needle lookup", load_lazy_bundle(tmp_path), StrategyConfig()
    )
    assert [(span.path, span.start_line) for span in result.spans] == [("long.py", 150)]
