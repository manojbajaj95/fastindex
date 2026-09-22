"""Plain Pi coding agent used as a retrieval-only external baseline."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from evals.baselines import StrategyConfig, StrategyConstraints, register
from fastindex.bundle import Bundle
from fastindex.types import RetrieveResult, RunStats, Span


def _assistant_messages(stdout: str) -> list[dict[str, Any]]:
    messages = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = event.get("message")
        if event.get("type") == "message_end" and message and message.get("role") == "assistant":
            messages.append(message)
    return messages


def _json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("Pi did not return a JSON object")


def _parse_pi_output(
    stdout: str, root: Path, top_k: int
) -> tuple[list[Span], dict[str, int | float]]:
    messages = _assistant_messages(stdout)
    texts = [
        part.get("text", "")
        for message in messages
        for part in message.get("content", [])
        if part.get("type") == "text" and part.get("text")
    ]
    payload = _json_object(texts[-1] if texts else "")

    spans: list[Span] = []
    seen: set[tuple[str, int, int]] = set()
    resolved_root = root.resolve()
    for item in payload.get("spans") or []:
        try:
            supplied_path = str(item.get("path") or "").removeprefix("./")
            path = (root / supplied_path).resolve()
            path.relative_to(resolved_root)
            if not supplied_path or not path.is_file():
                continue
            relative = path.relative_to(resolved_root).as_posix()
            start = int(item["start_line"])
            end = int(item["end_line"])
            lines = path.read_text(encoding="utf-8").splitlines()
        except (KeyError, OSError, TypeError, UnicodeError, ValueError):
            continue
        if start < 1 or end < start or end > len(lines):
            continue
        key = (relative, start, end)
        if key in seen:
            continue
        seen.add(key)
        spans.append(
            Span(
                path=relative,
                start_line=start,
                end_line=end,
                text="\n".join(lines[start - 1 : end]),
            )
        )
        if len(spans) == top_k:
            break

    usage = {"calls": 0, "input": 0, "output": 0, "cost": 0.0, "tools": 0}
    for message in messages:
        raw = message.get("usage") or {}
        if raw.get("totalTokens"):
            usage["calls"] += 1
        usage["input"] += int(raw.get("input") or 0) + int(raw.get("cacheRead") or 0)
        usage["output"] += int(raw.get("output") or 0)
        usage["cost"] += float((raw.get("cost") or {}).get("total") or 0)
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "tool_execution_start":
            usage["tools"] += 1
    return spans, usage


@register
class PiAgentStrategy:
    """Run a fresh local Pi session and ask it only for ranked evidence spans."""

    name = "pi"
    constraints = StrategyConstraints(allows_cold=True, track="agent")

    def retrieve(self, query: str, bundle: Bundle, cfg: StrategyConfig) -> RetrieveResult:
        pi = shutil.which("pi")
        if not pi:
            raise RuntimeError("pi not found on PATH")

        model = os.environ.get("FASTINDEX_PI_MODEL", "openai/gpt-5.6-luna")
        prompt = f"""Act only as a code retriever for the repository in your current directory.
Find the source evidence needed to answer this question:

{query}

Use read-only tools. Do not answer the question. Return only this JSON shape, with at most
{cfg.top_k} spans ranked most useful first:
{{"spans":[{{"path":"relative/path.py","start_line":1,"end_line":10}}]}}
Use repository-relative paths and precise 1-based inclusive line ranges. No Markdown.
"""
        command = [
            pi,
            "--print",
            "--mode",
            "json",
            "--no-session",
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-context-files",
            "--tools",
            "read,grep,find,ls",
            "--model",
            model,
            prompt,
        ]
        completed = subprocess.run(
            command,
            cwd=bundle.root,
            capture_output=True,
            text=True,
            timeout=cfg.wall_time_budget_s,
            check=False,
        )
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"Pi exited {completed.returncode}: {detail[-1000:]}")

        spans, usage = _parse_pi_output(completed.stdout, bundle.root, cfg.top_k)
        return RetrieveResult(
            spans=spans,
            stats=RunStats(
                model_calls=int(usage["calls"]),
                input_tokens=int(usage["input"]),
                output_tokens=int(usage["output"]),
                estimated_cost_usd=float(usage["cost"]),
                hops=int(usage["tools"]),
                nodes_visited=int(usage["tools"]),
                track="cold",
                model_id=model,
            ),
        )
