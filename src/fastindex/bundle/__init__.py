"""OKF bundle loading, frontmatter, and heading-based sectionization."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

RESERVED = frozenset({"index.md", "log.md", "_error.log"})
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


@dataclass(slots=True)
class Section:
    path: str
    start_line: int
    end_line: int
    heading: str
    level: int
    text: str


@dataclass(slots=True)
class Concept:
    path: str  # bundle-relative posix path
    type: str
    title: str
    description: str
    tags: list[str] = field(default_factory=list)
    when_to_use: str | None = None
    timestamp: str | None = None
    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    raw: str = ""
    lines: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    body_start_line: int = 1  # 1-based line of first body line


@dataclass(slots=True)
class DirNode:
    path: str  # "" for root, else relative dir
    index_md: str | None = None
    children_dirs: list[str] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Bundle:
    root: Path
    concepts: dict[str, Concept] = field(default_factory=dict)
    indexes: dict[str, str] = field(default_factory=dict)  # dir path -> index.md text
    tree: dict[str, DirNode] = field(default_factory=dict)


def _posix_rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def parse_frontmatter(raw: str) -> tuple[dict[str, Any], str, int]:
    """Return (frontmatter, body, body_start_line 1-based)."""
    m = FRONTMATTER_RE.match(raw)
    if not m:
        return {}, raw, 1
    fm = yaml.safe_load(m.group(1)) or {}
    if not isinstance(fm, dict):
        fm = {}
    body = raw[m.end() :]
    # Lines before body: frontmatter block
    preamble_lines = raw[: m.end()].count("\n")
    body_start = preamble_lines + 1
    if body.startswith("\n"):
        body = body[1:]
        body_start += 1
    return fm, body, body_start


def sectionize(path: str, lines: list[str], body_start_line: int) -> list[Section]:
    """Split file lines into sections by ATX headings. Line numbers are 1-based file lines."""
    # Map body-relative index -> absolute line
    sections: list[Section] = []
    # Work on full file lines for accurate numbering
    heading_idxs: list[tuple[int, int, str]] = []  # (line_idx0, level, title)
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m:
            heading_idxs.append((i, len(m.group(1)), m.group(2).strip()))

    if not heading_idxs:
        text = "".join(lines)
        if text.strip():
            sections.append(
                Section(
                    path=path,
                    start_line=1,
                    end_line=len(lines),
                    heading="(document)",
                    level=0,
                    text=text,
                )
            )
        return sections

    # Preamble before first heading
    first_i = heading_idxs[0][0]
    if first_i > 0:
        preamble = "".join(lines[:first_i])
        if preamble.strip():
            sections.append(
                Section(
                    path=path,
                    start_line=1,
                    end_line=first_i,
                    heading="(preamble)",
                    level=0,
                    text=preamble,
                )
            )

    for hi, (idx, level, title) in enumerate(heading_idxs):
        end_idx = heading_idxs[hi + 1][0] if hi + 1 < len(heading_idxs) else len(lines)
        chunk = "".join(lines[idx:end_idx])
        sections.append(
            Section(
                path=path,
                start_line=idx + 1,
                end_line=end_idx,
                heading=title,
                level=level,
                text=chunk,
            )
        )
    return sections


def _load_concept(root: Path, path: Path) -> Concept:
    rel = _posix_rel(root, path)
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines(keepends=True)
    if not lines and raw:
        lines = [raw]
    fm, body, body_start = parse_frontmatter(raw)
    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    concept = Concept(
        path=rel,
        type=str(fm.get("type") or ""),
        title=str(fm.get("title") or Path(rel).stem),
        description=str(fm.get("description") or ""),
        tags=[str(t) for t in tags],
        when_to_use=str(fm["when_to_use"]) if fm.get("when_to_use") else None,
        timestamp=str(fm["timestamp"]) if fm.get("timestamp") else None,
        frontmatter=fm,
        body=body,
        raw=raw,
        lines=lines,
        body_start_line=body_start,
    )
    concept.sections = sectionize(rel, lines, body_start)
    return concept


def load_bundle(root: str | Path) -> Bundle:
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Bundle root not found: {root}")

    bundle = Bundle(root=root)
    # Ensure root dir node
    bundle.tree[""] = DirNode(path="")

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        # Skip .fastindex
        try:
            path.relative_to(root / ".fastindex")
            continue
        except ValueError:
            pass
        if path.suffix.lower() != ".md":
            continue

        rel = _posix_rel(root, path)
        parent = str(Path(rel).parent)
        if parent == ".":
            parent = ""

        if parent not in bundle.tree:
            # create ancestor dirs
            parts = Path(rel).parent.parts
            acc = []
            for p in parts:
                acc.append(p)
                key = "/".join(acc)
                if key not in bundle.tree:
                    bundle.tree[key] = DirNode(path=key)
                    parent_key = "/".join(acc[:-1])
                    if parent_key not in bundle.tree:
                        bundle.tree[parent_key] = DirNode(path=parent_key)
                    if key not in bundle.tree[parent_key].children_dirs:
                        bundle.tree[parent_key].children_dirs.append(key)

        if path.name in RESERVED:
            text = path.read_text(encoding="utf-8")
            if path.name == "index.md":
                bundle.indexes[parent] = text
                bundle.tree[parent].index_md = text
            continue

        concept = _load_concept(root, path)
        bundle.concepts[rel] = concept
        if parent not in bundle.tree:
            bundle.tree[parent] = DirNode(path=parent)
        bundle.tree[parent].concepts.append(rel)

    # Sort children for stable traversal
    for node in bundle.tree.values():
        node.children_dirs.sort()
        node.concepts.sort()

    return bundle


def concept_search_text(concept: Concept) -> str:
    parts = [
        concept.path,
        concept.type,
        concept.title,
        concept.description,
        " ".join(concept.tags),
        concept.when_to_use or "",
    ]
    return "\n".join(p for p in parts if p)
