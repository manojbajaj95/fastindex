import tiktoken

from evals.arb_full_source import _candidate_paths
from evals.arb_oracle_span_ablation import _merged_match_spans, _pack, _round_robin
from evals.hybrid import build_context, select_window_spans
from fastindex.types import Span


def test_find_and_expand_preserves_source_lines_and_falls_back() -> None:
    lines = ["ordinary line\n"] * 320
    lines[170] = "needle lives here\n"
    small = Span("small.py", 1, 1, "small file\n")
    large = Span("large.py", 1, len(lines), "".join(lines))

    selected = select_window_spans(
        "Where does needle live?",
        [small, large],
        whole_file_lines=100,
        window_lines=80,
        windows_per_file=1,
    )
    assert selected[0] is small
    assert (selected[1].start_line, selected[1].end_line) == (161, 240)
    context, included, _ = build_context(selected, 20_000)
    assert included == selected
    assert "00171: needle lives here" in context

    no_match = select_window_spans(
        "unfindable",
        [large],
        whole_file_lines=100,
        window_lines=80,
        windows_per_file=1,
    )
    assert no_match == [large]


def test_token_packing_never_exceeds_budget() -> None:
    encoder = tiktoken.get_encoding("cl100k_base")
    span = Span("answer.py", 10, 10, "needle\n")
    block = "\n===== answer.py =====\n00010: needle\n"
    exact = len(encoder.encode(block))

    assert _pack([span], exact - 1, encoder) == ([], 0, 0)
    assert _pack([span], exact, encoder) == ([span], exact, len(block))


def test_match_centered_intervals_merge_without_repeating_lines() -> None:
    lines = ["ordinary\n"] * 200
    lines[100] = "needle first\n"
    lines[125] = "needle second\n"
    file = Span("answer.py", 1, len(lines), "".join(lines))

    merged = _merged_match_spans("needle", [file], radius=20, anchors_per_file=2)

    assert [(span.start_line, span.end_line) for span in merged] == [(81, 146)]
    assert merged[0].text.count("needle") == 2
    assert _merged_match_spans("missing", [file], radius=20, anchors_per_file=2) == [file]
    assert _merged_match_spans(
        "missing", [file], radius=20, anchors_per_file=2, skip_unmatched=True
    ) == []


def test_rare_term_can_outrank_earlier_common_matches() -> None:
    file = Span("answer.py", 1, 50, "common\n" * 49 + "rare\n")

    overlap = _merged_match_spans(
        "common rare", [file], radius=0, anchors_per_file=1
    )
    idf = _merged_match_spans(
        "common rare", [file], radius=0, anchors_per_file=1, hit_score="idf"
    )

    assert [(span.start_line, span.end_line) for span in overlap] == [(1, 1)]
    assert [(span.start_line, span.end_line) for span in idf] == [(50, 50)]


def test_round_robin_offers_one_interval_per_file_first() -> None:
    a1 = Span("a.py", 1, 1, "one\n")
    a2 = Span("a.py", 2, 2, "two\n")
    b1 = Span("b.py", 1, 1, "three\n")
    b2 = Span("b.py", 2, 2, "four\n")

    assert _round_robin([a1, a2, b1, b2]) == [a1, b1, a2, b2]


def test_candidate_paths_reuses_frozen_rankings() -> None:
    row = {
        "rrf_paths": ["fused.py"],
        "paths": ["tree.py"],
        "rankings": {"literal": ["literal.py"], "bm25": ["bm25.py"]},
    }

    assert _candidate_paths(row, "rrf") == ["fused.py"]
    assert _candidate_paths(row, "tree") == ["tree.py"]
    assert _candidate_paths(row, "literal") == ["literal.py"]
    assert _candidate_paths(row, "bm25") == ["bm25.py"]
