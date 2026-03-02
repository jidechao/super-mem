from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from memsearch.compact import compact_chunks


@pytest.mark.asyncio
async def test_compact_openai_retries_transient_errors(monkeypatch: pytest.MonkeyPatch):
    class APITimeoutError(Exception):
        pass

    class _FakeCompletions:
        def __init__(self):
            self.calls = 0

        async def create(self, **kwargs):  # noqa: ANN003
            self.calls += 1
            if self.calls == 1:
                raise APITimeoutError("temporary timeout")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok-summary"))]
            )

    fake_completions = _FakeCompletions()

    class _FakeOpenAIClient:
        def __init__(self, **kwargs):  # noqa: ANN003
            self.chat = SimpleNamespace(completions=fake_completions)

    fake_openai_module = SimpleNamespace(
        AsyncOpenAI=_FakeOpenAIClient,
        APITimeoutError=APITimeoutError,
    )
    monkeypatch.setitem(sys.modules, "openai", fake_openai_module)

    async def _no_sleep(_seconds):  # noqa: ANN001
        return None

    monkeypatch.setattr("memsearch.resilience.asyncio.sleep", _no_sleep)

    out = await compact_chunks(
        [{"content": "hello"}],
        llm_provider="openai",
        model="fake-model",
        max_retries=2,
        retry_base_delay=0.0,
        retry_max_delay=0.0,
    )
    assert out == "ok-summary"
    assert fake_completions.calls == 2
