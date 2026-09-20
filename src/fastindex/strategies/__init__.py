"""Owned strategy protocol and product registry (tree-reason only)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from fastindex.bundle import Bundle
from fastindex.types import RetrieveResult


@dataclass(slots=True)
class StrategyConstraints:
    """Capability flags for a retrieve strategy.

    ``requires_index`` / ``requires_embeddings`` are used by evals baselines;
    product tree-reason is always cold (both False).
    """

    requires_index: bool = False
    requires_embeddings: bool = False
    requires_llm: bool = False
    allows_cold: bool = True
    track: str = "vectorless"  # vectorless | open


@dataclass
class StrategyConfig:
    top_k: int = 2
    wall_time_budget_s: float = 60.0
    model_call_budget: int = 32
    parallelism: int = 4
    extra: dict = field(default_factory=dict)


class Strategy(Protocol):
    name: str
    constraints: StrategyConstraints

    def retrieve(self, query: str, bundle: Bundle, cfg: StrategyConfig) -> RetrieveResult: ...


_REGISTRY: dict[str, type] = {}


def register(cls: type) -> type:
    instance_name = getattr(cls, "name", None)
    if not instance_name:
        raise TypeError(f"{cls} missing name")
    _REGISTRY[instance_name] = cls
    return cls


def _ensure_loaded() -> None:
    """Import owned strategy modules so the product registry is complete."""
    from fastindex.strategies import tree_reason as _tree  # noqa: F401


def get_strategy(name: str) -> Strategy:
    if name not in _REGISTRY:
        _ensure_loaded()
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(f"Unknown strategy {name!r}. Known: {known}")
    return _REGISTRY[name]()


def list_strategies() -> list[str]:
    _ensure_loaded()
    return sorted(_REGISTRY)
