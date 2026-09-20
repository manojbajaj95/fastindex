"""Prepare an OKF-style directory index from an arbitrary text tree."""

from __future__ import annotations

import codecs
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from fastindex.bundle import RESERVED, SKIP_DIRS
from fastindex.models import ModelUsage, RemoteModel

CHUNK_CHARS = 10_000
SUMMARY_CHARS = 400
GROUP_SIZE = 8


@dataclass
class PrepareStats:
    """Counts and model usage from one preparation run."""

    written: int = 0
    preserved: int = 0
    files_summarized: int = 0
    files_skipped: int = 0
    skipped_paths: dict[str, str] = field(default_factory=dict)
    usage: ModelUsage = field(default_factory=ModelUsage)


def prepare(
    root: str | Path, *, model: RemoteModel | None = None, force: bool = False
) -> PrepareStats:
    """Write ``index.md`` in every visible source directory, bottom-up.

    Existing nonempty indexes are preserved unless ``force`` is true. Hidden
    files, common generated directories, and symlinks are outside the indexed
    tree. Text files are read fully in bounded chunks; binary/unreadable files
    are named in their directory index rather than silently represented as read.
    A failed model call stops preparation without writing that index.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Directory not found: {root}")
    model = model or RemoteModel(chat_model=os.environ.get("FASTINDEX_PREPARE_MODEL"))
    stats = PrepareStats()
    directories: list[Path] = []
    for dirpath, dirs, _ in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            name for name in dirs
            if name not in SKIP_DIRS
            and not (Path(dirpath) / name).is_symlink()
        )
        directories.append(Path(dirpath))
    directory_set = set(directories)
    if isinstance(model, RemoteModel) and not model.chat_model and any(
        force or not (directory / "index.md").is_file()
        or not (directory / "index.md").stat().st_size
        for directory in directories
    ):
        raise RuntimeError("Set FASTINDEX_PREPARE_MODEL or FASTINDEX_MODEL before preparation")

    # Child indexes must exist before their parent is summarized.
    for directory in reversed(directories):
        index = directory / "index.md"
        if index.is_symlink():
            raise ValueError(f"Refusing to follow index symlink: {index}")
        if index.is_file() and index.stat().st_size and not force:
            stats.preserved += 1
            continue

        child_dirs = sorted(
            path for path in directory.iterdir()
            if path.is_dir() and path in directory_set
        )
        files = sorted(
            path for path in directory.iterdir()
            if path.is_file() and not path.is_symlink() and not path.name.startswith(".")
            and path.name not in RESERVED
        )
        dir_entries: list[tuple[str, str]] = []
        file_entries: list[tuple[str, str]] = []
        skipped: list[tuple[str, str]] = []

        for child in child_dirs:
            summary = _summarize_file(
                child / "index.md", model, stats, subject=f"{child.name} directory"
            )
            dir_entries.append((child.name, summary))
        for path in files:
            try:
                summary = _summarize_file(path, model, stats)
            except (UnicodeError, OSError) as exc:
                reason = "binary or non-UTF-8" if isinstance(exc, UnicodeError) else "unreadable"
                stats.files_skipped += 1
                stats.skipped_paths[path.relative_to(root).as_posix()] = reason
                skipped.append((path.name, reason))
                continue
            stats.files_summarized += 1
            file_entries.append((path.name, summary))

        lines = [f"# {directory.name}", ""]
        if dir_entries:
            lines += ["## Directories", ""]
            lines += [f"- [{_label(name)}]({quote(name)}/) — {summary}"
                      for name, summary in dir_entries]
            lines.append("")
        if file_entries:
            lines += ["## Files", ""]
            lines += [f"- [{_label(name)}]({quote(name)}) — {summary}"
                      for name, summary in file_entries]
            lines.append("")
        if skipped:
            lines += ["## Skipped files", ""]
            lines += [f"- `{name}` — {reason}; contents were not summarized."
                      for name, reason in skipped]
            lines.append("")
        index.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        stats.written += 1
    return stats


def _label(name: str) -> str:
    return name.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _validate_text(path: Path) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")()
    with path.open("rb") as source:
        while data := source.read(64 * 1024):
            if b"\x00" in data:
                raise UnicodeError("NUL byte")
            decoder.decode(data)
    decoder.decode(b"", final=True)


def _text_chunks(path: Path):
    decoder = codecs.getincrementaldecoder("utf-8")()
    pending = ""
    with path.open("rb") as source:
        while data := source.read(64 * 1024):
            pending += decoder.decode(data)
            while len(pending) >= CHUNK_CHARS:
                boundary = max(
                    pending.rfind("\n", 0, CHUNK_CHARS),
                    pending.rfind(" ", 0, CHUNK_CHARS),
                )
                end = boundary + 1 if boundary >= CHUNK_CHARS // 2 else CHUNK_CHARS
                yield pending[:end]
                pending = pending[end:]
    pending += decoder.decode(b"", final=True)
    if pending:
        yield pending


def _summarize_file(
    path: Path, model: RemoteModel, stats: PrepareStats, *, subject: str | None = None
) -> str:
    _validate_text(path)
    subject = subject or path.name
    parts = [
        _ask(model, stats, f"Describe the contents of {subject} for a table of contents. "
             "Keep its stated when_to_use purpose, distinctive topics, and "
             "relationships to other files. For overviews, say what they enumerate. "
             f"Segment {number}:\n\n{chunk}")
        for number, chunk in enumerate(_text_chunks(path), 1)
    ]
    return _reduce(parts, model, stats, subject) if parts else "Empty file."


def _reduce(parts: list[str], model: RemoteModel, stats: PrepareStats, subject: str) -> str:
    while len(parts) > 1:
        parts = [
            _ask(model, stats, f"Combine these summaries of {subject} into one concise, "
                 "faithful summary. Preserve distinct search cues and coverage:\n\n"
                 + "\n".join(f"- {part}" for part in parts[i:i + GROUP_SIZE]))
            for i in range(0, len(parts), GROUP_SIZE)
        ]
    return parts[0]


def _ask(model: RemoteModel, stats: PrepareStats, prompt: str) -> str:
    messages = [
        {"role": "system", "content": (
            "Write one plain sentence describing contents for a directory table "
            "of contents. Preserve which questions this item answers, especially "
            "any when_to_use statement. State distinctive topics and useful "
            "relationships to other files. Ground it in the text; do not invent "
            "contents or tell "
            "the reader to open a path. No headings, lists, or metadata. "
            "Use at most 35 words and 300 characters."
        )},
        {"role": "user", "content": prompt},
    ]
    result = model.chat(messages)
    stats.usage.add(result.usage)
    summary = result.text.strip()
    if len(summary) > SUMMARY_CHARS:
        result = model.chat([
            messages[0],
            {"role": "user", "content": (
                "Shorten this to one plain sentence under 300 characters. "
                "Keep its distinct topics and useful relationships:\n\n" + summary
            )},
        ])
        stats.usage.add(result.usage)
        summary = result.text.strip()
    if not summary or len(summary) > SUMMARY_CHARS:
        raise ValueError("Model returned an empty or oversized preparation summary")
    return summary.replace("\n", " ")
