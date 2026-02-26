from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from memsearch.memory.triggers import TriggerManager


@dataclass
class _Cfg:
    keywords: list[str] = field(default_factory=lambda: ["记住", "remember"])
    short_interval_seconds: int = 0
    long_interval_seconds: int = 0
    auto_consolidate: bool = False
    consolidation_days: int = 7


class _FakeMemoryManager:
    def __init__(self) -> None:
        self.short_calls: list[tuple[str, str]] = []
        self.consolidate_calls: list[int] = []

    async def write_short(self, content: str, **kwargs):  # noqa: ANN003
        self.short_calls.append((content, kwargs.get("source", "")))
        return Path("/tmp/short.md")

    async def consolidate(self, days: int = 7, *, force: bool = False):
        self.consolidate_calls.append(days)
        return {"topic": Path("/tmp/topic.md")}


@pytest.mark.asyncio
async def test_keyword_trigger_writes_short_memory():
    tm = TriggerManager(_Cfg())
    mm = _FakeMemoryManager()

    result = await tm.evaluate_and_execute("这段话请记住", mm)

    assert result.keyword_triggered is True
    assert result.matched_keyword == "记住"
    assert len(mm.short_calls) == 1
    assert mm.short_calls[0][1].startswith("keyword:")


@pytest.mark.asyncio
async def test_auto_consolidate_triggers_long_memory():
    tm = TriggerManager(
        _Cfg(auto_consolidate=True, long_interval_seconds=1, consolidation_days=3)
    )
    mm = _FakeMemoryManager()

    result = await tm.evaluate_and_execute("remember this", mm)

    assert result.keyword_triggered is True
    assert len(mm.short_calls) == 1
    assert mm.consolidate_calls == [3]
