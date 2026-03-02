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


@pytest.mark.asyncio
async def test_api_reranker_retries_transient_errors(monkeypatch: pytest.MonkeyPatch):
    from memsearch.rerankers.api import APIReranker

    try:
        import httpx
    except ImportError:
        pytest.skip("httpx not installed")

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": [{"index": 0, "relevance_score": 0.8}]}

    class _Client:
        def __init__(self):
            self.calls = 0

        async def post(self, *args, **kwargs):  # noqa: ANN002, ANN003
            self.calls += 1
            if self.calls == 1:
                raise httpx.ConnectError("temporary connect issue")
            return _Resp()

    try:
        reranker = APIReranker(
            model="test-model",
            api_base="https://example.com/rerank",
            api_key="k",
        )
    except ImportError:
        pytest.skip("httpx not installed")

    fake_client = _Client()
    monkeypatch.setattr(reranker, "_client", fake_client)
    out = await reranker.rerank("apple", ["apple", "banana"], top_k=1)
    assert fake_client.calls == 2
    assert [r.index for r in out] == [0]


@pytest.mark.asyncio
async def test_api_reranker_retries_http_503(monkeypatch: pytest.MonkeyPatch):
    from memsearch.rerankers.api import APIReranker

    try:
        import httpx
    except ImportError:
        pytest.skip("httpx not installed")

    req = httpx.Request("POST", "https://example.com/rerank")

    class _Resp:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload
            self.request = req

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("status", request=self.request, response=self)

        def json(self):
            return self._payload

    class _Client:
        def __init__(self):
            self.calls = 0

        async def post(self, *args, **kwargs):  # noqa: ANN002, ANN003
            self.calls += 1
            if self.calls == 1:
                return _Resp(503, {})
            return _Resp(200, {"results": [{"index": 1, "relevance_score": 0.7}]})

    try:
        reranker = APIReranker(
            model="test-model",
            api_base="https://example.com/rerank",
            api_key="k",
            max_retries=2,
            retry_base_delay=0.0,
            retry_max_delay=0.0,
        )
    except ImportError:
        pytest.skip("httpx not installed")

    fake_client = _Client()
    monkeypatch.setattr(reranker, "_client", fake_client)
    out = await reranker.rerank("apple", ["a", "b"], top_k=1)
    assert fake_client.calls == 2
    assert [r.index for r in out] == [1]
