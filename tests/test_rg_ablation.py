from evals.baselines.ripgrep import rank_rg, rg_hits


def test_rg_literal_search_and_path_boost(tmp_path) -> None:
    (tmp_path / "json_provider.py").write_text("JSON provider\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("JSON provider\n", encoding="utf-8")

    hits, _ = rg_hits("Where is the JSON provider?", tmp_path, ["json_provider.py", "other.py"])
    assert hits == {
        "json_provider.py": {"json", "provider"},
        "other.py": {"json", "provider"},
    }
    assert rank_rg(hits, 2, path_boost=2)[0] == "json_provider.py"


def test_rg_json_allows_unicode_line_separator_in_match(tmp_path) -> None:
    (tmp_path / "unicode.py").write_text("JSON\u2028provider\n", encoding="utf-8")

    hits, _ = rg_hits("JSON provider", tmp_path, ["unicode.py"])

    assert hits == {"unicode.py": {"json", "provider"}}
