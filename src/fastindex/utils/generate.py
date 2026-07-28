"""Generate index.md files from concept frontmatter."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from fastindex.bundle import Bundle, load_bundle


def generate_indexes(root: str | Path, *, write: bool = True) -> dict[str, str]:
    """Regenerate index.md for each directory that has concepts or subdirs.

    Returns map of relative index path -> content.
    """
    root = Path(root).resolve()
    bundle = load_bundle(root)
    generated: dict[str, str] = {}

    by_dir: dict[str, list[str]] = defaultdict(list)
    for path in bundle.concepts:
        parent = str(Path(path).parent)
        if parent == ".":
            parent = ""
        by_dir[parent].append(path)

    dirs = set(by_dir) | set(bundle.tree)
    for dir_path in sorted(dirs, key=lambda d: (d.count("/"), d)):
        node = bundle.tree.get(dir_path)
        if node is None:
            continue
        concepts = sorted(by_dir.get(dir_path, []))
        child_dirs = sorted(node.children_dirs)
        if not concepts and not child_dirs:
            continue

        title = _dir_title(dir_path, root)
        lines = [f"# {title}", ""]
        if child_dirs:
            lines.append("## Directories")
            lines.append("")
            for cd in child_dirs:
                name = Path(cd).name
                desc = _dir_description(bundle, cd)
                if dir_path:
                    rel = Path(cd).relative_to(dir_path).as_posix() + "/"
                else:
                    rel = Path(cd).name + "/"
                lines.append(f"* [{name}]({rel}) — {desc}")
            lines.append("")
        if concepts:
            lines.append("## Concepts")
            lines.append("")
            for cpath in concepts:
                c = bundle.concepts[cpath]
                link = Path(cpath).name
                desc = c.description or c.title
                lines.append(f"* [{c.title}]({link}) — {desc}")
            lines.append("")

        content = "\n".join(lines).rstrip() + "\n"
        index_rel = f"{dir_path}/index.md" if dir_path else "index.md"
        generated[index_rel] = content
        if write:
            out = root / index_rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf-8")

    return generated


def generate_section_synopses(root: str | Path, *, write: bool = False) -> dict[str, str]:
    """Build a machine-readable synopsis map of section headings.

    Written to `.fastindex/section_synopses.md` when write=True (gitignored tree).
    """
    root = Path(root).resolve()
    bundle = load_bundle(root)
    lines = ["# Section synopses", ""]
    for path, concept in sorted(bundle.concepts.items()):
        lines.append(f"## {path}")
        lines.append("")
        lines.append(f"- title: {concept.title}")
        lines.append(f"- description: {concept.description}")
        if concept.when_to_use:
            lines.append(f"- when_to_use: {concept.when_to_use}")
        for sec in concept.sections:
            lines.append(f"- L{sec.start_line}-{sec.end_line} h{sec.level} {sec.heading}")
        lines.append("")
    content = "\n".join(lines)
    if write:
        out_dir = root / ".fastindex"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "section_synopses.md").write_text(content, encoding="utf-8")
    return {"section_synopses.md": content}


def _dir_title(dir_path: str, root: Path) -> str:
    if not dir_path:
        return root.name.replace("-", " ").replace("_", " ").title() or "Bundle"
    return Path(dir_path).name.replace("-", " ").replace("_", " ").title()


def _dir_description(bundle: Bundle, dir_path: str) -> str:
    for path, c in bundle.concepts.items():
        if path.startswith(dir_path.rstrip("/") + "/") or Path(path).parent.as_posix() == dir_path:
            if c.description:
                return c.description
    return f"{Path(dir_path).name} concepts"
