"""Literal ripgrep candidate source for repository retrieval experiments."""

from __future__ import annotations

import json
import math
import subprocess
import time
from collections import Counter
from pathlib import Path

from evals.baselines.bm25 import tokenize

STOP_WORDS = frozenset(
    "what which when where why how does that this the and from with into are for "
    "its use used there between about would could should their these those through "
    "under after before being have been once more than then each such they them "
    "also only given specified particular framework".split()
)


def rg_hits(query: str, root: Path, files: list[str]) -> tuple[dict[str, set[str]], float]:
    """Find files containing each literal query term in the frozen searchable set."""
    terms = sorted(term for term in set(tokenize(query)) - STOP_WORDS if len(term) > 2)
    if not terms:
        return {}, 0.0
    hits: dict[str, set[str]] = {}
    started = time.perf_counter()
    # ponytail: explicit paths match bundle scope; batch paths for larger corpora.
    result = subprocess.run(
        ["rg", "--json", "--no-messages", "-i", "-F", "-f", "-", "--", *files],
        input="\n".join(terms) + "\n",
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"rg failed: {result.stderr.strip()}")
    # JSON strings may contain Unicode line separators; only LF delimits events.
    for line in result.stdout.split("\n"):
        if not line:
            continue
        event = json.loads(line)
        if event["type"] != "match":
            continue
        path = event["data"]["path"]["text"]
        content = event["data"]["lines"]["text"].lower()
        hits.setdefault(path, set()).update(term for term in terms if term in content)
    return hits, (time.perf_counter() - started) * 1000


def rank_rg(hits: dict[str, set[str]], file_count: int, path_boost: float = 2) -> list[str]:
    """Rank files by distinct matching terms, rarity, and optional path matches."""
    df = Counter(term for terms in hits.values() for term in terms)
    scores = {
        path: sum(1 + math.log((file_count + 1) / (df[term] + 1)) for term in terms)
        + path_boost * sum(term in path.lower() for term in terms)
        for path, terms in hits.items()
    }
    return sorted(scores, key=lambda path: (-scores[path], path))
