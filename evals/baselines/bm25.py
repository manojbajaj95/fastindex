"""Cold BM25 / FTS lexical retrieval over path, frontmatter, and sections."""

from __future__ import annotations

import re
from collections import Counter

from rank_bm25 import BM25Okapi

from evals.baselines import StrategyConfig, StrategyConstraints, register
from evals.index import is_fresh, load_bm25_docs
from fastindex.bundle import Bundle, concept_search_text
from fastindex.types import RetrieveResult, RunStats, Span

_TOKEN = re.compile(r"[a-z0-9_]+", re.I)


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


def _docs_from_bundle(bundle: Bundle) -> list[dict]:
    docs: list[dict] = []
    for concept in bundle.iter_concepts():
        path = concept.path
        meta_text = concept_search_text(concept)
        docs.append(
            {
                "path": path,
                "start_line": 1,
                "end_line": max(1, len(concept.lines)),
                "text": meta_text + "\n" + concept.body,
                "heading": "(concept)",
            }
        )
        for sec in concept.sections:
            docs.append(
                {
                    "path": path,
                    "start_line": sec.start_line,
                    "end_line": sec.end_line,
                    "text": f"{concept.title}\n{concept.description}\n{sec.heading}\n{sec.text}",
                    "heading": sec.heading,
                }
            )
    return docs


class _Bm25Base:
    constraints = StrategyConstraints(allows_cold=True, track="vectorless")

    def retrieve(self, query: str, bundle: Bundle, cfg: StrategyConfig) -> RetrieveResult:
        track = "cold"
        docs = None
        if is_fresh(bundle.root):
            cached = load_bm25_docs(bundle.root)
            if cached is not None:
                docs = cached
                track = "warm"
        if docs is None:
            docs = _docs_from_bundle(bundle)

        corpus_tokens = [tokenize(d["text"]) for d in docs]
        if not corpus_tokens:
            return RetrieveResult(spans=[], stats=RunStats(track=track))

        if self.name == "fts":
            spans = self._fts(query, docs, cfg.top_k)
        else:
            bm25 = BM25Okapi(corpus_tokens)
            q_tokens = tokenize(query)
            scores = bm25.get_scores(q_tokens)
            ranked = sorted(range(len(docs)), key=lambda i: scores[i], reverse=True)[: cfg.top_k]
            spans = []
            for i in ranked:
                if scores[i] <= 0 and self.name == "bm25":
                    continue
                d = docs[i]
                spans.append(
                    Span(
                        path=d["path"],
                        start_line=int(d["start_line"]),
                        end_line=int(d["end_line"]),
                        text=d["text"][:4000],
                        score=float(scores[i]),
                    )
                )
            if not spans and ranked:
                # fallback: top by score even if zero
                for i in ranked[: cfg.top_k]:
                    d = docs[i]
                    spans.append(
                        Span(
                            path=d["path"],
                            start_line=int(d["start_line"]),
                            end_line=int(d["end_line"]),
                            text=d["text"][:4000],
                            score=float(scores[i]),
                        )
                    )

        return RetrieveResult(spans=spans, stats=RunStats(track=track))

    def _fts(self, query: str, docs: list[dict], top_k: int) -> list[Span]:
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        scored: list[tuple[float, dict]] = []
        for d in docs:
            tokens = tokenize(d["text"])
            counts = Counter(tokens)
            score = sum(counts[t] for t in q_tokens)
            # path boost
            path_tokens = tokenize(d["path"])
            score += 2 * sum(1 for t in q_tokens if t in path_tokens)
            if score > 0:
                scored.append((float(score), d))
        scored.sort(key=lambda x: x[0], reverse=True)
        spans = []
        for score, d in scored[:top_k]:
            spans.append(
                Span(
                    path=d["path"],
                    start_line=int(d["start_line"]),
                    end_line=int(d["end_line"]),
                    text=d["text"][:4000],
                    score=score,
                )
            )
        return spans


@register
class Bm25Strategy(_Bm25Base):
    name = "bm25"


@register
class FtsStrategy(_Bm25Base):
    name = "fts"
