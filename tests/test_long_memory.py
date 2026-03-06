from __future__ import annotations

import json
from datetime import date, datetime, time

import pytest

from memsearch.memory.long_memory import LongMemoryManager
from memsearch.memory.short_memory import ShortMemoryManager


@pytest.mark.asyncio
async def test_consolidate_with_watermark_and_merge(tmp_path, monkeypatch: pytest.MonkeyPatch):
    short_mgr = ShortMemoryManager(tmp_path, "alice")
    await short_mgr.write(
        "Service uses RESTful APIs.",
        timestamp=datetime.now(),
        source="manual",
    )

    async def fake_compact(chunks, **kwargs):  # noqa: ANN001
        combined = "\n".join(c["content"] for c in chunks)
        if "[existing-memory]" in combined:
            return (
                "# API topic\n\n"
                "> Last update: 2026-01-01\n\n"
                "## Summary\n"
                "- Service uses RESTful APIs.\n\n"
                "## Sources\n"
                "- 2026-02-26\n"
            )

        return json.dumps(
            {
                "topics": [
                    {
                        "name": "API topic",
                        "content": "## Summary\n- Service uses RESTful APIs.",
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
    assert "API topic" in written
    topic_text = long_mgr.read("API topic")
    assert "# API topic" in topic_text
    assert "RESTful" in topic_text

    written_again = await long_mgr.consolidate(days=7)
    assert written_again == {}


@pytest.mark.asyncio
async def test_long_memory_manual_write_and_topics(tmp_path):
    short_mgr = ShortMemoryManager(tmp_path, "alice")
    long_mgr = LongMemoryManager(tmp_path, "alice", short_mgr)

    path = await long_mgr.write("architecture", "## Notes\n- API uses a gateway")
    assert path.exists()

    topics = long_mgr.list_topics()
    assert "architecture" in topics
    assert "gateway" in long_mgr.read("architecture")


@pytest.mark.asyncio
async def test_consolidate_reprocesses_same_day_file_after_watermark(tmp_path, monkeypatch: pytest.MonkeyPatch):
    short_mgr = ShortMemoryManager(tmp_path, "alice")
    today = date.today()
    await short_mgr.write("first topic detail", timestamp=datetime.combine(today, time(9, 0)), source="manual")

    calls: list[str] = []

    async def fake_compact(chunks, **kwargs):  # noqa: ANN001
        combined = "\n".join(c["content"] for c in chunks)
        calls.append(combined)
        return json.dumps(
            {
                "topics": [
                    {
                        "name": "same-day-topic",
                        "content": f"## Summary\n- {combined}",
                        "sources": [date.today().isoformat()],
                    }
                ]
            }
        )

    monkeypatch.setattr("memsearch.memory.long_memory.compact_chunks", fake_compact)

    long_mgr = LongMemoryManager(tmp_path, "alice", short_mgr, llm_provider="openai")
    first_written = await long_mgr.consolidate(days=7)
    assert "same-day-topic" in first_written

    await short_mgr.write("second topic detail", timestamp=datetime.combine(today, time(18, 0)), source="manual")

    second_written = await long_mgr.consolidate(days=7)
    assert "same-day-topic" in second_written
    assert len(calls) == 3
    assert "second topic detail" in calls[1]



@pytest.mark.asyncio
async def test_consolidate_picks_up_new_backdated_file_after_watermark(tmp_path, monkeypatch: pytest.MonkeyPatch):
    short_mgr = ShortMemoryManager(tmp_path, "alice")
    today = date.today()
    await short_mgr.write("current-day detail", timestamp=datetime.combine(today, time(9, 0)), source="manual")

    calls: list[str] = []

    async def fake_compact(chunks, **kwargs):  # noqa: ANN001
        combined = "\n".join(c["content"] for c in chunks)
        calls.append(combined)
        return json.dumps(
            {
                "topics": [
                    {
                        "name": "history-topic",
                        "content": f"## Summary\n- {combined}",
                        "sources": [today.isoformat()],
                    }
                ]
            }
        )

    monkeypatch.setattr("memsearch.memory.long_memory.compact_chunks", fake_compact)

    long_mgr = LongMemoryManager(tmp_path, "alice", short_mgr, llm_provider="openai")
    first_written = await long_mgr.consolidate(days=7)
    assert "history-topic" in first_written

    imported_day = today.fromordinal(today.toordinal() - 1)
    imported_file = short_mgr.dir / f"{imported_day.isoformat()}.md"
    imported_file.write_text(
        f"# {imported_day.isoformat()}\n\n## 12:00 [manual]\n<!-- hash:backfill -->\n- imported historical detail\n\n",
        encoding="utf-8",
    )

    second_written = await long_mgr.consolidate(days=7)

    assert "history-topic" in second_written
    assert any("imported historical detail" in call for call in calls)
