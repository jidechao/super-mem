"""Shared resilience helpers (retry/backoff + retryable error classification)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

DEFAULT_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
DEFAULT_RETRYABLE_EXCEPTION_NAMES = {
    "APITimeoutError",
    "APIConnectionError",
    "RateLimitError",
    "InternalServerError",
    "ServiceUnavailableError",
    "OverloadedError",
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "RemoteProtocolError",
    "ServiceUnavailable",
    "TooManyRequests",
    "DeadlineExceeded",
}


def exception_status_code(exc: Exception) -> int | None:
    """Best-effort status code extraction from heterogeneous SDK exceptions."""
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    response = getattr(exc, "response", None)
    if response is None:
        return None
    resp_code = getattr(response, "status_code", None)
    return resp_code if isinstance(resp_code, int) else None


def is_retryable_external_exception(
    exc: Exception,
    *,
    extra_retryable_names: set[str] | None = None,
    retryable_status_codes: set[int] | None = None,
) -> bool:
    """Classify transient external-call failures that should be retried."""
    status_codes = retryable_status_codes or DEFAULT_RETRYABLE_STATUS_CODES
    status_code = exception_status_code(exc)
    if status_code in status_codes:
        return True

    retryable_names = set(DEFAULT_RETRYABLE_EXCEPTION_NAMES)
    if extra_retryable_names:
        retryable_names.update(extra_retryable_names)
    return exc.__class__.__name__ in retryable_names


async def async_retry(
    *,
    operation_name: str,
    call: Callable[[], Awaitable],
    is_retryable: Callable[[Exception], bool],
    max_retries: int = 3,
    retry_base_delay: float = 0.2,
    retry_max_delay: float = 2.0,
) -> object:
    """Retry an async operation with exponential backoff."""
    retries = max(1, int(max_retries))
    base_delay = max(0.0, float(retry_base_delay))
    max_delay = max(base_delay, float(retry_max_delay))

    for attempt in range(1, retries + 1):
        try:
            return await call()
        except Exception as exc:
            if attempt >= retries or not is_retryable(exc):
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            logger.warning(
                "event=external_retry operation=%s attempt=%d/%d delay_s=%.2f error_type=%s",
                operation_name,
                attempt,
                retries,
                delay,
                exc.__class__.__name__,
            )
            await asyncio.sleep(delay)

    raise RuntimeError("Unexpected retry state")

