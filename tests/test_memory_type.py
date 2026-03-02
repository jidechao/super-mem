from types import SimpleNamespace

import pytest

from memsearch.chunker import Chunk
from memsearch.config import MemoryConfig, RerankConfig
from memsearch.core import MemSearch, infer_memory_type_from_source


def test_infer_memory_type_from_source_paths():
    assert infer_memory_type_from_source("memory/alice/short-memory/2026-03-02.md") == "short"
    assert infer_memory_type_from_source(r"memory\alice\long-memory\topic.md") == "long"
    assert infer_memory_type_from_source("docs/notes.md") == "other"


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

