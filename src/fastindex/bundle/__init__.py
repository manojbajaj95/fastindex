"""OKF bundle loading, frontmatter, and heading-based sectionization."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any

import yaml

RESERVED = frozenset({"index.md", "log.md", "_error.log"})
SKIP_DIRS = frozenset({
    ".fastindex", ".git", ".hg", ".svn", ".venv", "venv", "__pycache__",
    "node_modules", "dist", "build", ".next", ".nuxt", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".cache", ".tox", "coverage", ".coverage", "target",
})
PLAIN_CHUNK_LINES = 80
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
    _lazy: bool = False
    _lock: RLock = field(default_factory=RLock, repr=False, compare=False)

    def get_node(self, dir_path: str) -> DirNode | None:
        """Return a directory and its immediate children, loading it on demand."""
        if not self._lazy:
            return self.tree.get(dir_path)
        with self._lock:
            if dir_path in self.tree:
                return self.tree[dir_path]
        path = _safe_path(self.root, dir_path)
        if path is None or not path.is_dir():
            return None
        index_md = self.get_index(dir_path)
        with self._lock:
            if dir_path in self.tree:
                return self.tree[dir_path]
            node = DirNode(path=dir_path, index_md=index_md)
            for child in sorted(path.iterdir()):
                if child.is_symlink():
                    continue
                rel = _posix_rel(self.root, child)
                if child.is_dir():
                    if child.name not in SKIP_DIRS:
                        node.children_dirs.append(rel)
                elif child.is_file():
                    if child.name not in RESERVED and not child.name.startswith("."):
                        node.concepts.append(rel)
            self.tree[dir_path] = node
            return node

    def get_index(self, dir_path: str) -> str | None:
        if not self._lazy:
            return self.indexes.get(dir_path)
        with self._lock:
            if dir_path in self.indexes:
                return self.indexes[dir_path]
        directory = _safe_path(self.root, dir_path)
        if directory is None or not directory.is_dir():
            return None
        index_path = directory / "index.md"
        if index_path.is_symlink() or not index_path.is_file():
            return None
        raw = _read_text_file(index_path)
        if raw is None:
            return None
        with self._lock:
            return self.indexes.setdefault(dir_path, raw)

    def get_concept(self, path: str) -> Concept | None:
        """Read one selected file, including non-Markdown UTF-8 text files."""
        if not self._lazy:
            return self.concepts.get(path)
        with self._lock:
            cached = self.concepts.get(path)
        if cached is not None:
            return cached
        file_path = _safe_path(self.root, path)
        if file_path is None or not file_path.is_file():
            return None
        parent = str(Path(path).parent)
        if parent == ".":
            parent = ""
        node = self.get_node(parent)
        if node is None or path not in node.concepts:
            return None
        raw = _read_text_file(file_path)
        if raw is None:
            return None
        concept = _concept_from_raw(self.root, file_path, raw)
        with self._lock:
            return self.concepts.setdefault(path, concept)


def _safe_path(root: Path, rel: str) -> Path | None:
    """Reject traversal, generated directories, and symlinks before opening a path."""
    path = Path(rel)
    if path.is_absolute() or any(part in {"..", "."} for part in path.parts):
        return None
    current = root
    for part in path.parts:
        if part in SKIP_DIRS:
            return None
        current = current / part
        if current.is_symlink():
            return None
    return current


def _read_text_file(path: Path) -> str | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    return None if "\0" in raw else raw


def _posix_rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def parse_frontmatter(raw: str) -> tuple[dict[str, Any], str, int]:
    """Return (frontmatter, body, body_start_line 1-based)."""
    m = FRONTMATTER_RE.match(raw)
    if not m:
        return {}, raw, 1
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}, raw, 1
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


def _concept_from_raw(root: Path, path: Path, raw: str) -> Concept:
    rel = _posix_rel(root, path)
    lines = raw.splitlines(keepends=True)
    if not lines and raw:
        lines = [raw]
    is_markdown = path.suffix.lower() == ".md"
    fm, body, body_start = parse_frontmatter(raw) if is_markdown else ({}, raw, 1)
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
    if is_markdown:
        concept.sections = sectionize(rel, lines, body_start)
    else:
        concept.sections = [
            Section(
                path=rel,
                start_line=start + 1,
                end_line=min(start + PLAIN_CHUNK_LINES, len(lines)),
                heading=f"Lines {start + 1}–{min(start + PLAIN_CHUNK_LINES, len(lines))}",
                level=0,
                text="".join(lines[start : start + PLAIN_CHUNK_LINES]),
            )
            for start in range(0, len(lines), PLAIN_CHUNK_LINES)
        ]
    return concept


def _load_concept(root: Path, path: Path) -> Concept:
    return _concept_from_raw(root, path, path.read_text(encoding="utf-8"))


def load_lazy_bundle(root: str | Path) -> Bundle:
    """Open a repository without reading its tree or files until requested."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Bundle root not found: {root}")
    return Bundle(root=root, _lazy=True)


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
