from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from memsearch.memory.short_memory import ShortMemoryManager


@pytest.mark.asyncio
async def test_short_memory_hash_dedup(tmp_path):
    mgr = ShortMemoryManager(tmp_path, "alice")
    p1 = await mgr.write("同一条内容", source="manual")
    p2 = await mgr.write("同一条内容", source="manual")

    assert p1 is not None
    assert p2 is None

    text = mgr.read()
    assert "<!-- hash:" in text
    assert text.count("同一条内容") == 1


@pytest.mark.asyncio
async def test_short_memory_turn_dedup(tmp_path):
    mgr = ShortMemoryManager(tmp_path, "alice")
    first = await mgr.write(
        "第一条",
        source="auto/stop-hook",
        session_id="s1",
        turn_id="t1",
    )
    second = await mgr.write(
        "第二条（同 turn）",
        source="auto/stop-hook",
        session_id="s1",
        turn_id="t1",
    )

    assert first is not None
    assert second is None
    assert "turn:t1" in mgr.read()


@pytest.mark.asyncio
async def test_list_files_since_and_recent_content(tmp_path):
    mgr = ShortMemoryManager(tmp_path, "alice")

    now = datetime.now()
    old_day = now - timedelta(days=2)
    mid_day = now - timedelta(days=1)
    new_day = now

    await mgr.write("old", timestamp=old_day)
    await mgr.write("mid", timestamp=mid_day)
    await mgr.write("new", timestamp=new_day)

    files = mgr.list_files(days=3)
    assert len(files) == 3

    since = date.today() - timedelta(days=1)
    files_since = mgr.list_files_since(since)
    assert len(files_since) == 1
    assert files_since[0].stem == date.today().isoformat()

    recent = mgr.get_recent_content(days=2, max_lines=20)
    assert "new" in recent
    assert "mid" in recent
