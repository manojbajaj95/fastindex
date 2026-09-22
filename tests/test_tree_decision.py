import json
from io import BytesIO

from fastindex.bundle import load_lazy_bundle
from fastindex.models import ModelUsage
from fastindex.strategies import StrategyConfig, list_strategies, tree_decision
from fastindex.strategies.tree_decision import (
    TreeDecisionStrategy,
    TypeSafeModel,
    make_decision_model,
)


class FakeDecisionModel:
    chat_model = "fake-decision"
    max_options = 254
    max_request_chars = 2800
    input_cost_per_million = 0.0

    def format_option(self, option):
        return option

    def choose(self, state, options):
        if "right/" in options[0] or "right/" in options[-1]:
            scores = [0.05 if "wrong" in option else 0.9 for option in options]
        else:
            scores = [0.9 if "right" in option or "Answer" in option else 0.05
                      for option in options]
        return [*scores, 0.05], ModelUsage(calls=1, model_id=self.chat_model)


def test_decision_walk_prunes_wrong_branch_and_returns_section(tmp_path):
    (tmp_path / "index.md").write_text("# Root\n- [wrong](wrong/)\n- [right](right/)\n")
    for name in ("wrong", "right"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "index.md").write_text("# Files\n- [Answer](answer.md)\n")
        (folder / "answer.md").write_text("# Answer\nThe useful evidence.\n")
    out = TreeDecisionStrategy(FakeDecisionModel()).retrieve(
        "find the answer", load_lazy_bundle(tmp_path), StrategyConfig(top_k=1)
    )
    assert [(s.path, s.start_line) for s in out.spans] == [("right/answer.md", 1)]
    assert out.stats.model_calls == 2

    limited = TreeDecisionStrategy(FakeDecisionModel()).retrieve(
        "find the answer", load_lazy_bundle(tmp_path), StrategyConfig(top_k=1, model_call_budget=1)
    )
    assert limited.stats.model_calls == 1
    assert limited.stats.truncated
    assert not limited.spans

    files = TreeDecisionStrategy(FakeDecisionModel()).retrieve(
        "find the answer",
        load_lazy_bundle(tmp_path),
        StrategyConfig(top_k=1, extra={"decision_return_files": True}),
    )
    assert [(s.path, s.start_line, s.end_line) for s in files.spans] == [
        ("right/answer.md", 1, 2)
    ]
    assert files.stats.model_calls == 1
    assert files.stats.extra["files_reached"] == ["right/answer.md"]


def test_typesafe_model_maps_choice_probabilities(monkeypatch):
    captured = {}

    class Response(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def fake_urlopen(request, timeout):
        body = json.loads(request.data)
        captured.update(body)
        captured["authorization"] = request.get_header("Authorization")
        payload = {
            "model": "jev-1.13.0",
            "answers": {
                "route": {
                    "type": "choice",
                    "choice": "option_0",
                    "confidence": 0.7,
                    "probabilities": {
                        "option_0": 0.8,
                        "option_1": 0.15,
                        "none_of_these": 0.05,
                    },
                },
            },
            "usage": {"input_tokens": 123, "output_tokens": 12},
        }
        return Response(json.dumps(payload).encode())

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(tree_decision, "urlopen", fake_urlopen)

    scores, usage = TypeSafeModel().choose("question", ["first", "second"])

    assert captured["model"] == "jev-latest"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["questions"]["route"]["criteria"] == {
        "option_0": "first",
        "option_1": "second",
        "none_of_these": "None contains direct answer evidence",
    }
    assert scores == [0.8, 0.15, 0.05]
    assert usage.input_tokens == 123
    assert usage.output_tokens == 12
    assert usage.model_id == "typesafe/jev-1.13.0"


def test_decision_model_factory_uses_configured_typesafe_model(monkeypatch):
    monkeypatch.setenv("FASTINDEX_DECISION_MODEL", "typesafe/jev-1.13.0")

    model = make_decision_model()
    assert isinstance(model, TypeSafeModel)
    assert model.model == "jev-1.13.0"
    assert list_strategies() == ["tree-decision", "tree-reason"]
