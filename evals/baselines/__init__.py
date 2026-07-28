"""Bench baseline / peer adapters implementing the product Strategy interface."""

from __future__ import annotations

from fastindex.strategies import Strategy, StrategyConfig, StrategyConstraints

_REGISTRY: dict[str, type] = {}


def register(cls: type) -> type:
    instance_name = getattr(cls, "name", None)
    if not instance_name:
        raise TypeError(f"{cls} missing name")
    _REGISTRY[instance_name] = cls
    return cls


def _ensure_loaded() -> None:
    from evals.baselines import bm25 as _bm25  # noqa: F401
    from evals.baselines import cognee_kg as _cognee  # noqa: F401
    from evals.baselines import qmd as _qmd  # noqa: F401
    from evals.baselines import rerank as _rerank  # noqa: F401
    from evals.baselines import vsearch as _vsearch  # noqa: F401


def get_baseline(name: str) -> Strategy:
    if name not in _REGISTRY:
        _ensure_loaded()
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(f"Unknown baseline {name!r}. Known: {known}")
    return _REGISTRY[name]()


def list_baselines() -> list[str]:
    _ensure_loaded()
    return sorted(_REGISTRY)


__all__ = [
    "Strategy",
    "StrategyConfig",
    "StrategyConstraints",
    "get_baseline",
    "list_baselines",
    "register",
]
