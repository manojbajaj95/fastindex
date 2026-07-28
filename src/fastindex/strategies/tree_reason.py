"""PageIndex-inspired tree-reason: relevance-gate → descend → bubble spans.

Walks the OKF directory tree top-down. At each dir node an LLM decides whether
the node is relevant; only then opens child dirs / concepts. For each opened
concept a second gate selects section spans (not answers).

Width/depth are unbounded by default. Circuit breakers: wall-time and
model-call budgets set ``truncated=True`` and return partial spans.

Hierarchy walk is a local find/miss baseline — not expected to assemble
cross-page multi-hop gold (no wikilink hops).
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Protocol

from fastindex.bundle import Bundle, Concept, DirNode
from fastindex.models import ModelUsage, RemoteModel, estimate_cost_usd
from fastindex.strategies import StrategyConfig, StrategyConstraints, register
from fastindex.types import RetrieveResult, RunStats, Span

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


class _ChatModel(Protocol):
    available: bool
    chat_model: str

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> Any: ...


def _extract_json_object(text: str) -> str | None:
    """Return the first balanced ``{…}`` substring, or None."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _parse_json(text: str) -> dict:
    """Parse model JSON; strip markdown fences and find the first object."""
    text = text.strip()
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    blob = _extract_json_object(text)
    if blob is None:
        raise json.JSONDecodeError("No JSON object found", text, 0)
    data = json.loads(blob)
    if not isinstance(data, dict):
        raise json.JSONDecodeError("JSON root is not an object", blob, 0)
    return data


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    if value is None:
        return default
    return bool(value)


def _normalize_dir(dir_path: str, child: str, tree: dict[str, DirNode]) -> str | None:
    """Resolve a model-returned child dir against the bundle tree."""
    child = child.strip().strip("/")
    if not child:
        return None
    candidates = [child]
    if dir_path and not child.startswith(dir_path + "/") and child != dir_path:
        candidates.append(f"{dir_path}/{child}")
        leaf = Path(child).name
        candidates.append(f"{dir_path}/{leaf}")
    for cand in candidates:
        if cand in tree:
            return cand
    return None


def _normalize_concept(dir_path: str, cpath: str, concepts: dict[str, Concept]) -> Concept | None:
    cpath = cpath.strip().lstrip("/")
    if not cpath:
        return None
    if not cpath.endswith(".md"):
        cpath = f"{cpath}.md"
    if cpath in concepts:
        return concepts[cpath]
    if dir_path:
        alt = f"{dir_path}/{Path(cpath).name}"
        if alt in concepts:
            return concepts[alt]
    name = Path(cpath).name
    for path, concept in concepts.items():
        if Path(path).name == name and (not dir_path or path.startswith(dir_path + "/")):
            return concept
    return None


def _clamp_span(concept: Concept, start: int, end: int) -> tuple[int, int]:
    n = max(1, len(concept.lines))
    start = max(1, int(start))
    end = min(n, max(start, int(end)))
    return start, end


