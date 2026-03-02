from __future__ import annotations

from types import SimpleNamespace

import pytest

from memsearch.resilience import async_retry, is_retryable_external_exception


@pytest.mark.asyncio
async def test_async_retry_succeeds_after_transient_failures(monkeypatch: pytest.MonkeyPatch):
    calls = {"n": 0}

    class ConnectError(Exception):
        pass

    async def _call():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectError("temporary")
        return "ok"

    async def _no_sleep(_seconds):  # noqa: ANN001
        return None

    monkeypatch.setattr("memsearch.resilience.asyncio.sleep", _no_sleep)
    out = await async_retry(
        operation_name="t",
        call=_call,
        is_retryable=is_retryable_external_exception,
        max_retries=3,
        retry_base_delay=0.0,
        retry_max_delay=0.0,
    )
    assert out == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_async_retry_does_not_retry_non_retryable():
    calls = {"n": 0}

    class ValidationError(Exception):
        pass

    async def _call():
        calls["n"] += 1
        raise ValidationError("bad input")

    with pytest.raises(ValidationError):
        await async_retry(
            operation_name="t",
            call=_call,
            is_retryable=is_retryable_external_exception,
            max_retries=3,
            retry_base_delay=0.0,
            retry_max_delay=0.0,
        )
    assert calls["n"] == 1


def test_retryable_by_status_code():
    exc = Exception("x")
    exc.response = SimpleNamespace(status_code=503)  # type: ignore[attr-defined]
    assert is_retryable_external_exception(exc) is True
