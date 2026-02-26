"""High-level memory manager facade."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .long_memory import LongMemoryManager
from .short_memory import ShortMemoryManager
from .triggers import TriggerManager, TriggerResult
from .user import resolve_user_id


@dataclass
class _FallbackMemoryConfig:
    base_dir: str = "memory"
    user_id: str = ""
    short_memory_dir: str = "short-memory"
    long_memory_dir: str = "long-memory"
    keywords: list[str] = field(default_factory=lambda: ["记住", "remember", "备忘"])
    short_interval_seconds: int = 0
    long_interval_seconds: int = 86400
    auto_consolidate: bool = False
    consolidation_days: int = 7


class MemoryManager:
    """Facade that combines user resolution, short/long memory, and triggers."""

    def __init__(
        self,
        base_dir: Path | str = "memory",
        user_id: str | None = None,
        *,
        config=None,
        llm_provider: str = "openai",
        llm_model: str | None = None,
    ) -> None:
        cfg = config or _FallbackMemoryConfig()
        self.base_dir = Path(base_dir)
        self.user_id = resolve_user_id(
            explicit=user_id,
            config_value=getattr(cfg, "user_id", ""),
        )

        self.short = ShortMemoryManager(
            self.base_dir,
            self.user_id,
            short_memory_dir=getattr(cfg, "short_memory_dir", "short-memory"),
        )
        self.long = LongMemoryManager(
            self.base_dir,
            self.user_id,
            self.short,
            llm_provider=llm_provider,
            llm_model=llm_model,
            long_memory_dir=getattr(cfg, "long_memory_dir", "long-memory"),
        )
        self.triggers = TriggerManager(cfg)

    async def write_short(self, content: str, **kwargs):
        return await self.short.write(content, **kwargs)

    async def write_long(self, topic: str, content: str) -> Path:
        return await self.long.write(topic, content)

    async def consolidate(self, days: int = 7, *, force: bool = False) -> dict[str, Path]:
        return await self.long.consolidate(days=days, force=force)

    async def on_input(self, text: str) -> TriggerResult:
        return await self.triggers.evaluate_and_execute(text, self)

    async def on_tick(self) -> TriggerResult:
        return await self.triggers.evaluate_and_execute(None, self)


__all__ = ["MemoryManager", "ShortMemoryManager", "LongMemoryManager", "TriggerManager"]