@register
class TreeReasonStrategy:
    name = "tree-reason"
    constraints = StrategyConstraints(
        requires_llm=True,
        allows_cold=True,
        track="vectorless",
    )

    def __init__(self, model: _ChatModel | None = None) -> None:
        self._injected_model = model

    def retrieve(self, query: str, bundle: Bundle, cfg: StrategyConfig) -> RetrieveResult:
        model = self._resolve_model(cfg)
        if not model.available:
            raise RuntimeError(
                "Set FASTINDEX_MODEL (and the matching provider API key) for tree-reason"
            )

        # Budget clock only (truncation) — end-to-end latency is measured by evals.bench.
        deadline = time.monotonic() + cfg.wall_time_budget_s
        call_budget = cfg.model_call_budget
        truncated = False
        hops = 0
        nodes_visited = 0
        max_depth = 0
        branch_opens = 0
        gate_count = 0
        input_tokens = 0
        output_tokens = 0
        model_calls = 0
        selected: list[tuple[str, int, int]] = []

        stack: list[tuple[str, int]] = [("", 0)]
        visited_dirs: set[str] = set()

        while stack:
            if time.monotonic() > deadline or model_calls >= call_budget:
                truncated = True
                break

            dir_path, depth = stack.pop()
            if dir_path in visited_dirs:
                continue
            visited_dirs.add(dir_path)
            nodes_visited += 1
            max_depth = max(max_depth, depth)
            hops += 1

            node = bundle.tree.get(dir_path)
            if node is None:
                continue

            gate = self._gate_dir(model, query, bundle, node, dir_path)
            usage: ModelUsage = gate["usage"]
            model_calls += usage.calls
            input_tokens += usage.input_tokens
            output_tokens += usage.output_tokens
            gate_count += 1

            if not gate.get("relevant", False):
                continue

            open_dirs = list(gate.get("open_dirs") or [])
            open_concepts = list(gate.get("open_concepts") or [])
            branch_opens += len(open_dirs) + len(open_concepts)

            resolved_dirs: list[str] = []
            for child in open_dirs:
                resolved = _normalize_dir(dir_path, child, bundle.tree)
                if resolved and resolved not in visited_dirs:
                    resolved_dirs.append(resolved)
            for child in reversed(resolved_dirs):
                stack.append((child, depth + 1))

            for cpath in open_concepts:
                if time.monotonic() > deadline or model_calls >= call_budget:
                    truncated = True
                    break
                concept = _normalize_concept(dir_path, cpath, bundle.concepts)
                if not concept:
                    continue
                sec_gate = self._gate_sections(model, query, concept)
                usage = sec_gate["usage"]
                model_calls += usage.calls
                input_tokens += usage.input_tokens
                output_tokens += usage.output_tokens
                nodes_visited += 1
                hops += 1

                if not sec_gate.get("relevant", False):
                    continue
                sections = list(sec_gate.get("sections") or [])
                if not sections:
                    sections = [{"start_line": 1, "end_line": max(1, len(concept.lines))}]
                for sec in sections:
                    start, end = _clamp_span(
                        concept,
                        sec.get("start_line", 1),
                        sec.get("end_line", len(concept.lines)),
                    )
                    selected.append((concept.path, start, end))

            if truncated:
                break

        spans = self._materialize_spans(bundle, selected, cfg.top_k)
        avg_branch = (branch_opens / gate_count) if gate_count else 0.0
        stats = RunStats(
            model_calls=model_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimate_cost_usd(input_tokens, output_tokens, model.chat_model),
            hops=hops,
            nodes_visited=nodes_visited,
            max_depth=max_depth,
            truncated=truncated,
            track="cold",
            model_id=model.chat_model,
            extra={
                "dirs_visited": len(visited_dirs),
                "branch_opens": branch_opens,
                "avg_branching": round(avg_branch, 3),
                "gate_count": gate_count,
            },
        )
        return RetrieveResult(spans=spans, stats=stats)

    def _resolve_model(self, cfg: StrategyConfig) -> _ChatModel:
        if self._injected_model is not None:
            return self._injected_model
        injected = cfg.extra.get("model")
        if injected is not None:
            return injected
        override = os.environ.get("FASTINDEX_TREE_REASON_MODEL")
        return RemoteModel(chat_model=override) if override else RemoteModel()

    def _materialize_spans(
        self, bundle: Bundle, selected: list[tuple[str, int, int]], top_k: int
    ) -> list[Span]:
        spans: list[Span] = []
        seen: set[tuple[str, int, int]] = set()
        for path, start, end in selected:
            key = (path, start, end)
            if key in seen:
                continue
            seen.add(key)
            concept = bundle.concepts.get(path)
            if not concept:
                continue
            start, end = _clamp_span(concept, start, end)
            text = "".join(concept.lines[start - 1 : end])
            spans.append(Span(path=path, start_line=start, end_line=end, text=text))
            if len(spans) >= top_k:
                break
        return spans

    def _gate_dir(
        self,
        model: _ChatModel,
        query: str,
        bundle: Bundle,
        node: DirNode,
        dir_path: str,
    ) -> dict:
        children = []
        for cd in node.children_dirs:
            name = Path(cd).name
            idx = (bundle.indexes.get(cd) or "")[:500]
            children.append({"dir": cd, "name": name, "index_preview": idx})
        concepts = []
        for cpath in node.concepts:
            c = bundle.concepts[cpath]
            concepts.append(
                {
                    "path": c.path,
                    "title": c.title,
                    "description": c.description,
                    "when_to_use": c.when_to_use,
                    "type": c.type,
                    "tags": c.tags,
                }
            )
        index_preview = (node.index_md or bundle.indexes.get(dir_path) or "")[:1500]
        prompt = {
            "query": query,
            "current_dir": dir_path or "/",
            "index_md": index_preview,
            "child_dirs": children,
            "concepts": concepts,
            "instructions": (
                "Relevance-gate this directory for the query (PageIndex-style). "
                "If the directory subtree cannot help, set relevant=false and open nothing. "
                "If relevant, open only child dirs / concept files that may contain evidence. "
                "Do not answer the query. Return JSON only: "
                '{"relevant": true|false, '
                '"open_dirs": ["dir/path"], '
                '"open_concepts": ["path.md"], '
                '"reason": "short"}'
            ),
        }
        result = model.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You navigate an OKF knowledge bundle tree with relevance gating. "
                        "Reply with JSON only. Never invent paths."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt)},
            ]
        )
        try:
            data = _parse_json(result.text)
        except Exception:
            data = {"relevant": False, "open_dirs": [], "open_concepts": []}
        data["usage"] = result.usage
        data["relevant"] = _as_bool(data.get("relevant"), default=False)
        if not data["relevant"] and (data.get("open_dirs") or data.get("open_concepts")):
            data["relevant"] = True
        data.setdefault("open_dirs", [])
        data.setdefault("open_concepts", [])
        if not data["relevant"]:
            data["open_dirs"] = []
            data["open_concepts"] = []
        return data

    def _gate_sections(self, model: _ChatModel, query: str, concept: Concept) -> dict:
        secs = [
            {
                "start_line": s.start_line,
                "end_line": s.end_line,
                "heading": s.heading,
                "preview": s.text[:400],
            }
            for s in concept.sections
        ]
        prompt = {
            "query": query,
            "path": concept.path,
            "title": concept.title,
            "description": concept.description,
            "sections": secs,
            "instructions": (
                "Relevance-gate this page. If irrelevant, set relevant=false. "
                "If relevant, return only section line ranges that evidence the query. "
                "Do not answer the query. Return JSON only: "
                '{"relevant": true|false, '
                '"sections": [{"start_line": N, "end_line": M}], '
                '"reason": "short"}'
            ),
        }
        result = model.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You select markdown evidence spans for a query. "
                        "Reply with JSON only. Do not generate an answer."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt)},
            ]
        )
        try:
            data = _parse_json(result.text)
        except Exception:
            data = {"relevant": False, "sections": []}
        data["usage"] = result.usage
        data["relevant"] = _as_bool(data.get("relevant"), default=False)
        data.setdefault("sections", [])
        if data.get("sections") and not data["relevant"]:
            data["relevant"] = True
        if not data["relevant"]:
            data["sections"] = []
        return data
