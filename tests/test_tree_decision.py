import json
from io import BytesIO

from fastindex.bundle import load_lazy_bundle
from fastindex.models import ModelUsage
from fastindex.strategies import StrategyConfig, tree_decision
from fastindex.strategies.tree_decision import ClassifierModel, TreeWattStrategy


class FakeWatt:
    chat_model = "fake-watt"
    max_options = 254
    max_request_chars = 2800

    def format_option(self, option):
        return option

    def choose(self, state, options):
        if "right/" in options[0] or "right/" in options[-1]:
            scores = [0.05 if "wrong" in option else 0.9 for option in options]
        else:
            scores = [0.9 if "right" in option or "Answer" in option else 0.05
                      for option in options]
        return [*scores, 0.05], ModelUsage(calls=1, model_id=self.chat_model)


def test_watt_walk_prunes_wrong_branch_and_returns_section(tmp_path):
    (tmp_path / "index.md").write_text("# Root\n- [wrong](wrong/)\n- [right](right/)\n")
    for name in ("wrong", "right"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "index.md").write_text("# Files\n- [Answer](answer.md)\n")
        (folder / "answer.md").write_text("# Answer\nThe useful evidence.\n")
    out = TreeWattStrategy(FakeWatt()).retrieve(
        "find the answer", load_lazy_bundle(tmp_path), StrategyConfig(top_k=1)
    )
    assert [(s.path, s.start_line) for s in out.spans] == [("right/answer.md", 1)]
    assert out.stats.model_calls == 2

    limited = TreeWattStrategy(FakeWatt()).retrieve(
        "find the answer", load_lazy_bundle(tmp_path), StrategyConfig(top_k=1, model_call_budget=1)
    )
    assert limited.stats.model_calls == 1
    assert limited.stats.truncated
    assert not limited.spans


def test_classifier_model_maps_returned_scores(monkeypatch):
    captured = {}

    class Response(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def fake_urlopen(request, timeout):
        body = json.loads(request.data)
        captured.update(body)
        labels = body["labels"]
        payload = {
            "results": [{
                "model": "jev-test",
                "scores": dict(zip(labels, [0.8, 0.15, 0.05], strict=True)),
            }],
        }
        return Response(json.dumps(payload).encode())

    monkeypatch.setattr(tree_decision, "urlopen", fake_urlopen)

    scores, usage = ClassifierModel("jev").choose("question", ["first", "second"])

    assert captured["labels"] == ["first", "second", "none of these"]
    assert scores == [0.8, 0.15, 0.05]
    assert usage.calls == 1
    assert usage.model_id == "jev-test"
