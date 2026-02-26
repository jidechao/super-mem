"""Local cross-encoder reranker provider."""

from __future__ import annotations

import asyncio

from . import RerankResult


class CrossEncoderReranker:
    """SentenceTransformers CrossEncoder-based reranker."""

    def __init__(self, model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise ImportError(
                "CrossEncoderReranker requires 'sentence-transformers'. "
                "Install with: pip install \"memsearch[rerank-local]\""
            ) from exc

        self._model_name = model
        self._model = CrossEncoder(model)

    @property
    def model_name(self) -> str:
        return self._model_name

    async def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_k: int = 10,
    ) -> list[RerankResult]:
        if not documents:
            return []

        pairs = [[query, doc] for doc in documents]
        scores = await asyncio.to_thread(self._model.predict, pairs)
        ranked = sorted(
            enumerate(scores),
            key=lambda x: float(x[1]),
            reverse=True,
        )
        return [
            RerankResult(index=int(idx), score=float(score))
            for idx, score in ranked[:top_k]
        ]
