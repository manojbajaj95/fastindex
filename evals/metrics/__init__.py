"""Retrieval-quality and ops metrics for the evals harness.

Aligned with common IR/RAG literature criteria:
- Retrieval quality: span/line recall, precision, F1 vs gold evidence
- Ops: latency, tokens, $/query, model calls, hops
- Setup: cold vs warm track, model_id
"""

from dataclasses import asdict, dataclass, field
from typing import Any

from fastindex.types import RunStats, Span


@dataclass(slots=True)
class GoldSpan:
    path: str
    start_line: int
    end_line: int
    text: str | None = None


@dataclass(slots=True)
class MetricBundle:
    # Ops
    latency_ms: float
    model_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    hops: int
    # Retrieval quality
    span_recall: float
    span_precision: float
    span_f1: float
    line_recall: float
    line_precision: float
    line_f1: float
    file_recall: float
    file_precision: float
    file_f1: float
    # Setup
    track: str
    truncated: bool = False
    nodes_visited: int = 0
    max_depth: int = 0
    model_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_bench_fields(self) -> dict[str, Any]:
        """Flat fields written onto each bench result row."""
        return {
            "span_recall": self.span_recall,
            "span_precision": self.span_precision,
            "span_f1": self.span_f1,
            "line_recall": self.line_recall,
            "line_precision": self.line_precision,
            "line_f1": self.line_f1,
            "file_recall": self.file_recall,
            "file_precision": self.file_precision,
            "file_f1": self.file_f1,
            "latency_ms": self.latency_ms,
            "model_calls": self.model_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "hops": self.hops,
            "track": self.track,
            "truncated": self.truncated,
            "nodes_visited": self.nodes_visited,
            "max_depth": self.max_depth,
            "model_id": self.model_id,
            "extra": dict(self.extra),
        }


def _line_set(span: Span | GoldSpan) -> set[tuple[str, int]]:
    return {(span.path, i) for i in range(span.start_line, span.end_line + 1)}


def _spans_overlap(a: Span | GoldSpan, b: Span | GoldSpan) -> bool:
    if a.path != b.path:
        return False
    return not (a.end_line < b.start_line or b.end_line < a.start_line)


def span_set_metrics(
    predicted: list[Span], gold: list[GoldSpan]
) -> tuple[float, float, float, float, float, float]:
    """Return span_recall, span_precision, span_f1, line_recall, line_precision, line_f1."""
    if not gold and not predicted:
        return 1.0, 1.0, 1.0, 1.0, 1.0, 1.0
    if not gold:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    if not predicted:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

    hits = sum(1 for g in gold if any(_spans_overlap(g, p) for p in predicted))
    span_recall = hits / len(gold)
    pred_hits = sum(1 for p in predicted if any(_spans_overlap(p, g) for g in gold))
    span_precision = pred_hits / len(predicted)
    span_f1 = (
        2 * span_precision * span_recall / (span_precision + span_recall)
        if (span_precision + span_recall)
        else 0.0
    )

    gold_lines: set[tuple[str, int]] = set()
    for g in gold:
        gold_lines |= _line_set(g)
    pred_lines: set[tuple[str, int]] = set()
    for p in predicted:
        pred_lines |= _line_set(p)
    inter = gold_lines & pred_lines
    line_recall = len(inter) / len(gold_lines) if gold_lines else 0.0
    line_precision = len(inter) / len(pred_lines) if pred_lines else 0.0
    line_f1 = (
        2 * line_precision * line_recall / (line_precision + line_recall)
        if (line_precision + line_recall)
        else 0.0
    )
    return span_recall, span_precision, span_f1, line_recall, line_precision, line_f1


def file_set_metrics(predicted: list[Span], gold: list[GoldSpan]) -> tuple[float, float, float]:
    """Return file recall, precision, and F1 over unique paths."""
    predicted_paths = {span.path for span in predicted}
    gold_paths = {span.path for span in gold}
    if not gold_paths and not predicted_paths:
        return 1.0, 1.0, 1.0
    if not gold_paths or not predicted_paths:
        return 0.0, 0.0, 0.0
    overlap = predicted_paths & gold_paths
    recall = len(overlap) / len(gold_paths)
    precision = len(overlap) / len(predicted_paths)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return recall, precision, f1


def build_metrics(
    stats: RunStats,
    predicted: list[Span],
    gold: list[GoldSpan],
    *,
    latency_ms: float,
) -> MetricBundle:
    """Build metrics. ``latency_ms`` is measured by the bench harness around retrieve."""
    sr, sp, sf, lr, lp, lf = span_set_metrics(predicted, gold)
    fr, fp, ff = file_set_metrics(predicted, gold)
    return MetricBundle(
        latency_ms=latency_ms,
        model_calls=stats.model_calls,
        input_tokens=stats.input_tokens,
        output_tokens=stats.output_tokens,
        cost_usd=stats.estimated_cost_usd,
        hops=stats.hops,
        span_recall=sr,
        span_precision=sp,
        span_f1=sf,
        line_recall=lr,
        line_precision=lp,
        line_f1=lf,
        file_recall=fr,
        file_precision=fp,
        file_f1=ff,
        track=stats.track,
        truncated=stats.truncated,
        nodes_visited=stats.nodes_visited,
        max_depth=stats.max_depth,
        model_id=stats.model_id,
        extra=dict(stats.extra),
    )


def row_get(row: dict[str, Any], key: str, *legacy: str, default: Any = 0) -> Any:
    """Read a bench field, falling back to legacy A_/B_/C_/D_ names."""
    if key in row and row[key] is not None:
        return row[key]
    for alt in legacy:
        if alt in row and row[alt] is not None:
            return row[alt]
    return default
