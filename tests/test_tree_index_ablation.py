from evals.tree_index_ablation import NameOnlyBundle
from fastindex.bundle import Bundle, DirNode
from fastindex.strategies.tree_decision import _route_option


def test_name_only_menu_removes_prepared_summary(tmp_path):
    bundle = Bundle(
        root=tmp_path,
        indexes={"": "- [src](src/) — generated summary with answer"},
        tree={"": DirNode("", children_dirs=["src"], concepts=["README.md"]),
              "src": DirNode("src", concepts=["src/module.py"])},
    )
    names = NameOnlyBundle(bundle)
    index = names.get_index("")
    assert index is not None
    assert "generated summary" not in index
    assert "[src/](src/)" in index
    assert "[README.md](README.md)" in index
    assert "generated summary" not in _route_option(names, index, "concept", "README.md")
    assert "subtopics" not in _route_option(names, index, "dir", "src", 0)
    assert "module" in _route_option(names, index, "dir", "src", 12)
