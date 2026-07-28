"""Evaluation harness: baselines, gold metrics, fixtures, and bench scripts.

Depends on ``fastindex`` one-way. Product code must not import this package.
"""

from __future__ import annotations

from pathlib import Path

EVALS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = EVALS_ROOT.parent
FIXTURES = EVALS_ROOT / "fixtures"
RESULTS = EVALS_ROOT / "results"
SAMPLE_BUNDLE = FIXTURES / "sample-bundle"
SAMPLE_QUERIES = FIXTURES / "queries" / "sample.jsonl"
MULTI_HOP_QUERIES = FIXTURES / "queries" / "multi_hop.jsonl"
