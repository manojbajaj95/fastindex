"""External QMD CLI baseline — do not reimplement QMD.

Uses ``qmd query`` (hybrid: expand → BM25 + vector → RRF → LLM rerank),
not bare ``qmd search``. See https://github.com/tobi/qmd.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from evals.baselines import StrategyConfig, StrategyConstraints, register
from fastindex.bundle import Bundle, Concept
from fastindex.types import RetrieveResult, RunStats, Span

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b.")


def _find_qmd() -> str | None:
    found = shutil.which("qmd")
    if found:
        return found
    bun = Path.home() / ".bun" / "bin" / "qmd"
    if bun.is_file() and os.access(bun, os.X_OK):
        return str(bun)
    return None


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _extract_json(stdout: str) -> object | None:
    """QMD may mix progress spinners into stdout before the JSON payload."""
    clean = _strip_ansi(stdout).strip()
    if not clean:
        return None
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass
    # Prefer the last top-level JSON value (array or object).
    for opener, closer in (("[", "]"), ("{", "}")):
        start = clean.rfind(opener)
        if start < 0:
            continue
        chunk = clean[start:]
        # Trim trailing junk after matching closer depth
        depth = 0
        in_str = False
        esc = False
        end = None
        for i, ch in enumerate(chunk):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is None:
            continue
        try:
            return json.loads(chunk[:end])
        except json.JSONDecodeError:
            continue
    return None


@register
class QmdStrategy:
    name = "qmd"
    constraints = StrategyConstraints(
        requires_index=True,
        allows_cold=False,
        track="open",
    )

    def retrieve(self, query: str, bundle: Bundle, cfg: StrategyConfig) -> RetrieveResult:
        qmd = _find_qmd()
        if not qmd:
            raise RuntimeError(
                "qmd not found on PATH (tried ~/.bun/bin/qmd). "
                "Install @tobilu/qmd and index the bundle collection."
            )

        # Full quality path: query expansion + FTS + vector + RRF + rerank.
        # Local GGUF query can exceed the default 60s wall budget — allow override
        # but default to at least 5 minutes for this external peer.
        timeout_s = float(cfg.extra.get("qmd_timeout_s") or max(cfg.wall_time_budget_s, 300.0))
        candidate_limit = int(cfg.extra.get("qmd_candidate_limit") or max(cfg.top_k * 10, 40))
        cmd = [qmd, "query", "--json", "-n", str(cfg.top_k), "-C", str(candidate_limit)]
        collection = cfg.extra.get("qmd_collection")
        if collection:
            cmd.extend(["-c", str(collection)])
        if cfg.extra.get("qmd_no_rerank"):
            cmd.append("--no-rerank")
        intent = cfg.extra.get("qmd_intent")
        if intent:
            cmd.extend(["--intent", str(intent)])
        cmd.append(query)

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(bundle.root),
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return RetrieveResult(
                spans=[],
                stats=RunStats(
                    truncated=True,
                    track="warm",
                    extra={"error": "timeout", "qmd_cmd": "query", "timeout_s": timeout_s},
                ),
            )

        spans: list[Span] = []
        stderr_tail = _strip_ansi(proc.stderr or "")[-500:]
        if proc.returncode == 0 and proc.stdout.strip():
            spans = self._parse_output(proc.stdout, bundle, cfg.top_k)
        if not spans and proc.stdout.strip():
            # Fallback: CLI text if JSON extract failed
            spans = self._parse_text(proc.stdout, bundle, cfg.top_k)

        return RetrieveResult(
            spans=spans,
            stats=RunStats(
                track="warm",
                truncated=False,
                extra={
                    "qmd_cmd": "query",
                    "qmd_candidate_limit": candidate_limit,
                    "returncode": proc.returncode,
                    "stderr": stderr_tail,
                },
            ),
        )

    def _parse_output(self, stdout: str, bundle: Bundle, top_k: int) -> list[Span]:
        data = _extract_json(stdout)
        if data is None:
            return self._parse_text(stdout, bundle, top_k)
        items = data if isinstance(data, list) else data.get("results") or data.get("docs") or []
        spans: list[Span] = []
        for item in items[:top_k]:
            if isinstance(item, str):
                path = item
                start, end, text = 1, 1, ""
                score = None
                title = None
            else:
                path = str(
                    item.get("path") or item.get("file") or item.get("displayPath") or ""
                )
                start = int(
                    item.get("fromLine")
                    or item.get("start_line")
                    or item.get("line")
                    or 1
                )
                end = int(item.get("toLine") or item.get("end_line") or start)
                text = str(item.get("snippet") or item.get("text") or item.get("body") or "")
                raw_score = item.get("score")
                score = float(raw_score) if raw_score is not None else None
                title = item.get("title")
            path = self._normalize_path(path, bundle)
            if not path:
                continue
            concept = bundle.concepts.get(path)
            start, end, text = self._materialize_span(concept, start, end, text, title)
            spans.append(
                Span(
                    path=path,
                    start_line=start,
                    end_line=end,
                    text=text[:4000],
                    score=score,
                )
            )
        return spans

    def _materialize_span(
        self,
        concept: Concept | None,
        start: int,
        end: int,
        text: str,
        title: str | None = None,
    ) -> tuple[int, int, str]:
        """Map QMD hit → file span; prefer titled section over a lone ``line``."""
        if concept is None:
            return max(1, start), max(start, end), text
        n = max(1, len(concept.lines))
        start = max(1, min(start, n))
        end = max(start, min(end, n))

        # Prefer section whose heading matches QMD's title (e.g. "Schema").
        if title:
            titled = next(
                (s for s in concept.sections if s.heading.lower() == str(title).lower()),
                None,
            )
            if titled:
                start, end = titled.start_line, titled.end_line
            elif end <= start:
                sec = next(
                    (s for s in concept.sections if s.start_line <= start <= s.end_line),
                    None,
                )
                if sec and sec.heading not in {"(preamble)", "(document)"}:
                    start, end = sec.start_line, sec.end_line
                else:
                    # Skip frontmatter: jump to first real heading section if nearby.
                    body_secs = [
                        s
                        for s in concept.sections
                        if s.heading not in {"(preamble)", "(document)"}
                    ]
                    if body_secs:
                        start, end = body_secs[0].start_line, body_secs[0].end_line
                    else:
                        end = min(n, start + 40)
        elif end <= start:
            sec = next(
                (s for s in concept.sections if s.start_line <= start <= s.end_line),
                None,
            )
            if sec:
                start, end = sec.start_line, sec.end_line
            else:
                end = min(n, start + 40)

        if not text or text.lstrip().startswith("@@"):
            text = "".join(concept.lines[start - 1 : end])
        return start, end, text

    def _parse_text(self, stdout: str, bundle: Bundle, top_k: int) -> list[Span]:
        spans: list[Span] = []
        for line in _strip_ansi(stdout).splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("Expanding"):
                continue
            token = line.split()[0].strip(",:")
            if token.startswith("qmd://"):
                path = self._normalize_path(token.split()[0], bundle)
            elif ":" in token:
                head, _, rest = token.partition(":")
                token = head if rest[:1].isdigit() else token
                path = self._normalize_path(token, bundle)
            else:
                path = self._normalize_path(token, bundle)
            if path and path in bundle.concepts:
                c = bundle.concepts[path]
                spans.append(
                    Span(
                        path=path,
                        start_line=1,
                        end_line=len(c.lines),
                        text="".join(c.lines)[:4000],
                    )
                )
            if len(spans) >= top_k:
                break
        return spans

    def _normalize_path(self, path: str, bundle: Bundle) -> str:
        path = path.strip().strip('"').strip("'")
        if path.startswith("qmd://"):
            rest = path[len("qmd://") :]
            if "/" in rest:
                rest = rest.split("/", 1)[1]
            path = rest
        path = path.lstrip("./")
        if path.startswith(str(bundle.root)):
            path = str(Path(path).relative_to(bundle.root))
        path = path.replace("\\", "/")
        if path in bundle.concepts:
            return path
        if not path.endswith(".md") and f"{path}.md" in bundle.concepts:
            return f"{path}.md"
        name = Path(path).name
        for p in bundle.concepts:
            if Path(p).name == name:
                return p
        return ""
