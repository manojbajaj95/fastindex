"""Vector search over `.fastindex/` embeddings (open track)."""

from __future__ import annotations

from evals.baselines import StrategyConfig, StrategyConstraints, register
from evals.index import cosine, is_fresh, load_bm25_docs, load_embeddings
from fastindex.bundle import Bundle
from fastindex.models import RemoteModel, estimate_cost_usd
from fastindex.types import RetrieveResult, RunStats, Span


@register
class VSearchStrategy:
    name = "vsearch"
    constraints = StrategyConstraints(
        requires_index=True,
        requires_embeddings=True,
        requires_llm=False,
        allows_cold=False,
        track="open",
    )

    def retrieve(self, query: str, bundle: Bundle, cfg: StrategyConfig) -> RetrieveResult:
        if not is_fresh(bundle.root):
            raise RuntimeError(
                "Embeddings index missing or stale. Re-run evals.bench "
                "(auto-builds) or call evals.index.build_index."
            )
        emb = load_embeddings(bundle.root)
        docs = load_bm25_docs(bundle.root)
        if emb is None or docs is None:
            raise RuntimeError(
                "Embeddings not built. Re-run evals.bench or evals.index.build_index."
            )

        ids, vectors, model_id = emb
        id_to_doc = {d["id"]: d for d in docs}
        model = RemoteModel()
        q_vecs, usage = model.embed([query])
        qv = q_vecs[0]
        scored: list[tuple[float, str]] = []
        for doc_id, vec in zip(ids, vectors, strict=True):
            scored.append((cosine(qv, vec), doc_id))
        scored.sort(key=lambda x: x[0], reverse=True)

        spans: list[Span] = []
        for score, doc_id in scored[: cfg.top_k]:
            d = id_to_doc.get(doc_id)
            if not d:
                continue
            spans.append(
                Span(
                    path=d["path"],
                    start_line=int(d["start_line"]),
                    end_line=int(d["end_line"]),
                    text=d["text"][:4000],
                    score=score,
                )
            )

        stats = RunStats(
            model_calls=usage.calls,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            estimated_cost_usd=estimate_cost_usd(
                usage.input_tokens, usage.output_tokens, usage.model_id
            ),
            track="warm",
            model_id=model_id or usage.model_id,
        )
        return RetrieveResult(spans=spans, stats=stats)
