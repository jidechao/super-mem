"""Reranker providers — protocol, registry, and factory."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable


@dataclass
class RerankResult:
    """Single reranked candidate."""

    index: int
    score: float


@runtime_checkable
class RerankerProvider(Protocol):
    """Minimal interface every reranker provider must satisfy."""

    @property
    def model_name(self) -> str: ...

    async def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_k: int = 10,
    ) -> list[RerankResult]: ...


_RERANKERS: dict[str, tuple[str, str]] = {
    "api": ("memsearch.rerankers.api", "APIReranker"),
    "cross-encoder": ("memsearch.rerankers.cross_encoder", "CrossEncoderReranker"),
}

_CUSTOM_RERANKERS: dict[str, type | Callable[..., RerankerProvider]] = {}

DEFAULT_RERANK_MODELS: dict[str, str] = {
    "api": "BAAI/bge-reranker-v2-m3",
    "cross-encoder": "cross-encoder/ms-marco-MiniLM-L-6-v2",
}


def register_reranker(
    name: str,
    provider_class: type | None = None,
    *,
    factory: Callable[..., RerankerProvider] | None = None,
    module_path: str | None = None,
    class_name: str | None = None,
) -> None:
    """Register a custom reranker provider.

    Supported forms:
    1) ``register_reranker("my", MyProvider)``
    2) ``register_reranker("my", factory=lambda **kw: MyProvider(**kw))``
    3) ``register_reranker("my", module_path="pkg.mod", class_name="MyProvider")``
    """
    if provider_class is not None:
        _CUSTOM_RERANKERS[name] = provider_class
        return

    if factory is not None:
        _CUSTOM_RERANKERS[name] = factory
        return

    if module_path and class_name:
        _RERANKERS[name] = (module_path, class_name)
        return

    raise ValueError(
        "Must provide provider_class, factory, or (module_path + class_name)"
    )


def get_reranker(
    name: str,
    *,
    model: str | None = None,
    **kwargs,
) -> RerankerProvider:
    """Instantiate a reranker provider by name."""
    resolved_model = model or DEFAULT_RERANK_MODELS.get(name, "")
    ctor_kwargs = dict(kwargs)
    if resolved_model:
        ctor_kwargs.setdefault("model", resolved_model)

    if name in _CUSTOM_RERANKERS:
        custom = _CUSTOM_RERANKERS[name]
        if isinstance(custom, type):
            return custom(**ctor_kwargs)
        return custom(**ctor_kwargs)

    if name not in _RERANKERS:
        available = sorted(set(_RERANKERS) | set(_CUSTOM_RERANKERS))
        raise ValueError(
            f"Unknown reranker {name!r}. Available: {available}. "
            "Use register_reranker() to add custom providers."
        )

    module_path, class_name = _RERANKERS[name]
    module = importlib.import_module(module_path)
    provider_cls = getattr(module, class_name)
    return provider_cls(**ctor_kwargs)


__all__ = [
    "DEFAULT_RERANK_MODELS",
    "RerankResult",
    "RerankerProvider",
    "get_reranker",
    "register_reranker",
]
