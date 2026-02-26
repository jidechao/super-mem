"""Trigger system for memory writes and consolidation."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol


class TriggerType(Enum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    KEYWORD = "keyword"


class KeywordTrigger:
    """Simple keyword trigger (case-insensitive contains check)."""

    def __init__(self, keywords: list[str]) -> None:
        self.keywords = [kw.strip() for kw in keywords if kw.strip()]

    def check(self, text: str) -> tuple[bool, str | None]:
        lowered = text.lower()
        for keyword in self.keywords:
            if keyword.lower() in lowered:
                return True, keyword
        return False, None


class ScheduledTrigger:
    """Interval-based trigger."""

    def __init__(self, interval_seconds: int = 3600) -> None:
        self.interval_seconds = interval_seconds
        self._last_trigger = 0.0

    def should_trigger(self) -> bool:
        if self.interval_seconds <= 0:
            return False
        now = time.time()
        if now - self._last_trigger < self.interval_seconds:
            return False
        self._last_trigger = now
        return True


class _MemoryExecutor(Protocol):
    async def write_short(self, content: str, **kwargs) -> Path | None: ...
    async def consolidate(self, days: int = 7, *, force: bool = False) -> dict[str, Path]: ...


@dataclass
class TriggerResult:
    keyword_triggered: bool = False
    matched_keyword: str | None = None
    short_memory_path: Path | None = None
    long_memory_paths: dict[str, Path] | None = None


class TriggerManager:
    """Coordinate keyword and schedule triggers."""

    def __init__(self, config) -> None:
        self.keyword_trigger = KeywordTrigger(getattr(config, "keywords", []))
        self.short_scheduled = ScheduledTrigger(
            getattr(config, "short_interval_seconds", 0)
        )
        self.long_scheduled = ScheduledTrigger(
            getattr(config, "long_interval_seconds", 0)
        )
        self.auto_consolidate = bool(getattr(config, "auto_consolidate", False))
        self.consolidation_days = int(getattr(config, "consolidation_days", 7))

    async def evaluate_and_execute(
        self,
        text: str | None,
        memory_manager: _MemoryExecutor,
    ) -> TriggerResult:
        """Evaluate triggers and execute matched actions."""
        result = TriggerResult()

        if text:
            hit, keyword = self.keyword_trigger.check(text)
            if hit:
                result.keyword_triggered = True
                result.matched_keyword = keyword
                result.short_memory_path = await memory_manager.write_short(
                    text,
                    source=f"keyword:{keyword}",
                )
                if self.auto_consolidate:
                    result.long_memory_paths = await memory_manager.consolidate(
                        days=self.consolidation_days
                    )
                return result

        if text and self.short_scheduled.should_trigger():
            result.short_memory_path = await memory_manager.write_short(
                text,
                source="scheduled:short",
            )

        if self.auto_consolidate and self.long_scheduled.should_trigger():
            result.long_memory_paths = await memory_manager.consolidate(
                days=self.consolidation_days
            )

        return result
