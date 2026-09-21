from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastindex.bundle import load_bundle, load_lazy_bundle


def test_lazy_bundle_opens_only_requested_directory_and_file(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "index.md").write_text("# Root index\n", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "index.md").write_text("# Nested index\n", encoding="utf-8")
    (nested / "page.md").write_text("# Page\nAnswer here.\n", encoding="utf-8")
    (nested / "code.py").write_text("line\n" * 81, encoding="utf-8")
    generated = tmp_path / "node_modules"
    generated.mkdir()
    (generated / "package.js").write_text("ignore", encoding="utf-8")
    hidden_dir = tmp_path / ".github"
    hidden_dir.mkdir()
    (hidden_dir / "index.md").write_text("# Workflows\n", encoding="utf-8")
    (tmp_path / "binary.dat").write_bytes(b"\x00\xff")
    (tmp_path / "escape.md").symlink_to(nested / "page.md")
    (tmp_path / "linked").symlink_to(nested, target_is_directory=True)

    original_read = Path.read_text
    reads: list[Path] = []

    def tracked_read(path: Path, *args, **kwargs) -> str:
        reads.append(path)
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracked_read)
    bundle = load_lazy_bundle(tmp_path)
    assert reads == []
    assert bundle.tree == {}
    root = bundle.get_node("")
    assert root is not None
    assert root.children_dirs == [".github", "nested"]
    assert root.concepts == ["binary.dat"]
    assert bundle.get_index("") == "# Root index\n"
    assert reads == [tmp_path / "index.md"]

    assert bundle.get_index("nested") == "# Nested index\n"
    assert "nested" not in bundle.tree
    node = bundle.get_node("nested")
    assert node is not None
    assert node.concepts == ["nested/code.py", "nested/page.md"]
    assert bundle.get_index("nested") == "# Nested index\n"
    assert reads == [tmp_path / "index.md", nested / "index.md"]

    assert bundle.get_concept("binary.dat") is None
    assert bundle.get_concept("escape.md") is None
    assert bundle.get_concept("linked/page.md") is None
    assert bundle.get_concept("../outside.md") is None
    page = bundle.get_concept("nested/page.md")
    assert page is not None
    assert [(s.heading, s.start_line, s.end_line) for s in page.sections] == [("Page", 1, 2)]
    assert bundle.get_concept("nested/page.md") is page
    assert reads.count(nested / "page.md") == 1

    code = bundle.get_concept("nested/code.py")
    assert code is not None
    assert [(s.start_line, s.end_line) for s in code.sections] == [(1, 80), (81, 81)]


def test_eager_bundle_accessors_keep_existing_behavior(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text("# Index\n", encoding="utf-8")
    (tmp_path / "page.md").write_text("# Page\n", encoding="utf-8")
    bundle = load_bundle(tmp_path)
    assert bundle.get_node("") is bundle.tree[""]
    assert bundle.get_index("") == "# Index\n"
    assert bundle.get_concept("page.md") is bundle.concepts["page.md"]
    assert bundle.get_concept("missing.md") is None


def test_lazy_bundle_cache_publication_is_safe_across_workers(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text("# Index\n", encoding="utf-8")
    (tmp_path / "page.md").write_text("# Page\nContent\n", encoding="utf-8")
    bundle = load_lazy_bundle(tmp_path)

    def load() -> tuple[object, object]:
        return bundle.get_node(""), bundle.get_concept("page.md")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: load(), range(32)))

    assert all(node is results[0][0] for node, _ in results)
    assert all(concept is results[0][1] for _, concept in results)
