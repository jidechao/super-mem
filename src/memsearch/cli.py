"""CLI interface for memsearch."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click

from .config import (
    MemSearchConfig,
    config_to_dict,
    get_config_value,
    load_config_file,
    resolve_config,
    save_config,
    set_config_value,
    GLOBAL_CONFIG_PATH,
    PROJECT_CONFIG_PATH,
    _SECTION_CLASSES,
)


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# -- CLI param name → dotted config key mapping --
_PARAM_MAP = {
    "provider": "embedding.provider",
    "model": "embedding.model",
    "collection": "milvus.collection",
    "milvus_uri": "milvus.uri",
    "milvus_token": "milvus.token",
    "llm_provider": "compact.llm_provider",
    "llm_model": "compact.llm_model",
    "prompt_file": "compact.prompt_file",
    "max_chunk_size": "chunking.max_chunk_size",
    "overlap_lines": "chunking.overlap_lines",
    "debounce_ms": "watch.debounce_ms",
}


def _build_cli_overrides(**kwargs) -> dict:
    """Map flat CLI params to a nested config override dict.

    Only non-None values are included (None means "not set by user").
    """
    result: dict = {}
    for param, dotted_key in _PARAM_MAP.items():
        val = kwargs.get(param)
        if val is None:
            continue
        section, field = dotted_key.split(".")
        result.setdefault(section, {})[field] = val
    return result


def _cfg_to_memsearch_kwargs(cfg: MemSearchConfig) -> dict:
    """Extract MemSearch constructor kwargs from a resolved config."""
    return {
        "embedding_provider": cfg.embedding.provider,
        "embedding_model": cfg.embedding.model or None,
        "milvus_uri": cfg.milvus.uri,
        "milvus_token": cfg.milvus.token or None,
        "collection": cfg.milvus.collection,
        "max_chunk_size": cfg.chunking.max_chunk_size,
        "overlap_lines": cfg.chunking.overlap_lines,
        "compact_timeout_seconds": cfg.compact.timeout_seconds,
        "compact_max_retries": cfg.compact.max_retries,
        "compact_retry_base_delay": cfg.compact.retry_base_delay,
        "compact_retry_max_delay": cfg.compact.retry_max_delay,
    }


def _resolve_effective_user(cfg: MemSearchConfig, user: str | None) -> str:
    from .memory.user import resolve_user_id

    return resolve_user_id(explicit=user, config_value=cfg.memory.user_id)


def _cfg_to_memsearch_kwargs_with_context(
    cfg: MemSearchConfig,
    *,
    user: str | None = None,
    reranker: str | None = None,
    rerank_model: str | None = None,
    no_rerank: bool = False,
) -> dict:
    kwargs = _cfg_to_memsearch_kwargs(cfg)
    kwargs["user_id"] = _resolve_effective_user(cfg, user)
    kwargs["memory_base_dir"] = cfg.memory.base_dir
    kwargs["memory_config"] = cfg.memory
    kwargs["compact_llm_provider"] = cfg.compact.llm_provider
    kwargs["compact_llm_model"] = cfg.compact.llm_model or None
    kwargs["rerank_config"] = cfg.rerank

    resolved_reranker: str | None = None
    resolved_rerank_model: str | None = None
    if not no_rerank:
        if reranker is not None:
            resolved_reranker = reranker
            resolved_rerank_model = rerank_model
        elif cfg.rerank.enabled:
            resolved_reranker = cfg.rerank.provider
            resolved_rerank_model = cfg.rerank.model or None

    if resolved_reranker is not None:
        kwargs["reranker"] = resolved_reranker
    if resolved_rerank_model is not None:
        kwargs["rerank_model"] = resolved_rerank_model

    return kwargs


def _build_memory_manager(
    cfg: MemSearchConfig,
    *,
    user: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
):
    from .memory import MemoryManager

    return MemoryManager(
        base_dir=cfg.memory.base_dir,
        user_id=_resolve_effective_user(cfg, user),
        config=cfg.memory,
        llm_provider=llm_provider or cfg.compact.llm_provider,
        llm_model=llm_model or cfg.compact.llm_model or None,
    )


# -- Common CLI options --

def _common_options(f):
    """Shared options for commands that create a MemSearch instance."""
    f = click.option("--provider", "-p", default=None, help="Embedding provider.")(f)
    f = click.option("--model", "-m", default=None, help="Override embedding model.")(f)
    f = click.option("--collection", "-c", default=None, help="Milvus collection name.")(f)
    f = click.option("--milvus-uri", default=None, help="Milvus connection URI.")(f)
    f = click.option("--milvus-token", default=None, help="Milvus auth token.")(f)
    f = click.option("--user", default=None, help="User ID for isolation.")(f)
    return f


@click.group()
@click.version_option(package_name="memsearch")
def cli() -> None:
    """memsearch — semantic memory search for markdown knowledge bases."""


@cli.command()
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True))
@_common_options
@click.option("--force", is_flag=True, help="Re-index all files.")
def index(
    paths: tuple[str, ...],
    provider: str | None,
    model: str | None,
    collection: str | None,
    milvus_uri: str | None,
    milvus_token: str | None,
    user: str | None,
    force: bool,
) -> None:
    """Index markdown files from PATHS."""
    from .core import MemSearch

    cfg = resolve_config(_build_cli_overrides(
        provider=provider, model=model, collection=collection,
        milvus_uri=milvus_uri, milvus_token=milvus_token,
    ))
    ms = MemSearch(
        list(paths),
        **_cfg_to_memsearch_kwargs_with_context(cfg, user=user),
    )
    try:
        n = _run(ms.index(force=force))
        click.echo(f"Indexed {n} chunks.")
    finally:
        ms.close()


@cli.command()
@click.argument("query")
@click.option("--top-k", "-k", default=None, type=int, help="Number of results.")
@click.option("--filter", "filter_expr", default=None, help="Milvus filter expression.")
@click.option("--reranker", default=None, help="Reranker provider name.")
@click.option("--rerank-model", default=None, help="Override reranker model.")
@click.option("--no-rerank", is_flag=True, help="Disable reranking for this call.")
@_common_options
@click.option("--json-output", "-j", is_flag=True, help="Output as JSON.")
def search(
    query: str,
    top_k: int | None,
    filter_expr: str | None,
    reranker: str | None,
    rerank_model: str | None,
    no_rerank: bool,
    provider: str | None,
    model: str | None,
    collection: str | None,
    milvus_uri: str | None,
    milvus_token: str | None,
    user: str | None,
    json_output: bool,
) -> None:
    """Search indexed memory for QUERY."""
    from .core import MemSearch

    cfg = resolve_config(_build_cli_overrides(
        provider=provider, model=model, collection=collection,
        milvus_uri=milvus_uri, milvus_token=milvus_token,
    ))
    effective_user = _resolve_effective_user(cfg, user)
    ms = MemSearch(
        **_cfg_to_memsearch_kwargs_with_context(
            cfg,
            user=user,
            reranker=reranker,
            rerank_model=rerank_model,
            no_rerank=no_rerank,
        )
    )
    try:
        results = _run(
            ms.search(
                query,
                top_k=top_k or 5,
                filter_expr=filter_expr or "",
                user_id=effective_user,
            )
        )
        if json_output:
            click.echo(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            if not results:
                click.echo("No results found.")
                return
            for i, r in enumerate(results, 1):
                score = r.get("score", 0)
                source = r.get("source", "?")
                heading = r.get("heading", "")
                content = r.get("content", "")
                click.echo(f"\n--- Result {i} (score: {score:.4f}) ---")
                click.echo(f"Source: {source}")
                if heading:
                    click.echo(f"Heading: {heading}")
                if len(content) > 500:
                    click.echo(content[:500])
                    chunk_hash = r.get("chunk_hash", "")
                    click.echo(f"  ... [truncated, run 'memsearch expand {chunk_hash}' for full content]")
                else:
                    click.echo(content)
    finally:
        ms.close()


# ======================================================================
# Claude Code plugin commands (progressive disclosure L2/L3)
#
# The following commands (`expand` and `transcript`) are designed for
# the Claude Code plugin's three-level progressive disclosure workflow:
#   L1: `search` returns chunk snippets (injected into the prompt)
#   L2: `expand` shows the full heading section around a chunk
#   L3: `transcript` drills into the original JSONL conversation
#
# They work with memsearch's anchor comments embedded in memory files:
#   <!-- session:UUID turn:UUID transcript:PATH -->
#
# These commands are fully functional standalone, but their primary
# consumer is the ccplugin/ hooks that auto-inject memory context.
# ======================================================================


@cli.command()
@click.argument("chunk_hash")
@click.option("--section/--no-section", default=True, help="Show full heading section (default).")
@click.option("--lines", "-n", default=None, type=int, help="Show N lines before/after instead of full section.")
@click.option("--json-output", "-j", is_flag=True, help="Output as JSON.")
@_common_options
def expand(
    chunk_hash: str,
    section: bool,
    lines: int | None,
    json_output: bool,
    provider: str | None,
    model: str | None,
    collection: str | None,
    milvus_uri: str | None,
    milvus_token: str | None,
    user: str | None,
) -> None:
    """Expand a memory chunk to show full context. [Claude Code plugin: L2]

    Look up CHUNK_HASH in the index, then read the source markdown file
    to return the surrounding context (full heading section by default).

    Part of the progressive disclosure workflow (search -> expand -> transcript).
    """
    from .store import MilvusStore

    cfg = resolve_config(_build_cli_overrides(
        provider=provider, model=model, collection=collection,
        milvus_uri=milvus_uri, milvus_token=milvus_token,
    ))
    store = MilvusStore(
        uri=cfg.milvus.uri,
        token=cfg.milvus.token or None,
        collection=cfg.milvus.collection,
        dimension=None,
    )
    try:
        chunks = store.query(
            filter_expr=f'chunk_hash == "{chunk_hash}"',
            user_id=_resolve_effective_user(cfg, user),
        )
        if not chunks:
            click.echo(f"Chunk not found: {chunk_hash}", err=True)
            sys.exit(1)

        chunk = chunks[0]
        source = chunk["source"]
        start_line = chunk["start_line"]
        end_line = chunk["end_line"]
        heading = chunk.get("heading", "")
        heading_level = chunk.get("heading_level", 0)

        source_path = Path(source)
        if not source_path.exists():
            click.echo(f"Source file not found: {source}", err=True)
            sys.exit(1)

        all_lines = source_path.read_text(encoding="utf-8").splitlines()

        if lines is not None:
            # Show N lines before/after the chunk
            ctx_start = max(0, start_line - 1 - lines)
            ctx_end = min(len(all_lines), end_line + lines)
            expanded = "\n".join(all_lines[ctx_start:ctx_end])
            expanded_start = ctx_start + 1
            expanded_end = ctx_end
        else:
            # Show full section under the same heading
            expanded, expanded_start, expanded_end = _extract_section(
                all_lines, start_line, heading_level,
            )

        # Parse any anchor comments in the expanded text
        import re
        anchor_match = re.search(
            r"<!--\s*session:(\S+)\s+turn:(\S+)\s+transcript:(\S+)\s*-->",
            expanded,
        )
        anchor = {}
        if anchor_match:
            anchor = {
                "session": anchor_match.group(1),
                "turn": anchor_match.group(2),
                "transcript": anchor_match.group(3),
            }

        if json_output:
            result = {
                "chunk_hash": chunk_hash,
                "source": source,
                "heading": heading,
                "start_line": expanded_start,
                "end_line": expanded_end,
                "content": expanded,
            }
            if anchor:
                result["anchor"] = anchor
            click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            click.echo(f"Source: {source} (lines {expanded_start}-{expanded_end})")
            if heading:
                click.echo(f"Heading: {heading}")
            if anchor:
                click.echo(f"Session: {anchor['session']}  Turn: {anchor['turn']}")
                click.echo(f"Transcript: {anchor['transcript']}")
            click.echo(f"\n{expanded}")
    finally:
        store.close()


def _extract_section(
    all_lines: list[str], start_line: int, heading_level: int,
) -> tuple[str, int, int]:
    """Extract the full section containing the chunk.

    Walks backward to find the section heading, then forward to the next
    heading of equal or higher level (or EOF).
    """
    # Find section start — walk backward to the heading
    section_start = start_line - 1  # 0-indexed
    if heading_level > 0:
        for i in range(start_line - 2, -1, -1):
            line = all_lines[i]
            if line.startswith("#"):
                level = len(line) - len(line.lstrip("#"))
                if level <= heading_level:
                    section_start = i
                    break

    # Find section end — walk forward to the next heading of same or higher level
    section_end = len(all_lines)
    if heading_level > 0:
        for i in range(start_line, len(all_lines)):
            line = all_lines[i]
            if line.startswith("#"):
                level = len(line) - len(line.lstrip("#"))
                if level <= heading_level:
                    section_end = i
                    break

    content = "\n".join(all_lines[section_start:section_end])
    return content, section_start + 1, section_end


@cli.command()
@click.argument("jsonl_path", type=click.Path(exists=True))
@click.option("--turn", "-t", default=None, help="Target turn UUID (prefix match).")
@click.option("--context", "-c", "ctx", default=3, type=int, help="Number of turns before/after target.")
@click.option("--json-output", "-j", is_flag=True, help="Output as JSON.")
def transcript(
    jsonl_path: str,
    turn: str | None,
    ctx: int,
    json_output: bool,
) -> None:
    """View original conversation turns from a JSONL transcript. [Claude Code plugin: L3]

    Parse JSONL_PATH and display conversation turns. If --turn is given,
    show context around that specific turn; otherwise show an index of
    all user turns.

    Part of the progressive disclosure workflow (search -> expand -> transcript).
    """
    from .transcript import (
        parse_transcript,
        find_turn_context,
        format_turns,
        format_turn_index,
        turns_to_dicts,
    )

    turns = parse_transcript(jsonl_path)
    if not turns:
        click.echo("No conversation turns found.")
        return

    if turn:
        context_turns, highlight = find_turn_context(turns, turn, context=ctx)
        if not context_turns:
            click.echo(f"Turn not found: {turn}", err=True)
            sys.exit(1)
        if json_output:
            click.echo(json.dumps(turns_to_dicts(context_turns), indent=2, ensure_ascii=False))
        else:
            click.echo(f"Showing {len(context_turns)} turns around {turn[:12]}:\n")
            click.echo(format_turns(context_turns, highlight_idx=highlight))
    else:
        if json_output:
            click.echo(json.dumps(turns_to_dicts(turns), indent=2, ensure_ascii=False))
        else:
            click.echo(f"All turns ({len(turns)}):\n")
            click.echo(format_turn_index(turns))


@cli.command()
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True))
@_common_options
@click.option("--debounce-ms", default=None, type=int, help="Debounce delay in ms.")
def watch(
    paths: tuple[str, ...],
    provider: str | None,
    model: str | None,
    collection: str | None,
    milvus_uri: str | None,
    milvus_token: str | None,
    user: str | None,
    debounce_ms: int | None,
) -> None:
    """Watch PATHS for markdown changes and auto-index."""
    from .core import MemSearch

    cfg = resolve_config(_build_cli_overrides(
        provider=provider, model=model, collection=collection,
        milvus_uri=milvus_uri, milvus_token=milvus_token,
        debounce_ms=debounce_ms,
    ))
    ms = MemSearch(
        list(paths),
        **_cfg_to_memsearch_kwargs_with_context(cfg, user=user),
    )

    # Initial index: ensure existing files are indexed before watching
    n = _run(ms.index())
    if n:
        click.echo(f"Indexed {n} chunks.")

    def _on_event(event_type: str, summary: str, file_path) -> None:
        click.echo(summary)

    click.echo(f"Watching {len(paths)} path(s) for changes... (Ctrl+C to stop)")
    watcher = ms.watch(on_event=_on_event, debounce_ms=cfg.watch.debounce_ms)
    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        click.echo("\nStopping watcher.")
    finally:
        watcher.stop()
        ms.close()


@cli.command()
@click.option("--source", "-s", default=None, help="Only compact chunks from this source.")
@click.option("--output-dir", "-o", default=None, type=click.Path(), help="Directory to write the compact summary into.")
@click.option("--llm-provider", default=None, help="LLM for summarization.")
@click.option("--llm-model", default=None, help="Override LLM model.")
@click.option("--prompt", default=None, help="Custom prompt template (must contain {chunks}).")
@click.option("--prompt-file", default=None, type=click.Path(exists=True), help="Read prompt template from file.")
@_common_options
def compact(
    source: str | None,
    output_dir: str | None,
    llm_provider: str | None,
    llm_model: str | None,
    prompt: str | None,
    prompt_file: str | None,
    provider: str | None,
    model: str | None,
    collection: str | None,
    milvus_uri: str | None,
    milvus_token: str | None,
    user: str | None,
) -> None:
    """Compress stored memories into a summary."""
    from .core import MemSearch

    cfg = resolve_config(_build_cli_overrides(
        provider=provider, model=model, collection=collection,
        milvus_uri=milvus_uri, milvus_token=milvus_token,
        llm_provider=llm_provider, llm_model=llm_model,
        prompt_file=prompt_file,
    ))

    prompt_template = prompt
    if cfg.compact.prompt_file and not prompt_template:
        prompt_template = Path(cfg.compact.prompt_file).read_text(encoding="utf-8")

    ms = MemSearch(**_cfg_to_memsearch_kwargs_with_context(cfg, user=user))
    try:
        summary = _run(ms.compact(
            source=source,
            llm_provider=cfg.compact.llm_provider,
            llm_model=cfg.compact.llm_model or None,
            prompt_template=prompt_template,
            output_dir=output_dir,
            user_id=_resolve_effective_user(cfg, user),
        ))
        if summary:
            click.echo("Compact complete. Summary:\n")
            click.echo(summary)
        else:
            click.echo("No chunks to compact.")
    finally:
        ms.close()


@cli.command()
@click.option("--collection", "-c", default=None, help="Milvus collection name.")
@click.option("--milvus-uri", default=None, help="Milvus connection URI.")
@click.option("--milvus-token", default=None, help="Milvus auth token.")
@click.option("--user", default=None, help="User ID for isolation.")
def stats(
    collection: str | None,
    milvus_uri: str | None,
    milvus_token: str | None,
    user: str | None,
) -> None:
    """Show statistics about the index."""
    from .store import MilvusStore

    cfg = resolve_config(_build_cli_overrides(
        collection=collection, milvus_uri=milvus_uri, milvus_token=milvus_token,
    ))
    store = MilvusStore(
        uri=cfg.milvus.uri,
        token=cfg.milvus.token or None,
        collection=cfg.milvus.collection,
        dimension=None,
    )
    try:
        resolved_user = _resolve_effective_user(cfg, user) if user else ""
        count = store.count(user_id=resolved_user)
        click.echo(f"Total indexed chunks: {count}")
    finally:
        store.close()


@cli.command()
@click.option("--collection", "-c", default=None, help="Milvus collection name.")
@click.option("--milvus-uri", default=None, help="Milvus connection URI.")
@click.option("--milvus-token", default=None, help="Milvus auth token.")
@click.confirmation_option(prompt="This will delete all indexed data. Continue?")
def reset(
    collection: str | None,
    milvus_uri: str | None,
    milvus_token: str | None,
) -> None:
    """Drop all indexed data."""
    from .store import MilvusStore

    cfg = resolve_config(_build_cli_overrides(
        collection=collection, milvus_uri=milvus_uri, milvus_token=milvus_token,
    ))
    store = MilvusStore(
        uri=cfg.milvus.uri,
        token=cfg.milvus.token or None,
        collection=cfg.milvus.collection,
        dimension=None,
    )
    try:
        store.drop()
        click.echo("Dropped collection.")
    finally:
        store.close()


# ======================================================================
# Memory command group
# ======================================================================


@cli.group("memory")
def memory_group() -> None:
    """Manage short-term and long-term memory files."""


@memory_group.command("write")
@click.argument("content", required=False)
@click.option("--stdin", "from_stdin", is_flag=True, help="Read content from stdin.")
@click.option("--source", default="manual", help="Write source label.")
@click.option("--tag", "tags", multiple=True, help="Optional tag(s).")
@click.option("--session-id", default=None, help="Session ID for anchor comment.")
@click.option("--turn-id", default=None, help="Turn ID for anchor comment.")
@click.option("--transcript-path", default=None, help="Transcript path for anchor comment.")
@click.option("--user", default=None, help="User ID for isolation.")
def memory_write(
    content: str | None,
    from_stdin: bool,
    source: str,
    tags: tuple[str, ...],
    session_id: str | None,
    turn_id: str | None,
    transcript_path: str | None,
    user: str | None,
) -> None:
    """Write one short-memory entry."""
    if from_stdin:
        content = sys.stdin.read()
    if not content or not content.strip():
        click.echo("Error: empty content. Pass text argument or --stdin.", err=True)
        sys.exit(1)

    cfg = resolve_config()
    mm = _build_memory_manager(cfg, user=user)
    path = _run(
        mm.write_short(
            content,
            source=source,
            tags=list(tags) if tags else None,
            session_id=session_id,
            turn_id=turn_id,
            transcript_path=transcript_path,
        )
    )
    if path is None:
        click.echo("Skipped duplicate short-memory entry.")
    else:
        click.echo(str(path))


@memory_group.command("list")
@click.option("--days", default=30, type=int, help="Lookback days.")
@click.option("--user", default=None, help="User ID for isolation.")
def memory_list(days: int, user: str | None) -> None:
    """List short-memory files."""
    cfg = resolve_config()
    mm = _build_memory_manager(cfg, user=user)
    files = mm.short.list_files(days=days)
    if not files:
        click.echo("No short-memory files found.")
        return
    for file in files:
        click.echo(str(file))


@memory_group.command("read")
@click.option("--date", "day", default=None, help="Date like YYYY-MM-DD (default today).")
@click.option("--user", default=None, help="User ID for isolation.")
def memory_read(day: str | None, user: str | None) -> None:
    """Read one short-memory daily file."""
    cfg = resolve_config()
    mm = _build_memory_manager(cfg, user=user)
    try:
        text = mm.short.read(day=day)
    except ValueError:
        click.echo("Invalid --date format, expected YYYY-MM-DD.", err=True)
        sys.exit(1)

    if not text.strip():
        click.echo("No memory content found.")
        return
    click.echo(text)


@memory_group.command("consolidate")
@click.option("--days", default=7, type=int, help="Lookback days.")
@click.option("--force", is_flag=True, help="Ignore watermark and force processing.")
@click.option("--llm-provider", default=None, help="Override LLM provider for consolidation.")
@click.option("--llm-model", default=None, help="Override LLM model for consolidation.")
@click.option("--user", default=None, help="User ID for isolation.")
def memory_consolidate(
    days: int,
    force: bool,
    llm_provider: str | None,
    llm_model: str | None,
    user: str | None,
) -> None:
    """Extract long-term topics from short-term memory."""
    cfg = resolve_config()
    mm = _build_memory_manager(
        cfg,
        user=user,
        llm_provider=llm_provider,
        llm_model=llm_model,
    )
    written = _run(mm.consolidate(days=days, force=force))
    if not written:
        click.echo("No new long-memory topics generated.")
        return
    for topic, path in written.items():
        click.echo(f"{topic}: {path}")


@memory_group.command("topics")
@click.option("--user", default=None, help="User ID for isolation.")
def memory_topics(user: str | None) -> None:
    """List all long-memory topics."""
    cfg = resolve_config()
    mm = _build_memory_manager(cfg, user=user)
    topics = mm.long.list_topics()
    if not topics:
        click.echo("No long-memory topics found.")
        return
    for topic in topics:
        click.echo(topic)


@memory_group.command("read-topic")
@click.argument("topic")
@click.option("--user", default=None, help="User ID for isolation.")
def memory_read_topic(topic: str, user: str | None) -> None:
    """Read one long-memory topic file."""
    cfg = resolve_config()
    mm = _build_memory_manager(cfg, user=user)
    text = mm.long.read(topic)
    if not text.strip():
        click.echo(f"Topic not found: {topic}", err=True)
        sys.exit(1)
    click.echo(text)


@memory_group.command("write-topic")
@click.argument("topic")
@click.argument("content")
@click.option("--user", default=None, help="User ID for isolation.")
def memory_write_topic(topic: str, content: str, user: str | None) -> None:
    """Write long-memory content into a topic file."""
    cfg = resolve_config()
    mm = _build_memory_manager(cfg, user=user)
    path = _run(mm.write_long(topic, content))
    click.echo(str(path))


@memory_group.command("check-triggers")
@click.argument("text")
@click.option("--user", default=None, help="User ID for isolation.")
def memory_check_triggers(text: str, user: str | None) -> None:
    """Evaluate triggers against input text and execute matched actions."""
    cfg = resolve_config()
    mm = _build_memory_manager(cfg, user=user)
    result = _run(mm.on_input(text))
    payload = {
        "keyword_triggered": result.keyword_triggered,
        "matched_keyword": result.matched_keyword,
        "short_memory_path": str(result.short_memory_path) if result.short_memory_path else None,
        "long_memory_paths": (
            {topic: str(path) for topic, path in result.long_memory_paths.items()}
            if result.long_memory_paths
            else {}
        ),
    }
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))


# ======================================================================
# Config command group
# ======================================================================

@cli.group("config")
def config_group() -> None:
    """Manage memsearch configuration."""


@config_group.command("init")
@click.option("--project", is_flag=True, help="Write to .memsearch.toml (project-level) instead of global.")
def config_init(project: bool) -> None:
    """Interactive configuration wizard."""
    from dataclasses import fields as dc_fields

    target = PROJECT_CONFIG_PATH if project else GLOBAL_CONFIG_PATH
    existing = load_config_file(target)
    current = resolve_config()

    result: dict = {}

    click.echo(f"memsearch configuration wizard")
    click.echo(f"Writing to: {target}\n")

    # Milvus
    click.echo("── Milvus ──")
    result["milvus"] = {}
    result["milvus"]["uri"] = click.prompt(
        "  Milvus URI", default=current.milvus.uri,
    )
    result["milvus"]["token"] = click.prompt(
        "  Milvus token (empty for none)", default=current.milvus.token,
    )
    result["milvus"]["collection"] = click.prompt(
        "  Collection name", default=current.milvus.collection,
    )

    # Embedding
    click.echo("\n── Embedding ──")
    result["embedding"] = {}
    _embedding_defaults = {
        "openai": "text-embedding-3-small",
        "google": "gemini-embedding-001",
        "voyage": "voyage-3-lite",
        "ollama": "nomic-embed-text",
        "local": "all-MiniLM-L6-v2",
    }
    result["embedding"]["provider"] = click.prompt(
        "  Provider (openai/google/voyage/ollama/local)",
        default=current.embedding.provider,
    )
    _emb_provider = result["embedding"]["provider"]
    _emb_model_default = current.embedding.model or _embedding_defaults.get(_emb_provider, "")
    result["embedding"]["model"] = click.prompt(
        "  Model", default=_emb_model_default,
    )

    # Chunking
    click.echo("\n── Chunking ──")
    result["chunking"] = {}
    result["chunking"]["max_chunk_size"] = click.prompt(
        "  Max chunk size (chars)", default=current.chunking.max_chunk_size, type=int,
    )
    result["chunking"]["overlap_lines"] = click.prompt(
        "  Overlap lines", default=current.chunking.overlap_lines, type=int,
    )

    # Watch
    click.echo("\n── Watch ──")
    result["watch"] = {}
    result["watch"]["debounce_ms"] = click.prompt(
        "  Debounce (ms)", default=current.watch.debounce_ms, type=int,
    )

    # Compact
    click.echo("\n── Compact ──")
    _compact_defaults = {
        "openai": "gpt-4o-mini",
        "anthropic": "claude-sonnet-4-5-20250929",
        "gemini": "gemini-2.0-flash",
    }
    result["compact"] = {}
    result["compact"]["llm_provider"] = click.prompt(
        "  LLM provider (openai/anthropic/gemini)", default=current.compact.llm_provider,
    )
    _compact_provider = result["compact"]["llm_provider"]
    _compact_model_default = current.compact.llm_model or _compact_defaults.get(_compact_provider, "")
    result["compact"]["llm_model"] = click.prompt(
        "  LLM model", default=_compact_model_default,
    )
    result["compact"]["prompt_file"] = click.prompt(
        "  Prompt file path (empty for built-in)", default=current.compact.prompt_file,
    )
    result["compact"]["timeout_seconds"] = click.prompt(
        "  Request timeout seconds",
        default=current.compact.timeout_seconds,
        type=float,
    )
    result["compact"]["max_retries"] = click.prompt(
        "  Max retries",
        default=current.compact.max_retries,
        type=int,
    )
    result["compact"]["retry_base_delay"] = click.prompt(
        "  Retry base delay (seconds)",
        default=current.compact.retry_base_delay,
        type=float,
    )
    result["compact"]["retry_max_delay"] = click.prompt(
        "  Retry max delay (seconds)",
        default=current.compact.retry_max_delay,
        type=float,
    )

    # Memory
    click.echo("\n── Memory ──")
    result["memory"] = {}
    result["memory"]["base_dir"] = click.prompt(
        "  Base dir", default=current.memory.base_dir,
    )
    result["memory"]["user_id"] = click.prompt(
        "  Default user_id (empty = auto resolve)", default=current.memory.user_id,
    )
    result["memory"]["keywords"] = click.prompt(
        "  Keyword triggers (comma-separated)",
        default=",".join(current.memory.keywords),
    ).split(",")
    result["memory"]["short_interval_seconds"] = click.prompt(
        "  Short memory interval seconds (0=off)",
        default=current.memory.short_interval_seconds,
        type=int,
    )
    result["memory"]["long_interval_seconds"] = click.prompt(
        "  Long memory interval seconds (0=off)",
        default=current.memory.long_interval_seconds,
        type=int,
    )
    result["memory"]["auto_consolidate"] = click.confirm(
        "  Auto-consolidate long memory",
        default=current.memory.auto_consolidate,
    )
    result["memory"]["consolidation_days"] = click.prompt(
        "  Consolidation lookback days",
        default=current.memory.consolidation_days,
        type=int,
    )

    # Rerank
    click.echo("\n── Rerank ──")
    result["rerank"] = {}
    result["rerank"]["enabled"] = click.confirm(
        "  Enable reranker",
        default=current.rerank.enabled,
    )
    result["rerank"]["provider"] = click.prompt(
        "  Provider (api/cross-encoder)",
        default=current.rerank.provider,
    )
    result["rerank"]["model"] = click.prompt(
        "  Model",
        default=current.rerank.model,
    )
    result["rerank"]["top_k_multiplier"] = click.prompt(
        "  Candidate multiplier",
        default=current.rerank.top_k_multiplier,
        type=int,
    )
    result["rerank"]["api_base"] = click.prompt(
        "  API base (for provider=api)",
        default=current.rerank.api_base,
    )
    result["rerank"]["api_key_env"] = click.prompt(
        "  API key env var name",
        default=current.rerank.api_key_env,
    )
    result["rerank"]["timeout_seconds"] = click.prompt(
        "  Request timeout seconds",
        default=current.rerank.timeout_seconds,
        type=float,
    )
    result["rerank"]["max_retries"] = click.prompt(
        "  Max retries",
        default=current.rerank.max_retries,
        type=int,
    )
    result["rerank"]["retry_base_delay"] = click.prompt(
        "  Retry base delay (seconds)",
        default=current.rerank.retry_base_delay,
        type=float,
    )
    result["rerank"]["retry_max_delay"] = click.prompt(
        "  Retry max delay (seconds)",
        default=current.rerank.retry_max_delay,
        type=float,
    )

    save_config(result, target)
    click.echo(f"\nConfig saved to {target}")


@config_group.command("set")
@click.argument("key")
@click.argument("value")
@click.option("--project", is_flag=True, help="Write to project config.")
def config_set(key: str, value: str, project: bool) -> None:
    """Set a config value (e.g. memsearch config set milvus.uri http://host:19530)."""
    try:
        set_config_value(key, value, project=project)
        target = PROJECT_CONFIG_PATH if project else GLOBAL_CONFIG_PATH
        click.echo(f"Set {key} = {value} in {target}")
    except (KeyError, ValueError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@config_group.command("get")
@click.argument("key")
def config_get(key: str) -> None:
    """Get a resolved config value (e.g. memsearch config get milvus.uri)."""
    try:
        val = get_config_value(key)
        click.echo(val)
    except KeyError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@config_group.command("list")
@click.option("--resolved", "mode", flag_value="resolved", default=True, help="Show fully resolved config (default).")
@click.option("--global", "mode", flag_value="global", help="Show global config file only.")
@click.option("--project", "mode", flag_value="project", help="Show project config file only.")
def config_list(mode: str) -> None:
    """Show configuration."""
    import tomli_w

    if mode == "global":
        data = load_config_file(GLOBAL_CONFIG_PATH)
        label = f"Global ({GLOBAL_CONFIG_PATH})"
    elif mode == "project":
        data = load_config_file(PROJECT_CONFIG_PATH)
        label = f"Project ({PROJECT_CONFIG_PATH})"
    else:
        cfg = resolve_config()
        data = config_to_dict(cfg)
        label = "Resolved (all sources merged)"

    click.echo(f"# {label}\n")
    if data:
        click.echo(tomli_w.dumps(data))
    else:
        click.echo("(empty)")
