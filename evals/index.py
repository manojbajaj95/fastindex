"""Optional `.fastindex/` sidecars with content-hash freshness (evals only).

Used by warm baselines (bm25 / vsearch / cognee). Product tree-reason is cold
and never reads these files. Bench auto-builds when a baseline needs them.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastindex.bundle import concept_search_text, load_bundle
from fastindex.models import RemoteModel

META_NAME = "meta.json"
BM25_NAME = "bm25.json"
EMB_NAME = "embeddings.json"


def fastindex_dir(root: Path) -> Path:
    return root / ".fastindex"


def content_hash(root: Path) -> str:
    """Hash all concept markdown (and index.md) under the bundle."""
    h = hashlib.sha256()
    root = root.resolve()
    paths = sorted(
        p
        for p in root.rglob("*.md")
        if ".fastindex" not in p.parts and not any(part.startswith(".") for part in p.parts)
    )
    for path in paths:
        rel = path.relative_to(root).as_posix()
        h.update(rel.encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


@dataclass
class IndexMeta:
    content_hash: str
    bm25: bool = False
    embeddings: bool = False
    embed_model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "content_hash": self.content_hash,
            "bm25": self.bm25,
            "embeddings": self.embeddings,
            "embed_model": self.embed_model,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IndexMeta:
        return cls(
            content_hash=str(d.get("content_hash") or ""),
            bm25=bool(d.get("bm25")),
            embeddings=bool(d.get("embeddings")),
            embed_model=d.get("embed_model"),
        )


def read_meta(root: Path) -> IndexMeta | None:
    path = fastindex_dir(root) / META_NAME
    if not path.exists():
        return None
    return IndexMeta.from_dict(json.loads(path.read_text(encoding="utf-8")))


def is_fresh(root: Path) -> bool:
    meta = read_meta(root)
    if meta is None:
        return False
    return meta.content_hash == content_hash(root)


def build_index(
    root: str | Path,
    *,
    force: bool = False,
    with_embeddings: bool = True,
    model: RemoteModel | None = None,
) -> IndexMeta:
    root = Path(root).resolve()
    bundle = load_bundle(root)
    digest = content_hash(root)
    out = fastindex_dir(root)
    out.mkdir(parents=True, exist_ok=True)

    meta = read_meta(root)
    if meta and meta.content_hash == digest and not force and meta.bm25:
        if not with_embeddings or meta.embeddings:
            return meta

    docs: list[dict[str, Any]] = []
    for path, concept in sorted(bundle.concepts.items()):
        docs.append(
            {
                "id": f"{path}#meta",
                "path": path,
                "start_line": 1,
                "end_line": max(1, len(concept.lines)),
                "text": concept_search_text(concept) + "\n" + concept.body[:2000],
                "heading": "(concept)",
            }
        )
        for sec in concept.sections:
            docs.append(
                {
                    "id": f"{path}#L{sec.start_line}-{sec.end_line}",
                    "path": path,
                    "start_line": sec.start_line,
                    "end_line": sec.end_line,
                    "text": f"{concept.title}\n{concept.description}\n{sec.heading}\n{sec.text}",
                    "heading": sec.heading,
                }
            )

    (out / BM25_NAME).write_text(json.dumps({"docs": docs}, indent=2), encoding="utf-8")

    emb_ok = False
    embed_model = None
    if with_embeddings:
        model = model or RemoteModel()
        if model.available:
            texts = [d["text"][:8000] for d in docs]
            vectors: list[list[float]] = []
            batch_size = 64
            for i in range(0, len(texts), batch_size):
                batch = texts[i : i + batch_size]
                vecs, _usage = model.embed(batch)
                vectors.extend(vecs)
            payload = {
                "model": model.embed_model,
                "ids": [d["id"] for d in docs],
                "vectors": vectors,
            }
            (out / EMB_NAME).write_text(json.dumps(payload), encoding="utf-8")
            emb_ok = True
            embed_model = model.embed_model

    meta = IndexMeta(
        content_hash=digest,
        bm25=True,
        embeddings=emb_ok,
        embed_model=embed_model,
    )
    (out / META_NAME).write_text(json.dumps(meta.to_dict(), indent=2), encoding="utf-8")
    return meta


def load_bm25_docs(root: Path) -> list[dict[str, Any]] | None:
    path = fastindex_dir(root) / BM25_NAME
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["docs"]


def load_embeddings(root: Path) -> tuple[list[str], list[list[float]], str] | None:
    path = fastindex_dir(root) / EMB_NAME
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["ids"], data["vectors"], data.get("model") or ""


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
