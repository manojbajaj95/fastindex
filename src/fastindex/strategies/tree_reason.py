"""Model-guided tree retrieval: relevance-gate, descend, then return spans.

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
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Protocol

from fastindex.bundle import Bundle, Concept, DirNode, Section
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


def _match_child(returned: str, allowed: list[str]) -> str | None:
    """Accept only an immediate child named in the current directory."""
    if not isinstance(returned, str):
        return None
    candidate = returned.strip().strip("/")
    if candidate in allowed:
        return candidate
    matches = [path for path in allowed if Path(path).name == candidate]
    return matches[0] if len(matches) == 1 else None


def _clamp_span(concept: Concept, start: int, end: int) -> tuple[int, int]:
    n = max(1, len(concept.lines))
    start = max(1, int(start))
    end = min(n, max(start, int(end)))
    return start, end


def _section_preview(section: Section, query: str) -> str:
    """Show short sections fully and surface query matches deep in longer ones."""
    if len(section.text) <= 2400:
        return section.text
    terms = {
        word.lower()
        for word in re.findall(r"[\w-]{3,}", query)
        if word.lower() not in {"the", "and", "for", "what", "where", "how", "does"}
    }
    matches = [
        f"L{section.start_line + offset}: {line}"
        for offset, line in enumerate(section.text.splitlines())
        if any(term in line.lower() for term in terms)
    ]
    if not matches:
        return section.text[:2400]
    return (section.text[:400] + "\n...\n" + "\n".join(matches))[:2400]


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

        pending = deque([("dir", "", 0)])
        queued_dirs = {""}
        queued_concepts: set[str] = set()
        scheduled_calls = 0
        workers = max(1, cfg.parallelism)

        def visit(task: tuple[str, str, int]) -> tuple[DirNode | Concept | None, dict | None]:
            kind, path, _depth = task
            if kind == "dir":
                node = bundle.get_node(path)
                if node is None:
                    return None, None
                return node, self._gate_dir(model, query, bundle, node, path)
            concept = bundle.get_concept(path)
            if concept is None:
                return None, None
            return concept, self._gate_sections(model, query, concept)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            while pending:
                if time.monotonic() > deadline or scheduled_calls >= call_budget:
                    truncated = True
                    break

                batch_size = min(len(pending), workers, call_budget - scheduled_calls)
                batch = [pending.popleft() for _ in range(batch_size)]
                scheduled_calls += batch_size
                for (kind, path, depth), (item, gate) in zip(
                    batch, executor.map(visit, batch), strict=True
                ):
                    if item is None or gate is None:
                        continue
                    usage: ModelUsage = gate["usage"]
                    model_calls += usage.calls
                    input_tokens += usage.input_tokens
                    output_tokens += usage.output_tokens
                    nodes_visited += 1
                    hops += 1

                    if kind == "dir":
                        node = item
                        assert isinstance(node, DirNode)
                        max_depth = max(max_depth, depth)
                        gate_count += 1
                        if not gate.get("relevant", False):
                            continue
                        for child in gate.get("open_dirs") or []:
                            resolved = _match_child(child, node.children_dirs)
                            if resolved and resolved not in queued_dirs:
                                queued_dirs.add(resolved)
                                pending.append(("dir", resolved, depth + 1))
                                branch_opens += 1
                        for child in gate.get("open_concepts") or []:
                            resolved = _match_child(child, node.concepts)
                            if resolved and resolved not in queued_concepts:
                                queued_concepts.add(resolved)
                                pending.append(("concept", resolved, depth + 1))
                                branch_opens += 1
                    elif gate.get("relevant", False):
                        concept = item
                        assert isinstance(concept, Concept)
                        sections = list(gate.get("sections") or [])
                        if not sections:
                            sections = [{"start_line": 1, "end_line": max(1, len(concept.lines))}]
                        for sec in sections:
                            start, end = _clamp_span(
                                concept,
                                sec.get("start_line", 1),
                                sec.get("end_line", len(concept.lines)),
                            )
                            selected.append((concept.path, start, end))

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
                "dirs_visited": gate_count,
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
            concept = bundle.get_concept(path)
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
            children.append({"dir": cd, "name": name})
        concepts = [{"path": path, "name": Path(path).name} for path in node.concepts]
        index_md = bundle.get_index(dir_path)
        if not index_md or not index_md.strip():
            location = dir_path or "/"
            raise RuntimeError(f"Missing index.md in {location}; run fastindex prepare first")
        prompt = {
            "query": query,
            "current_dir": dir_path or "/",
            "index_md": index_md,
            "child_dirs": children,
            "concepts": concepts,
            "instructions": (
                "Relevance-gate this directory for the query. "
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
                "preview": _section_preview(s, query),
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
