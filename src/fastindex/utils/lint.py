"""OKF profile lint for knowledge bundles.

Checks required/recommended frontmatter and dangling markdown links
(concept bodies + indexes). Missing links are structure defects — fix in the
wiki, not via query-time soft-fail.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastindex.bundle import RESERVED, load_bundle, parse_frontmatter
from fastindex.utils.links import classify_wiki_targets, extract_md_links

REQUIRED = ("type", "title", "description")
RECOMMENDED = ("tags", "when_to_use", "timestamp")


@dataclass(slots=True)
class LintIssue:
    path: str
    level: str  # error | warning
    message: str


def lint_bundle(root: str | Path) -> list[LintIssue]:
    root = Path(root).resolve()
    issues: list[LintIssue] = []
    bundle = load_bundle(root)
    concept_paths = set(bundle.concepts)
    index_dirs = set(bundle.indexes)
    tree_dirs = set(bundle.tree)

    for path in sorted(root.rglob("*.md")):
        if ".fastindex" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        if path.name in RESERVED:
            continue
        raw = path.read_text(encoding="utf-8")
        fm, _, _ = parse_frontmatter(raw)
        if not fm:
            issues.append(LintIssue(rel, "error", "missing YAML frontmatter"))
            continue
        for key in REQUIRED:
            val = fm.get(key)
            if val is None or (isinstance(val, str) and not val.strip()):
                issues.append(LintIssue(rel, "error", f"missing required field: {key}"))
        for key in RECOMMENDED:
            if key not in fm or fm.get(key) in (None, "", []):
                issues.append(LintIssue(rel, "warning", f"missing recommended field: {key}"))

        base = str(Path(rel).parent)
        if base == ".":
            base = ""
        _valid, dangling = classify_wiki_targets(
            extract_md_links(raw, base),
            concept_paths=concept_paths,
            index_dirs=index_dirs,
            tree_dirs=tree_dirs,
        )
        for target in dangling:
            issues.append(LintIssue(rel, "error", f"dangling link: {target}"))

    for dir_path, index_text in bundle.indexes.items():
        rel = f"{dir_path}/index.md" if dir_path else "index.md"
        _valid, dangling = classify_wiki_targets(
            extract_md_links(index_text, dir_path),
            concept_paths=concept_paths,
            index_dirs=index_dirs,
            tree_dirs=tree_dirs,
        )
        for target in dangling:
            issues.append(LintIssue(rel, "error", f"dangling link: {target}"))

    return issues
