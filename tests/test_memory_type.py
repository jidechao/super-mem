from datetime import date
from types import SimpleNamespace

import pytest

from memsearch.chunker import Chunk
from memsearch.config import MemoryConfig, RerankConfig
from memsearch.core import MemSearch, infer_memory_type_from_source


def test_infer_memory_type_from_source_paths():
    assert infer_memory_type_from_source("memory/alice/short-memory/2026-03-02.md") == "short"
    assert infer_memory_type_from_source(r"memory\alice\long-memory\topic.md") == "long"
    assert infer_memory_type_from_source("docs/notes.md") == "other"


def test_infer_memory_type_from_source_paths_with_custom_dirs():
    assert infer_memory_type_from_source(
        "memory/alice/s-memory/2026-03-02.md",
        short_memory_dir="s-memory",
        long_memory_dir="l-memory",
    ) == "short"
    assert infer_memory_type_from_source(
        r"memory\alice\l-memory\topic.md",
        short_memory_dir="s-memory",
        long_memory_dir="l-memory",
    ) == "long"
    assert infer_memory_type_from_source(
        "docs/notes.md",
        short_memory_dir="s-memory",
        long_memory_dir="l-memory",
    ) == "other"


@pytest.mark.asyncio
async def test_embed_and_store_sets_memory_type():
    class _Embedder:
        model_name = "test-model"

        async def embed(self, contents):  # noqa: ANN001
            return [[0.1, 0.2, 0.3] for _ in contents]

    class _Store:
        def __init__(self):
            self.records = None

        def upsert(self, records, *, user_id=""):  # noqa: ANN001
            self.records = records
            return len(records)

    mem = MemSearch.__new__(MemSearch)
    mem._embedder = _Embedder()
    mem._store = _Store()
    mem._user_id = "alice"
    mem._memory_config = MemoryConfig()

    chunks = [
        Chunk(
            content="short content",
            source="memory/alice/short-memory/2026-03-02.md",
            heading="h1",
            heading_level=1,
            start_line=1,
            end_line=2,
        ),
        Chunk(
            content="long content",
            source="memory/alice/long-memory/topic.md",
            heading="h2",
            heading_level=2,
            start_line=3,
            end_line=4,
        ),
        Chunk(
            content="other content",
            source="notes/general.md",
            heading="h3",
            heading_level=2,
            start_line=5,
            end_line=6,
        ),
    ]

    inserted = await mem._embed_and_store(chunks)
    assert inserted == 3
    assert [r["memory_type"] for r in mem._store.records] == ["short", "long", "other"]


@pytest.mark.asyncio
async def test_search_backfills_missing_memory_type_from_source():
    class _Embedder:
        async def embed(self, contents):  # noqa: ANN001
            return [[0.1, 0.2, 0.3] for _ in contents]

    class _Store:
        def search(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return [
                {"content": "a", "source": "memory/alice/short-memory/2026-03-02.md", "score": 0.9},
                {"content": "b", "source": "memory/alice/long-memory/topic.md", "score": 0.8},
                {"content": "c", "source": "docs/random.md", "score": 0.7},
                {"content": "d", "source": "docs/random.md", "score": 0.6, "memory_type": "long"},
            ]

    mem = MemSearch.__new__(MemSearch)
    mem._embedder = _Embedder()
    mem._store = _Store()
    mem._user_id = "alice"
    mem._memory_config = MemoryConfig()
    mem._reranker = None
    mem._rerank_config = RerankConfig()

    results = await mem.search("query", top_k=10)
    assert [r["memory_type"] for r in results] == ["short", "long", "other", "long"]


@pytest.mark.asyncio
async def test_search_backfills_with_custom_memory_dirs():
    class _Embedder:
        async def embed(self, contents):  # noqa: ANN001
            return [[0.1, 0.2, 0.3] for _ in contents]

    class _Store:
        def search(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return [
                {"content": "a", "source": "memory/alice/s-memory/2026-03-02.md", "score": 0.9},
                {"content": "b", "source": "memory/alice/l-memory/topic.md", "score": 0.8},
            ]

    mem = MemSearch.__new__(MemSearch)
    mem._embedder = _Embedder()
    mem._store = _Store()
    mem._user_id = "alice"
    mem._memory_config = MemoryConfig(short_memory_dir="s-memory", long_memory_dir="l-memory")
    mem._reranker = None
    mem._rerank_config = RerankConfig()

    results = await mem.search("query", top_k=10)
    assert [r["memory_type"] for r in results] == ["short", "long"]


@pytest.mark.asyncio
async def test_compact_honors_explicit_user_when_writing_and_reindexing(tmp_path, monkeypatch: pytest.MonkeyPatch):
    queried: list[tuple[str, str]] = []
    indexed_paths: list[str] = []

    class _Store:
        def query(self, *, filter_expr="", user_id=""):  # noqa: ANN001
            queried.append((filter_expr, user_id))
            return [{"content": "remember this", "source": 'C:\\temp\\note "1".md'}]

    async def fake_compact(chunks, **kwargs):  # noqa: ANN001
        return "summary body"

    async def fake_index_file(path, *, user_id=None):  # noqa: ANN001
        indexed_paths.append(str(path))
        return 1

    mem = MemSearch.__new__(MemSearch)
    mem._store = _Store()
    mem._user_id = "alice"
    mem._paths = []
    mem._memory_base_dir = tmp_path
    mem._memory_config = MemoryConfig()
    mem._compact_timeout_seconds = 30.0
    mem._compact_max_retries = 3
    mem._compact_retry_base_delay = 0.2
    mem._compact_retry_max_delay = 2.0
    mem.index_file = fake_index_file  # type: ignore[method-assign]

    monkeypatch.setattr("memsearch.core.compact_chunks", fake_compact)

    summary = await mem.compact(source='C:\\temp\\note "1".md', user_id="bob")

    assert summary == "summary body"
    assert queried == [('source == "C:\\\\temp\\\\note \\\"1\\\".md"', "bob")]
    expected_path = tmp_path / "bob" / "short-memory" / f"{date.today()}.md"
    assert expected_path.exists()
    assert indexed_paths == [str(expected_path)]
