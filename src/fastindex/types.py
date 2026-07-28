"""Shared types for retrieval spans and run stats."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Span:
    path: str
    start_line: int
    end_line: int
    text: str
    score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass(slots=True)
class RunStats:
    """Ops from a retrieve call. Latency is measured by the bench harness, not here."""

    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    hops: int = 0
    nodes_visited: int = 0
    max_depth: int = 0
    truncated: bool = False
    track: str = "cold"  # cold | warm
    model_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RetrieveResult:
    spans: list[Span]
    stats: RunStats

    def to_dict(self) -> dict[str, Any]:
        return {
            "spans": [s.to_dict() for s in self.spans],
            "stats": self.stats.to_dict(),
        }
