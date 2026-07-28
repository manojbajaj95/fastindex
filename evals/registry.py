"""Combined strategy lookup for the eval harness (product + baselines)."""

from __future__ import annotations

from evals.baselines import get_baseline, list_baselines
from fastindex.strategies import Strategy
from fastindex.strategies import get_strategy as get_product_strategy
from fastindex.strategies import list_strategies as list_product_strategies


def get_strategy(name: str) -> Strategy:
    """Resolve owned product strategies first, then eval baselines/peers."""
    try:
        return get_product_strategy(name)
    except KeyError:
        return get_baseline(name)


def list_strategies() -> list[str]:
    return sorted(set(list_product_strategies()) | set(list_baselines()))
