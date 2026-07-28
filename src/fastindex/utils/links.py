"""Markdown link extraction and resolution for OKF wiki pages."""

from __future__ import annotations

import re
from pathlib import Path

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def resolve_href(base_dir: str, href: str) -> str | None:
    """Resolve a markdown href to a bundle-relative path (no leading slash)."""
    href = href.strip()
    if not href or href.startswith(("http://", "https://", "mailto:", "#")):
        return None
    href = href.split("#", 1)[0].strip()
    if not href:
        return None
    if href.startswith("/"):
        target = href.lstrip("/")
    else:
        base = Path(base_dir) if base_dir else Path(".")
        target = (base / href).as_posix()
    if target.startswith("./"):
        target = target[2:]
    while "/../" in target or target.startswith("../"):
        parts = Path(target).parts
        out: list[str] = []
        for p in parts:
            if p == "..":
                if out:
                    out.pop()
            elif p != ".":
                out.append(p)
        target = "/".join(out)
    return target


def extract_md_links(text: str, base_dir: str) -> list[str]:
    """Extract resolved relative markdown link targets (order preserved, unique)."""
    seen: set[str] = set()
    out: list[str] = []
    for _label, href in _MD_LINK_RE.findall(text):
        resolved = resolve_href(base_dir, href)
        if not resolved or resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def classify_wiki_targets(
    targets: list[str],
    *,
    concept_paths: set[str],
    index_dirs: set[str],
    tree_dirs: set[str],
) -> tuple[list[str], list[str]]:
    """Split resolved hrefs into existing wiki targets vs dangling.

    Dangling links are a structural defect (LLM-Wiki Error Book / lint Discover).
    Callers must not invent replacements at query time.
    """
    valid: list[str] = []
    dangling: list[str] = []
    seen_v: set[str] = set()
    seen_d: set[str] = set()
    for link in targets:
        cand = link if link.endswith(".md") else f"{link}.md"
        dir_key = link.rstrip("/")
        if cand in concept_paths:
            if cand not in seen_v:
                seen_v.add(cand)
                valid.append(cand)
        elif _is_index_path(link) or link in index_dirs or dir_key in tree_dirs:
            key = link if _is_index_path(link) else dir_key
            if key not in seen_v:
                seen_v.add(key)
                valid.append(key)
        else:
            if link not in seen_d:
                seen_d.add(link)
                dangling.append(link)
    return valid, dangling


def _is_index_path(path: str) -> bool:
    return path == "index.md" or path.endswith("/index.md")
