from __future__ import annotations

import pytest

from memsearch.rerankers import RerankResult, get_reranker, register_reranker


class _CustomReranker:
    def __init__(self, model: str = "custom-model") -> None:
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    async def rerank(self, query: str, documents: list[str], *, top_k: int = 10):
        scored = []
        for idx, doc in enumerate(documents):
            score = 1.0 if query.lower() in doc.lower() else 0.0
            scored.append(RerankResult(index=idx, score=score))
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:top_k]


@pytest.mark.asyncio
async def test_register_and_get_custom_reranker():
    register_reranker("custom-test", _CustomReranker)
    reranker = get_reranker("custom-test", model="m1")
    results = await reranker.rerank("apple", ["banana", "apple pie"], top_k=1)

    assert reranker.model_name == "m1"
    assert len(results) == 1
    assert results[0].index == 1


def test_unknown_reranker_raises():
    with pytest.raises(ValueError, match="Unknown reranker"):
        get_reranker("does-not-exist")


@pytest.mark.asyncio
async def test_api_reranker_parses_response(monkeypatch: pytest.MonkeyPatch):
    from memsearch.rerankers.api import APIReranker

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.1},
                ]
            }

    class _Client:
        async def post(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return _Resp()

    try:
        reranker = APIReranker(
            model="test-model",
            api_base="https://example.com/rerank",
            api_key="k",
        )
    except ImportError:
        pytest.skip("httpx not installed")

    monkeypatch.setattr(reranker, "_client", _Client())
    out = await reranker.rerank("apple", ["apple", "banana"], top_k=2)
    assert [r.index for r in out] == [1, 0]
