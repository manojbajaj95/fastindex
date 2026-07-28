"""Local Cognee KG baseline (Kuzu + LanceDB + SQLite). Optional extra: kg."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

from evals.baselines import StrategyConfig, StrategyConstraints, register
from evals.index import content_hash, fastindex_dir
from fastindex.bundle import Bundle, load_bundle
from fastindex.models import RemoteModel, estimate_cost_usd
from fastindex.types import RetrieveResult, RunStats, Span

DATASET = "fastindex_bundle"
PATH_RE = re.compile(r"FASTINDEX_PATH:\s*(\S+)")
META_NAME = "meta.json"


def cognee_root(bundle_root: Path) -> Path:
    return fastindex_dir(bundle_root) / "cognee"


def _embed_dims(embed_model: str) -> int:
    if "large" in embed_model:
        return 3072
    return 1536


def _configure_cognee(bundle_root: Path, model: RemoteModel) -> None:
    """Point Cognee at per-bundle local stores and lab model settings."""
    # Must set before importing heavy pipeline state; callers import cognee first.
    import cognee

    os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "false")
    os.environ.setdefault("CACHING", "false")
    os.environ.setdefault("COGNEE_SKIP_CONNECTION_TEST", "true")
    os.environ.setdefault("GRAPH_DATABASE_PROVIDER", "kuzu")
    os.environ.setdefault("VECTOR_DB_PROVIDER", "lancedb")
    os.environ.setdefault("DB_PROVIDER", "sqlite")

    root = cognee_root(bundle_root)
    sys_dir = root / "system"
    data_dir = root / "data"
    sys_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    cognee.config.system_root_directory(str(sys_dir.resolve()))
    cognee.config.data_root_directory(str(data_dir.resolve()))
    cognee.config.set_graph_database_provider("kuzu")
    cognee.config.set_vector_db_provider("lancedb")

    cognee.config.set_llm_provider("openai")
    cognee.config.set_llm_api_key(model.api_key)
    cognee.config.set_llm_model(model.chat_model)
    cognee.config.set_llm_endpoint(model.base_url)

    cognee.config.set_embedding_provider("openai")
    cognee.config.set_embedding_api_key(model.api_key)
    cognee.config.set_embedding_model(model.embed_model)
    cognee.config.set_embedding_dimensions(_embed_dims(model.embed_model))
    cognee.config.set_embedding_endpoint(model.base_url)


def _concept_doc(path: str, concept) -> str:
    body = "".join(concept.lines).strip()
    header = (
        f"FASTINDEX_PATH: {path}\n"
        f"title: {concept.title}\n"
        f"type: {concept.type}\n"
        f"description: {concept.description}\n"
    )
    if concept.tags:
        header += f"tags: {', '.join(concept.tags)}\n"
    if concept.when_to_use:
        header += f"when_to_use: {concept.when_to_use}\n"
    return f"{header}\n{body}\n"


def _read_meta(bundle_root: Path) -> dict | None:
    path = cognee_root(bundle_root) / META_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _write_meta(bundle_root: Path, meta: dict) -> None:
    root = cognee_root(bundle_root)
    root.mkdir(parents=True, exist_ok=True)
    (root / META_NAME).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def cognee_index_ready(bundle_root: Path, *, model: RemoteModel | None = None) -> bool:
    model = model or RemoteModel()
    meta = _read_meta(bundle_root)
    if not meta:
        return False
    return (
        meta.get("content_hash") == content_hash(bundle_root)
        and meta.get("chat_model") == model.chat_model
        and meta.get("embed_model") == model.embed_model
        and meta.get("dataset") == DATASET
    )


async def _rebuild_async(bundle_root: Path, model: RemoteModel) -> None:
    import cognee

    _configure_cognee(bundle_root, model)
    bundle = load_bundle(bundle_root)
    docs = [_concept_doc(p, c) for p, c in sorted(bundle.concepts.items())]
    if not docs:
        raise RuntimeError("No concepts to ingest into Cognee")

    await cognee.prune.prune_data()
    await cognee.prune.prune_system(metadata=True)
    await cognee.add(docs, dataset_name=DATASET)
    await cognee.cognify(datasets=DATASET)
    _write_meta(
        bundle_root,
        {
            "content_hash": content_hash(bundle_root),
            "chat_model": model.chat_model,
            "embed_model": model.embed_model,
            "dataset": DATASET,
            "n_docs": len(docs),
        },
    )


def ensure_cognee_index(bundle_root: Path, *, force: bool = False) -> bool:
    """Build local Cognee index if missing/stale. Returns True if ready."""
    model = RemoteModel()
    if not model.available:
        print("SKIP cognee: LLM credentials not configured", flush=True)
        return False
    try:
        import cognee  # noqa: F401
    except ImportError:
        print("SKIP cognee: install with `uv sync --extra kg`", flush=True)
        return False

    if not force and cognee_index_ready(bundle_root, model=model):
        return True

    print(f"Building Cognee KG index for {bundle_root} …", flush=True)
    asyncio.run(_rebuild_async(bundle_root, model))
    print("Cognee index ready", flush=True)
    return True


def _extract_path(text: str, bundle: Bundle) -> str | None:
    m = PATH_RE.search(text)
    if m:
        path = m.group(1).strip().rstrip(".,;")
        if path in bundle.concepts:
            return path
        if not path.endswith(".md") and f"{path}.md" in bundle.concepts:
            return f"{path}.md"
    # fallback: substring match against known paths
    for path in bundle.concepts:
        if path in text:
            return path
    return None


def _result_texts(results) -> list[str]:
    texts: list[str] = []
    if results is None:
        return texts
    if isinstance(results, str):
        return [results]
    if not isinstance(results, list):
        results = [results]
    for item in results:
        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, dict):
            t = item.get("text") or item.get("content") or item.get("payload")
            if t:
                texts.append(str(t))
            else:
                texts.append(json.dumps(item))
        else:
            t = getattr(item, "text", None) or getattr(item, "content", None)
            texts.append(str(t if t is not None else item))
    return texts


async def _search_async(query: str, bundle: Bundle, top_k: int, model: RemoteModel) -> list[Span]:
    import cognee
    from cognee import SearchType

    _configure_cognee(bundle.root, model)
    results = await cognee.search(
        query_text=query,
        query_type=SearchType.CHUNKS,
        datasets=[DATASET],
        top_k=top_k,
    )
    spans: list[Span] = []
    seen: set[str] = set()
    for text in _result_texts(results):
        path = _extract_path(text, bundle)
        if not path or path in seen:
            continue
        seen.add(path)
        concept = bundle.concepts[path]
        end = len(concept.lines) or 1
        spans.append(
            Span(
                path=path,
                start_line=1,
                end_line=end,
                text="".join(concept.lines)[:4000],
            )
        )
        if len(spans) >= top_k:
            break
    return spans


@register
class CogneeStrategy:
    """Open-track local KG via Cognee (embedded Kuzu/LanceDB/SQLite)."""

    name = "cognee"
    constraints = StrategyConstraints(
        requires_index=True,
        requires_embeddings=False,
        requires_llm=True,
        allows_cold=False,
        track="open",
    )

    def retrieve(self, query: str, bundle: Bundle, cfg: StrategyConfig) -> RetrieveResult:
        model = RemoteModel()
        if not model.available:
            raise RuntimeError("LLM credentials required for cognee")
        try:
            import cognee  # noqa: F401
        except ImportError as e:
            raise RuntimeError("cognee not installed; uv sync --extra kg") from e

        if not cognee_index_ready(bundle.root, model=model):
            asyncio.run(_rebuild_async(bundle.root, model))
            if not cognee_index_ready(bundle.root, model=model):
                raise RuntimeError("Cognee index build failed")

        spans = asyncio.run(_search_async(query, bundle, cfg.top_k, model))
        stats = RunStats(
            model_calls=0,  # Cognee owns LLM/embed calls internally
            input_tokens=0,
            output_tokens=0,
            estimated_cost_usd=estimate_cost_usd(0, 0, model.chat_model),
            track="warm",
            model_id=model.chat_model,
            extra={
                "embed_model": model.embed_model,
                "backend": "cognee+kuzu+lancedb",
                "search_type": "CHUNKS",
            },
        )
        return RetrieveResult(spans=spans, stats=stats)
