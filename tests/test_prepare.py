from pathlib import Path

from fastindex.models import ChatResult, ModelUsage
from fastindex.utils.prepare import CHUNK_CHARS, prepare


class StubModel:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def chat(self, messages: list[dict[str, str]]) -> ChatResult:
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        cues = [word for word in ("alpha", "beta", "gamma") if word in prompt]
        return ChatResult(", ".join(cues) or "No named cue", ModelUsage(calls=1))


def test_prepare_writes_each_layer_and_preserves_existing_indexes(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "details.py").write_text("alpha function\n", encoding="utf-8")
    (tmp_path / "a" / "old.md").write_text("beta notes\n", encoding="utf-8")
    (tmp_path / "a" / "b" / "bad.bin").write_bytes(b"\x00\xff")
    (tmp_path / "a" / "b" / "index.md").write_text("", encoding="utf-8")
    (tmp_path / "readme.md").write_text("gamma guide\n", encoding="utf-8")
    (tmp_path / "log.md").write_text("secret history\n", encoding="utf-8")
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep" / "index.md").write_text("# Existing\ngamma\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("secret", encoding="utf-8")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "workflow.yml").write_text("gamma", encoding="utf-8")
    model = StubModel()

    stats = prepare(tmp_path, model=model)  # type: ignore[arg-type]

    assert stats.written == 4
    assert stats.preserved == 1
    assert stats.files_summarized == 4
    assert stats.files_skipped == 1
    assert stats.skipped_paths == {"a/b/bad.bin": "binary or non-UTF-8"}
    assert stats.usage.calls == len(model.prompts)
    assert "[details.py](details.py) — alpha" in (tmp_path / "a" / "b" / "index.md").read_text()
    assert "[b](b/) — alpha" in (tmp_path / "a" / "index.md").read_text()
    root_index = (tmp_path / "index.md").read_text()
    assert "[a](a/) — alpha, beta" in root_index
    assert "[readme.md](readme.md) — gamma" in root_index
    assert root_index.count("[a](a/)") == 1
    assert "Directory a:" not in root_index
    assert not any("secret history" in prompt for prompt in model.prompts)
    assert (tmp_path / "keep" / "index.md").read_text() == "# Existing\ngamma\n"
    assert not (tmp_path / "node_modules" / "index.md").exists()
    assert (tmp_path / ".github" / "index.md").exists()
    assert "bad.bin" in (tmp_path / "a" / "b" / "index.md").read_text()


def test_prepare_reads_all_large_file_chunks_and_force_rewrites(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text("# Old\n", encoding="utf-8")
    (tmp_path / "large.txt").write_text("alpha " * (CHUNK_CHARS // 6) + " beta", encoding="utf-8")
    model = StubModel()

    stats = prepare(tmp_path, model=model, force=True)  # type: ignore[arg-type]

    assert stats.written == 1
    assert stats.files_summarized == 1
    assert "alpha, beta" in (tmp_path / "index.md").read_text()
    assert any("Segment 2" in prompt and "beta" in prompt for prompt in model.prompts)


def test_prepare_shortens_oversized_model_summary(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("alpha", encoding="utf-8")

    class LongSummaryModel(StubModel):
        def chat(self, messages: list[dict[str, str]]) -> ChatResult:
            self.prompts.append(messages[-1]["content"])
            text = "alpha" if len(self.prompts) == 2 else "alpha " * 250
            return ChatResult(text, ModelUsage(calls=1))

    model = LongSummaryModel()
    stats = prepare(tmp_path, model=model)  # type: ignore[arg-type]

    assert stats.usage.calls == 2
    assert "Shorten this to one plain sentence" in model.prompts[1]
    assert "[notes.md](notes.md) — alpha" in (tmp_path / "index.md").read_text()


def test_prepare_bounds_summary_when_model_ignores_length_request(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("alpha", encoding="utf-8")

    class LongSummaryModel(StubModel):
        def chat(self, messages: list[dict[str, str]]) -> ChatResult:
            self.prompts.append(messages[-1]["content"])
            return ChatResult("alpha " * 250, ModelUsage(calls=1))

    model = LongSummaryModel()
    stats = prepare(tmp_path, model=model)  # type: ignore[arg-type]

    assert stats.usage.calls == 2
    assert len((tmp_path / "index.md").read_text().split(" — ", 1)[1].strip()) <= 400


def test_prepare_retries_empty_model_summary(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("alpha", encoding="utf-8")

    class EmptyOnceModel(StubModel):
        def chat(self, messages: list[dict[str, str]]) -> ChatResult:
            self.prompts.append(messages[-1]["content"])
            return ChatResult("" if len(self.prompts) == 1 else "alpha", ModelUsage(calls=1))

    model = EmptyOnceModel()
    stats = prepare(tmp_path, model=model)  # type: ignore[arg-type]

    assert stats.usage.calls == 2
    assert "[notes.md](notes.md) — alpha" in (tmp_path / "index.md").read_text()
