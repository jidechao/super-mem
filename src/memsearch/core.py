"""MemSearch — main orchestrator class."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .watcher import FileWatcher

from .chunker import Chunk, chunk_markdown, compute_chunk_id
from .config import MemoryConfig, RerankConfig
from .embeddings import EmbeddingProvider, get_provider
from .compact import compact_chunks
from .memory import MemoryManager
from .memory.user import resolve_user_id
from .scanner import ScannedFile, scan_paths
from .store import MilvusStore

logger = logging.getLogger(__name__)


def infer_memory_type_from_source(
    source: str,
    *,
    short_memory_dir: str = "short-memory",
    long_memory_dir: str = "long-memory",
) -> str:
    """Infer memory type from source file path."""
    normalized = source.replace("\\", "/").lower()
    short_segment = f"/{short_memory_dir.strip('/').lower()}/"
    long_segment = f"/{long_memory_dir.strip('/').lower()}/"
    if short_segment in normalized:
        return "short"
    if long_segment in normalized:
        return "long"
    return "other"


class MemSearch:
    """High-level API for semantic memory search.

    Parameters
    ----------
    paths:
        Directories / files to index.
    embedding_provider:
        Name of the embedding backend (``"openai"``, ``"google"``, etc.).
    embedding_model:
        Override the default model for the chosen provider.
    milvus_uri:
        Milvus connection URI.  A local ``*.db`` path uses Milvus Lite,
        ``http://host:port`` connects to a Milvus server, and a
        ``https://*.zillizcloud.com`` URL connects to Zilliz Cloud.
    milvus_token:
        Authentication token for Milvus server or Zilliz Cloud.
        Not needed for Milvus Lite (local).
    collection:
        Milvus collection name.  Use different names to isolate
        agents sharing the same Milvus server.
    """

    def __init__(
        self,
        paths: list[str | Path] | None = None,
        *,
        embedding_provider: str = "openai",
        embedding_model: str | None = None,
        milvus_uri: str = "~/.memsearch/milvus.db",
        milvus_token: str | None = None,
        collection: str = "memsearch_chunks",
        max_chunk_size: int = 1500,
        overlap_lines: int = 2,
        user_id: str | None = None,
        memory_base_dir: str | Path = "memory",
        memory_config: MemoryConfig | None = None,
        compact_llm_provider: str = "openai",
        compact_llm_model: str | None = None,
        compact_timeout_seconds: float = 30.0,
        compact_max_retries: int = 3,
        compact_retry_base_delay: float = 0.2,
        compact_retry_max_delay: float = 2.0,
        reranker: str | None = None,
        rerank_model: str | None = None,
        rerank_config: RerankConfig | None = None,
    ) -> None:
        self._memory_config = memory_config or MemoryConfig()
        self._user_id = resolve_user_id(
            explicit=user_id,
            config_value=self._memory_config.user_id,
        )
        self._memory_base_dir = Path(memory_base_dir)
        self._compact_llm_provider = compact_llm_provider
        self._compact_llm_model = compact_llm_model
        self._compact_timeout_seconds = compact_timeout_seconds
        self._compact_max_retries = compact_max_retries
        self._compact_retry_base_delay = compact_retry_base_delay
        self._compact_retry_max_delay = compact_retry_max_delay
        self._memory: MemoryManager | None = None

        self._paths = [str(p) for p in (paths or [])]
        self._max_chunk_size = max_chunk_size
        self._overlap_lines = overlap_lines
        self._embedder: EmbeddingProvider = get_provider(
            embedding_provider, model=embedding_model
        )
        self._store = MilvusStore(
            uri=milvus_uri, token=milvus_token, collection=collection,
            dimension=self._embedder.dimension,
        )

        self._rerank_config = rerank_config or RerankConfig()
        self._reranker = None
        resolved_reranker = reranker
        resolved_rerank_model = rerank_model
        if resolved_reranker is None and self._rerank_config.enabled:
            resolved_reranker = self._rerank_config.provider
        if resolved_rerank_model is None and self._rerank_config.model:
            resolved_rerank_model = self._rerank_config.model

        if resolved_reranker:
            from .rerankers import get_reranker

            provider_kwargs: dict[str, Any] = {}
            if resolved_reranker == "api":
                provider_kwargs = {
                    "api_base": self._rerank_config.api_base,
                    "api_key_env": self._rerank_config.api_key_env,
                    "top_k_field": self._rerank_config.top_k_field,
                    "result_path": self._rerank_config.result_path,
                    "score_field": self._rerank_config.score_field,
                    "index_field": self._rerank_config.index_field,
                    "timeout_seconds": self._rerank_config.timeout_seconds,
                    "max_retries": self._rerank_config.max_retries,
                    "retry_base_delay": self._rerank_config.retry_base_delay,
                    "retry_max_delay": self._rerank_config.retry_max_delay,
                }
            self._reranker = get_reranker(
                resolved_reranker,
                model=resolved_rerank_model,
                **provider_kwargs,
            )

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    async def index(self, *, force: bool = False) -> int:
        """Scan paths and index all markdown files.

        Returns the number of chunks indexed.  Also removes chunks for
        files that no longer exist on disk (deleted-file cleanup).
        """
        started_at = time.perf_counter()
        files = scan_paths(self._paths)
        total = 0
        active_sources: set[str] = set()
        for f in files:
            active_sources.add(str(f.path))
            n = await self._index_file(f, force=force)
            total += n

        # Clean up chunks for files that no longer exist
        indexed_sources = self._store.indexed_sources(user_id=self._user_id)
        for source in indexed_sources:
            if source not in active_sources:
                self._store.delete_by_source(source, user_id=self._user_id)
                logger.info("Removed stale chunks for deleted file: %s", source)

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "event=index_complete user_id=%s files=%d chunks=%d duration_ms=%d",
            self._user_id,
            len(files),
            total,
            duration_ms,
        )
        return total

    async def index_file(self, path: str | Path) -> int:
        """Index a single file.  Returns number of chunks."""
        p = Path(path).expanduser().resolve()
        sf = ScannedFile(path=p, mtime=p.stat().st_mtime, size=p.stat().st_size)
        return await self._index_file(sf)

    async def _index_file(self, f: ScannedFile, *, force: bool = False) -> int:
        source = str(f.path)
        text = f.path.read_text(encoding="utf-8")
        chunks = chunk_markdown(
            text, source=source,
            max_chunk_size=self._max_chunk_size,
            overlap_lines=self._overlap_lines,
        )
        model = self._embedder.model_name

        # Compute composite chunk IDs (matching OpenClaw format)
        chunk_ids = {
            self._scoped_chunk_id(
                compute_chunk_id(c.source, c.start_line, c.end_line, c.content_hash, model)
            )
            for c in chunks
        }
        old_ids = self._store.hashes_by_source(source, user_id=self._user_id)

        # Delete stale chunks that are no longer in the file
        stale = old_ids - chunk_ids
        if stale:
            self._store.delete_by_hashes(list(stale))

        if not chunks:
            return 0

        if not force:
            # Only embed chunks whose ID doesn't already exist
            chunks = [
                c for c in chunks
                if self._scoped_chunk_id(
                    compute_chunk_id(c.source, c.start_line, c.end_line, c.content_hash, model)
                )
                not in old_ids
            ]
            if not chunks:
                return 0

        return await self._embed_and_store(chunks)

    async def _embed_and_store(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0

        model = self._embedder.model_name
        contents = [c.content for c in chunks]
        embeddings = await self._embedder.embed(contents)

        records: list[dict[str, Any]] = []
        for i, chunk in enumerate(chunks):
            chunk_id = compute_chunk_id(
                chunk.source, chunk.start_line, chunk.end_line,
                chunk.content_hash, model,
            )
            records.append(
                {
                    "chunk_hash": self._scoped_chunk_id(chunk_id),
                    "embedding": embeddings[i],
                    "content": chunk.content,
                    "source": chunk.source,
                    "heading": chunk.heading,
                    "heading_level": chunk.heading_level,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "user_id": self._user_id,
                    "memory_type": infer_memory_type_from_source(
                        chunk.source,
                        short_memory_dir=self._memory_config.short_memory_dir,
                        long_memory_dir=self._memory_config.long_memory_dir,
                    ),
                }
            )

        return self._store.upsert(records, user_id=self._user_id)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        filter_expr: str = "",
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search across indexed chunks.

        Parameters
        ----------
        query:
            Natural-language query.
        top_k:
            Maximum results to return.

        Returns
        -------
        list[dict]
            Each dict contains ``content``, ``source``, ``heading``,
            ``score``, and other metadata.
        """
        started_at = time.perf_counter()
        effective_user = self._resolve_user(user_id)
        embeddings = await self._embedder.embed([query])
        candidate_k = top_k
        if self._reranker:
            candidate_k = top_k * max(1, self._rerank_config.top_k_multiplier)

        candidates = self._store.search(
            embeddings[0],
            query_text=query,
            top_k=candidate_k,
            filter_expr=filter_expr,
            user_id=effective_user,
        )

        if self._reranker and candidates:
            docs = [c.get("content", "") for c in candidates]
            reranked = await self._reranker.rerank(query, docs, top_k=top_k)
            remapped: list[dict[str, Any]] = []
            for result in reranked:
                if 0 <= result.index < len(candidates):
                    remapped.append({**candidates[result.index], "score": result.score})
            candidates = remapped

        results = candidates[:top_k]
        for result in results:
            memory_type = result.get("memory_type", "")
            if memory_type not in {"short", "long", "other"}:
                result["memory_type"] = infer_memory_type_from_source(
                    result.get("source", ""),
                    short_memory_dir=self._memory_config.short_memory_dir,
                    long_memory_dir=self._memory_config.long_memory_dir,
                )
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "event=search_complete user_id=%s top_k=%d query_len=%d results=%d duration_ms=%d",
            effective_user,
            top_k,
            len(query),
            len(results),
            duration_ms,
        )
        return results

    # ------------------------------------------------------------------
    # Compact (compress memories)
    # ------------------------------------------------------------------

    async def compact(
        self,
        *,
        source: str | None = None,
        llm_provider: str = "openai",
        llm_model: str | None = None,
        prompt_template: str | None = None,
        output_dir: str | Path | None = None,
        user_id: str | None = None,
    ) -> str:
        """Compress indexed chunks into a summary and append to a daily log.

        The summary is appended to ``memory/YYYY-MM-DD.md`` inside the
        output directory (defaults to the first configured path).  The
        next ``index()`` or ``watch`` cycle will pick it up as a normal
        markdown file — keeping markdown as the single source of truth.

        Parameters
        ----------
        source:
            If given, only compact chunks from this source file.
        llm_provider:
            LLM backend for summarization.
        llm_model:
            Override the default model.
        prompt_template:
            Custom prompt template for the LLM.  Must contain a
            ``{chunks}`` placeholder.  Defaults to the built-in prompt.
        output_dir:
            Directory to write the compact file into.  Defaults to the
            first entry in *paths*.

        Returns
        -------
        str
            The generated summary markdown.
        """
        started_at = time.perf_counter()
        effective_user = self._resolve_user(user_id)
        filter_expr = f'source == "{source}"' if source else ""
        all_chunks = self._store.query(
            filter_expr=filter_expr,
            user_id=effective_user,
        )
        if not all_chunks:
            return ""

        summary = await compact_chunks(
            all_chunks, llm_provider=llm_provider, model=llm_model,
            prompt_template=prompt_template,
            timeout_seconds=self._compact_timeout_seconds,
            max_retries=self._compact_max_retries,
            retry_base_delay=self._compact_retry_base_delay,
            retry_max_delay=self._compact_retry_max_delay,
        )

        # Write summary to memory/YYYY-MM-DD.md (append)
        base = Path(output_dir) if output_dir else Path(self._paths[0]) if self._paths else Path.cwd()
        memory_dir = base / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        compact_file = memory_dir / f"{date.today()}.md"
        compact_heading = f"\n\n## Memory Compact\n\n"
        with open(compact_file, "a", encoding="utf-8") as f:
            if compact_file.stat().st_size == 0:
                f.write(f"# {date.today()}\n")
            f.write(compact_heading)
            f.write(summary)
            f.write("\n")

        # Index the updated file immediately
        n = await self.index_file(compact_file)
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "event=compact_complete user_id=%s source=%s input_chunks=%d indexed_new=%d duration_ms=%d output=%s",
            effective_user,
            source or "",
            len(all_chunks),
            n,
            duration_ms,
            compact_file,
        )
        return summary

    # ------------------------------------------------------------------
    # Watch
    # ------------------------------------------------------------------

    def watch(
        self,
        *,
        on_event: Callable[[str, str, Path], None] | None = None,
        debounce_ms: int | None = None,
    ) -> FileWatcher:
        """Watch configured paths for markdown changes and auto-index.

        Starts a background thread that monitors the filesystem.  When a
        markdown file is created or modified it is re-indexed automatically;
        when deleted its chunks are removed from the store.

        Parameters
        ----------
        on_event:
            Optional callback invoked *after* each event is processed.
            Signature: ``(event_type, action_summary, file_path)``.
            ``event_type`` is ``"created"``, ``"modified"``, or ``"deleted"``.

        Returns
        -------
        FileWatcher
            The running watcher.  Call ``watcher.stop()`` when done, or
            use it as a context manager.

        Example
        -------
        ::

            mem = MemSearch(paths=["./docs/"])
            watcher = mem.watch()
            # ... watcher auto-indexes in background ...
            watcher.stop()
        """
        from .watcher import FileWatcher

        def _on_change(event_type: str, file_path: Path) -> None:
            try:
                if event_type == "deleted":
                    self._store.delete_by_source(str(file_path), user_id=self._user_id)
                    summary = f"Removed chunks for {file_path}"
                else:
                    n = asyncio.run(self.index_file(file_path))
                    summary = f"Indexed {n} chunks from {file_path}"
                logger.info(summary)
            except Exception as exc:
                summary = f"Error processing {event_type} for {file_path}: {exc}"
                logger.exception(
                    "event=watch_handler_error user_id=%s event_type=%s path=%s error_type=%s message=%s",
                    self._user_id,
                    event_type,
                    file_path,
                    exc.__class__.__name__,
                    exc,
                )
            if on_event is not None:
                on_event(event_type, summary, file_path)

        fw_kwargs: dict[str, Any] = {}
        if debounce_ms is not None:
            fw_kwargs["debounce_ms"] = debounce_ms
        watcher = FileWatcher(self._paths, _on_change, **fw_kwargs)
        watcher.start()
        return watcher

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @property
    def store(self) -> MilvusStore:
        return self._store

    @property
    def memory(self) -> MemoryManager:
        """Lazy memory manager instance scoped by current user."""
        if self._memory is None:
            self._memory = MemoryManager(
                base_dir=self._memory_base_dir,
                user_id=self._user_id,
                config=self._memory_config,
                llm_provider=self._compact_llm_provider,
                llm_model=self._compact_llm_model,
            )
        return self._memory

    def _resolve_user(self, user_id: str | None) -> str:
        if user_id is None:
            return self._user_id
        return resolve_user_id(explicit=user_id, config_value=self._memory_config.user_id)

    def _scoped_chunk_id(self, base_chunk_id: str) -> str:
        if not self._user_id:
            return base_chunk_id
        return f"{self._user_id}:{base_chunk_id}"

    def close(self) -> None:
        """Release resources."""
        close_fn = getattr(self._reranker, "close", None)
        if close_fn is not None:
            maybe_coro = close_fn()
            if asyncio.iscoroutine(maybe_coro):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    asyncio.run(maybe_coro)
                else:
                    loop.create_task(maybe_coro)
        self._store.close()

    def __enter__(self) -> MemSearch:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
