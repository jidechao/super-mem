"""Generic API-based reranker provider."""

from __future__ import annotations

import os
from typing import Any

from ..resilience import async_retry, is_retryable_external_exception
from . import RerankResult


class APIReranker:
    """Config-driven reranker for `/v1/rerank` style HTTP APIs."""

    def __init__(
        self,
        model: str = "BAAI/bge-reranker-v2-m3",
        *,
        api_base: str = "",
        api_key: str = "",
        api_key_env: str = "RERANK_API_KEY",
        top_k_field: str = "top_n",
        result_path: str = "results",
        score_field: str = "relevance_score",
        index_field: str = "index",
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        retry_base_delay: float = 0.2,
        retry_max_delay: float = 2.0,
    ) -> None:
        try:
            import httpx
        except ImportError as exc:
            raise ImportError(
                "APIReranker requires extra dependency 'httpx'. "
                "Install with: pip install \"memsearch[rerank]\""
            ) from exc

        self._model_name = model
        self._api_base = api_base or os.environ.get("RERANK_API_BASE", "")
        self._api_key = (
            api_key
            or os.environ.get(api_key_env, "")
            or os.environ.get("RERANK_API_KEY", "")
        )
        self._top_k_field = top_k_field
        self._result_path = result_path
        self._score_field = score_field
        self._index_field = index_field
        self._timeout_seconds = max(0.1, float(timeout_seconds))
        self._max_retries = max(1, int(max_retries))
        self._retry_base_delay = max(0.0, float(retry_base_delay))
        self._retry_max_delay = max(self._retry_base_delay, float(retry_max_delay))
        self._client = httpx.AsyncClient(timeout=self._timeout_seconds)

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
        if not self._api_base:
            raise ValueError("Reranker API base URL is empty. Set rerank.api_base.")

        payload: dict[str, Any] = {
            "model": self._model_name,
            "query": query,
            "documents": documents,
            self._top_k_field: top_k,
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        data = await self._post_with_retry(payload, headers)

        raw_results = self._resolve_path(data, self._result_path)
        if not isinstance(raw_results, list):
            raise ValueError(
                f"Invalid rerank response: expected list at '{self._result_path}'."
            )

        parsed: list[RerankResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            idx = item.get(self._index_field)
            score = item.get(self._score_field)
            try:
                idx_int = int(idx)
                score_float = float(score)
            except (TypeError, ValueError):
                continue
            if idx_int < 0 or idx_int >= len(documents):
                continue
            parsed.append(RerankResult(index=idx_int, score=score_float))

        parsed.sort(key=lambda x: x.score, reverse=True)
        return parsed[:top_k]

    async def close(self) -> None:
        await self._client.aclose()

    async def _post_with_retry(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        async def _call():
                resp = await self._client.post(self._api_base, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict):
                    return data
                raise ValueError("Invalid rerank response: expected a JSON object.")
        out = await async_retry(
            operation_name="rerank_api",
            call=_call,
            is_retryable=is_retryable_external_exception,
            max_retries=self._max_retries,
            retry_base_delay=self._retry_base_delay,
            retry_max_delay=self._retry_max_delay,
        )
        if not isinstance(out, dict):
            raise ValueError("Invalid rerank response: expected a JSON object.")
        return out

    @staticmethod
    def _resolve_path(obj: Any, path: str) -> Any:
        current = obj
        if not path:
            return current
        for part in path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current
