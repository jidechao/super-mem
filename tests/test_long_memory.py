from __future__ import annotations

import json
from datetime import datetime

import pytest

from memsearch.memory.long_memory import LongMemoryManager
from memsearch.memory.short_memory import ShortMemoryManager


@pytest.mark.asyncio
async def test_consolidate_with_watermark_and_merge(tmp_path, monkeypatch: pytest.MonkeyPatch):
    short_mgr = ShortMemoryManager(tmp_path, "alice")
    await short_mgr.write(
        "我们决定统一使用 RESTful 路由。",
        timestamp=datetime.now(),
        source="manual",
    )

    async def fake_compact(chunks, **kwargs):  # noqa: ANN001
        combined = "\n".join(c["content"] for c in chunks)
        if "[已有记忆]" in combined:
            return (
                "# API设计决策\n\n"
                "> 最后更新: 2026-01-01\n\n"
                "## 关键决策\n"
                "- 统一使用 RESTful 路由\n\n"
                "## 来源\n"
                "- 2026-02-26\n"
            )

        return json.dumps(
            {
                "topics": [
                    {
                        "name": "API设计决策",
                        "content": "## 关键决策\n- 统一使用 RESTful 路由",
                        "sources": [datetime.now().date().isoformat()],
                    }
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("memsearch.memory.long_memory.compact_chunks", fake_compact)

    long_mgr = LongMemoryManager(
        tmp_path,
        "alice",
        short_mgr,
        llm_provider="openai",
    )

    written = await long_mgr.consolidate(days=7)
    assert "API设计决策" in written
    topic_text = long_mgr.read("API设计决策")
    assert "# API设计决策" in topic_text
    assert "RESTful" in topic_text

    # No new short files after watermark -> no-op
    written_again = await long_mgr.consolidate(days=7)
    assert written_again == {}


@pytest.mark.asyncio
async def test_long_memory_manual_write_and_topics(tmp_path):
    short_mgr = ShortMemoryManager(tmp_path, "alice")
    long_mgr = LongMemoryManager(tmp_path, "alice", short_mgr)

    path = await long_mgr.write("架构模式", "## 重要事实\n- 使用分层架构")
    assert path.exists()

    topics = long_mgr.list_topics()
    assert "架构模式" in topics
    assert "分层架构" in long_mgr.read("架构模式")
