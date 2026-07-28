"""Product helpers: lint, generate-index, markdown links."""

from fastindex.utils.generate import generate_indexes, generate_section_synopses
from fastindex.utils.links import classify_wiki_targets, extract_md_links, resolve_href
from fastindex.utils.lint import LintIssue, lint_bundle

__all__ = [
    "LintIssue",
    "classify_wiki_targets",
    "extract_md_links",
    "generate_indexes",
    "generate_section_synopses",
    "lint_bundle",
    "resolve_href",
]
