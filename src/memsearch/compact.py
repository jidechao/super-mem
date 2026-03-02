"""Memory compact — compress and summarize chunks using an LLM.

Supports OpenAI (default), Anthropic, and Gemini as LLM backends.
API keys are read from environment variables:
    OPENAI_API_KEY / OPENAI_BASE_URL
    ANTHROPIC_API_KEY
    GOOGLE_API_KEY
"""

from __future__ import annotations

import os
from typing import Any

from .resilience import async_retry, is_retryable_external_exception

COMPACT_PROMPT = """\
You are a knowledge compression assistant. Given the following chunks of text \
from a knowledge base, create a concise but comprehensive summary that preserves \
all key facts, decisions, code patterns, and actionable insights.

Chunks:
{chunks}

Write a clear, well-structured markdown summary. Use headings and bullet points. \
Preserve technical details, code snippets, and specific decisions."""


async def compact_chunks(
    chunks: list[dict[str, Any]],
    *,
    llm_provider: str = "openai",
    model: str | None = None,
    prompt_template: str | None = None,
    timeout_seconds: float = 30.0,
    max_retries: int = 3,
    retry_base_delay: float = 0.2,
    retry_max_delay: float = 2.0,
) -> str:
    """Compress *chunks* into a summary using an LLM.

    Parameters
    ----------
    chunks:
        List of chunk dicts (must contain ``"content"`` key).
    llm_provider:
        One of ``"openai"``, ``"anthropic"``, ``"gemini"``.
    model:
        Override the default model for the provider.
    prompt_template:
        Custom prompt template.  Must contain ``{chunks}`` placeholder.
        Defaults to the built-in ``COMPACT_PROMPT``.

    Returns
    -------
    str
        The compressed summary markdown.
    """
    combined = "\n\n---\n\n".join(c["content"] for c in chunks)
    template = prompt_template or COMPACT_PROMPT
    prompt = template.format(chunks=combined)

    if llm_provider == "openai":
        return await _compact_openai(
            prompt,
            model or "gpt-4o-mini",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
            retry_max_delay=retry_max_delay,
        )
    elif llm_provider == "anthropic":
        return await _compact_anthropic(
            prompt,
            model or "claude-sonnet-4-5-20250929",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
            retry_max_delay=retry_max_delay,
        )
    elif llm_provider == "gemini":
        return await _compact_gemini(
            prompt,
            model or "gemini-2.0-flash",
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
            retry_max_delay=retry_max_delay,
        )
    else:
        raise ValueError(
            f"Unknown LLM provider {llm_provider!r}. "
            f"Available: openai, anthropic, gemini"
        )


async def _compact_openai(
    prompt: str,
    model: str,
    *,
    timeout_seconds: float = 30.0,
    max_retries: int = 3,
    retry_base_delay: float = 0.2,
    retry_max_delay: float = 2.0,
) -> str:
    import openai

    kwargs: dict = {}
    base_url = os.environ.get("OPENAI_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url

    client = openai.AsyncOpenAI(timeout=timeout_seconds, **kwargs)

    async def _call():
        return await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )

    resp = await async_retry(
        operation_name="compact_openai",
        call=_call,
        is_retryable=is_retryable_external_exception,
        max_retries=max_retries,
        retry_base_delay=retry_base_delay,
        retry_max_delay=retry_max_delay,
    )
    return resp.choices[0].message.content or ""


async def _compact_anthropic(
    prompt: str,
    model: str,
    *,
    timeout_seconds: float = 30.0,
    max_retries: int = 3,
    retry_base_delay: float = 0.2,
    retry_max_delay: float = 2.0,
) -> str:
    import anthropic

    client = anthropic.AsyncAnthropic(timeout=timeout_seconds)  # reads ANTHROPIC_API_KEY

    async def _call():
        return await client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

    resp = await async_retry(
        operation_name="compact_anthropic",
        call=_call,
        is_retryable=is_retryable_external_exception,
        max_retries=max_retries,
        retry_base_delay=retry_base_delay,
        retry_max_delay=retry_max_delay,
    )
    return resp.content[0].text


async def _compact_gemini(
    prompt: str,
    model: str,
    *,
    max_retries: int = 3,
    retry_base_delay: float = 0.2,
    retry_max_delay: float = 2.0,
) -> str:
    from google import genai

    client = genai.Client()  # reads GOOGLE_API_KEY

    async def _call():
        return await client.aio.models.generate_content(
            model=model,
            contents=prompt,
        )

    resp = await async_retry(
        operation_name="compact_gemini",
        call=_call,
        is_retryable=is_retryable_external_exception,
        max_retries=max_retries,
        retry_base_delay=retry_base_delay,
        retry_max_delay=retry_max_delay,
    )
    return resp.text or ""
