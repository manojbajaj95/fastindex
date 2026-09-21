"""Vectorless tree walk using typed decisions instead of generated text."""

from __future__ import annotations

import json
import os
import re
import time
from collections import deque
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from fastindex.bundle import Bundle, Concept, Section
from fastindex.models import ModelUsage
from fastindex.strategies import StrategyConfig, StrategyConstraints, register
from fastindex.strategies.tree_reason import _section_preview
from fastindex.types import RetrieveResult, RunStats, Span

_LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)\s*[—–-]?\s*(.*)")


class _CallBudgetExhausted(Exception):
    pass


class WattModel:
    """HTTP client for WattAI's System One choice API."""

    chat_model = "watt-v0.5"
    max_options = 254
    max_request_chars = 2800
    max_option_chars = 800

    def __init__(self, *, url: str | None = None) -> None:
        self.url = url or os.getenv("FASTINDEX_WATT_URL", "https://api.wattai.dev/v1/systemone")

    def format_option(self, option: str) -> str:
        return option[:self.max_option_chars]

    def choose(self, state: str, options: list[str]) -> tuple[list[float], ModelUsage]:
        body = {
            "state": state,
            "questions": [{
                "q": "Which option contains the most direct evidence for the user's question?",
                "options": options,
                "kind": "choice",
                "add_none": True,
            }],
        }
        headers = {"content-type": "application/json"}
        if key := os.getenv("WATTAI_API_KEY"):
            headers["authorization"] = f"Bearer {key}"
        request = Request(self.url, json.dumps(body).encode(), headers, method="POST")
        try:
            with urlopen(request, timeout=30) as response:
                data = json.load(response)
        except HTTPError as exc:
            raise RuntimeError(f"WattAI HTTP {exc.code}") from exc
        results = data.get("results")
        if not isinstance(results, list) or len(results) != 1:
            raise RuntimeError("WattAI returned an incomplete choice result")
        result = results[0]
        labels, probs = result.get("options"), result.get("probs")
        if (
            result.get("kind") != "choice"
            or labels != [*options, "none of these"]
            or not isinstance(probs, list)
            or len(probs) != len(options) + 1
            or any(not isinstance(p, (int, float)) or not 0 <= p <= 1 for p in probs)
        ):
            raise RuntimeError("WattAI returned invalid choice probabilities")
        # The API does not report usage; this is a rough input estimate for ops comparison.
        usage = ModelUsage(calls=1, input_tokens=len(json.dumps(body)) // 4, model_id=data["model"])
        return [float(p) for p in probs], usage


class ClassifierModel:
    """Free classifier.dev routes to Jev or the Laya trial."""

    def __init__(self, model: str = "jev", *, instructions: str | None = None) -> None:
        if model not in {"jev", "laya"}:
            raise ValueError("Classifier model must be 'jev' or 'laya'")
        self.model = model
        self.chat_model = model
        self.max_options = 15 if model == "laya" else 99
        self.max_request_chars = 1200 if model == "laya" else 2800
        self.max_option_chars = 95 if model == "laya" else 190
        self.instructions = instructions or os.getenv(
            "FASTINDEX_CLASSIFIER_INSTRUCTIONS",
            "Choose the source with direct evidence to answer the user question.",
        )

    def format_option(self, option: str) -> str:
        head, _, topics = option.partition("; subtopics: ")
        if topics:
            path, _, summary = head.partition(": ")
            option = f"{path}: {topics}; {summary[:85]}"
        return option[:self.max_option_chars]

    def choose(self, state: str, options: list[str]) -> tuple[list[float], ModelUsage]:
        labels = [self.format_option(option) for option in options]
        labels.append("none of these")
        body = {
            "model": self.model,
            "processing": "fast",
            "input": state,
            "labels": labels,
            "instructions": self.instructions,
        }
        request = Request(
            "https://classifier.dev",
            json.dumps(body).encode(),
            {"content-type": "application/json", "user-agent": "fastindex/0.1"},
            method="POST",
        )
        for attempt in range(4):
            try:
                with urlopen(request, timeout=30) as response:
                    data = json.load(response)
                break
            except HTTPError as exc:
                if self.model == "laya" and exc.code in {429, 503} and attempt < 3:
                    delay = float(exc.headers.get("Retry-After", 2 ** attempt))
                    time.sleep(min(max(delay, 1), 15))
                    continue
                raise RuntimeError(f"classifier.dev HTTP {exc.code}") from exc
        results = data.get("results")
        if not isinstance(results, list) or len(results) != 1:
            raise RuntimeError("classifier.dev returned an incomplete choice result")
        result = results[0]
        actual_model = result.get("model", "")
        if not actual_model.startswith(self.model) or not isinstance(result.get("scores"), dict):
            raise RuntimeError("classifier.dev returned another model or no scores")
        scores = result["scores"]
        if any(label not in scores or not isinstance(scores[label], (int, float))
               for label in labels):
            raise RuntimeError("classifier.dev returned invalid choice probabilities")
        usage = ModelUsage(calls=1, input_tokens=len(json.dumps(body)) // 4,
                           model_id=actual_model)
        return [float(scores[label]) for label in labels], usage


def _choose(
    model: WattModel | ClassifierModel, state: str, options: list[str], max_calls: int
) -> tuple[list[float], ModelUsage]:
    """Score groups that fit the API's question and context limits."""
    scores: list[float] = []
    usage = ModelUsage()
    batch: list[str] = []
    batches: list[list[str]] = []
    size = len(state)
    for option in options:
        option = model.format_option(option)
        if batch and (len(batch) == model.max_options or
                      size + len(option) > model.max_request_chars):
            batches.append(batch)
            batch, size = [], len(state)
        batch.append(option)
        size += len(option)
    if batch:
        batches.append(batch)
    if len(batches) > max_calls:
        raise _CallBudgetExhausted
    for batch in batches:
        values, used = model.choose(state, batch)
        scores.extend(values[:-1])
        usage.add(used)
    return scores, usage


def _index_summary(index: str, path: str) -> str:
    name = Path(path).name
    for line in index.splitlines():
        match = _LINK.search(line)
        if match and Path(match.group(1).rstrip("/")).name == name:
            return line.strip()[:600]
    return name


def _route_option(bundle: Bundle, index: str, kind: str, path: str) -> str:
    """Add bounded descendant titles when a prepared parent summary is too generic."""
    summary = _index_summary(index, path)
    if kind != "dir":
        return f"{path}: {summary}"
    names: list[str] = []
    pending = deque([(path, 0)])
    while pending and len(names) < 12:
        current, depth = pending.popleft()
        node = bundle.get_node(current)
        if node is None:
            continue
        for child in [*node.children_dirs, *node.concepts]:
            names.append(Path(child).stem.replace("-", " "))
            if len(names) >= 12:
                break
        if depth < 2:
            pending.extend((child, depth + 1) for child in node.children_dirs)
    return f"{path}: {summary}; subtopics: {', '.join(names)}"


def _section_option(section: Section, concept: Concept, query: str) -> str:
    if section.heading == "(preamble)":
        # Frontmatter's when_to_use repeats likely query words and distracts the model.
        return f"Metadata for {concept.title}: {concept.description}; source URL and tags"
    return f"{section.heading}: {_section_preview(section, query)[:650]}"


@register
class TreeWattStrategy:
    """Descend through prepared indexes using categorical branch probabilities."""

    name = "tree-watt"
    constraints = StrategyConstraints(allows_cold=True, track="vectorless")
    default_model = WattModel

    def __init__(self, model: WattModel | ClassifierModel | None = None) -> None:
        self.model = model

    def retrieve(self, query: str, bundle: Bundle, cfg: StrategyConfig) -> RetrieveResult:
        model = self.model or cfg.extra.get("model") or self.default_model()
        deadline = time.monotonic() + cfg.wall_time_budget_s
        pending = [("dir", "", 0)]
        spans: list[Span] = []
        visited = 0
        dirs_visited = 0
        max_depth = 0
        usage = ModelUsage()
        truncated = False
        min_probability = float(cfg.extra.get("decision_min_probability", 0.12))
        relative_probability = float(cfg.extra.get("decision_relative_probability", 0.2))
        while pending and len(spans) < cfg.top_k:
            if time.monotonic() >= deadline or usage.calls >= cfg.model_call_budget:
                truncated = True
                break
            kind, path, depth = pending.pop()
            if kind == "dir":
                node = bundle.get_node(path)
                if node is None:
                    continue
                index = bundle.get_index(path)
                if not index or not index.strip():
                    location = path or "/"
                    raise RuntimeError(
                        f"Missing index.md in {location}; run fastindex prepare first"
                    )
                children = [("dir", p) for p in node.children_dirs] + [
                    ("concept", p) for p in node.concepts
                ]
                if not children:
                    continue
                if len(children) == 1:
                    # There is no choice to make at this node.
                    child_kind, child_path = children[0]
                    pending.append((child_kind, child_path, depth + 1))
                    visited += 1
                    dirs_visited += 1
                    max_depth = max(max_depth, depth)
                    continue
                options = [_route_option(bundle, index, kind, p) for kind, p in children]
                try:
                    scores, used = _choose(
                        model, f"User question: {query[:500]}", options,
                        cfg.model_call_budget - usage.calls,
                    )
                except _CallBudgetExhausted:
                    truncated = True
                    break
                usage.add(used)
                visited += 1
                dirs_visited += 1
                max_depth = max(max_depth, depth)
                best = max(scores, default=0)
                # Keep plausible second leads for multi-page questions.
                ranked = sorted(zip(children, scores, strict=True), key=lambda item: item[1])
                for (child_kind, child_path), score in ranked:
                    if score >= max(min_probability, best * relative_probability):
                        pending.append((child_kind, child_path, depth + 1))
            else:
                concept = bundle.get_concept(path)
                if concept is None or not concept.sections:
                    continue
                options = [_section_option(s, concept, query) for s in concept.sections]
                try:
                    scores, used = _choose(
                        model, f"User question: {query[:500]}\nPage: {path}", options,
                        cfg.model_call_budget - usage.calls,
                    )
                except _CallBudgetExhausted:
                    truncated = True
                    break
                usage.add(used)
                visited += 1
                best_idx = max(range(len(scores)), key=scores.__getitem__)
                section = concept.sections[best_idx]
                if scores[best_idx] < 0.12:
                    continue
                if section.heading == "(preamble)":
                    # The heading gate can favor frontmatter over a factual section;
                    # include the page so evidence is not silently discarded.
                    start_line, end_line = 1, len(concept.lines)
                else:
                    start_line, end_line = section.start_line, section.end_line
                spans.append(Span(
                    path=path,
                    start_line=start_line,
                    end_line=end_line,
                    text="".join(concept.lines[start_line - 1:end_line]),
                ))
        stats = RunStats(
            model_calls=usage.calls,
            input_tokens=usage.input_tokens,
            output_tokens=0,
            estimated_cost_usd=0.0,
            hops=visited,
            nodes_visited=visited,
            max_depth=max_depth,
            truncated=truncated,
            track="cold",
            model_id=usage.model_id or model.chat_model,
            extra={"dirs_visited": dirs_visited, "branch_opens": visited - 1},
        )
        return RetrieveResult(spans=spans, stats=stats)


@register
class TreeJevStrategy(TreeWattStrategy):
    """Same tree walk, using Jev through classifier.dev's free fast route."""

    name = "tree-jev"
    default_model = staticmethod(lambda: ClassifierModel("jev"))


@register
class TreeLayaStrategy(TreeWattStrategy):
    """Same tree walk, using the free hosted Laya trial."""

    name = "tree-laya"
    default_model = staticmethod(lambda: ClassifierModel("laya"))
