"""Full-feature memsearch demo (single file).

Shows end-to-end flow with:
- short-term memory writes + dedup
- keyword/scheduled triggers
- long-term memory consolidation
- multi-user isolation
- search with filter expression
- API reranker comparison

Default runtime profile: OpenAI + Zilliz Cloud + API reranker.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path
from typing import Iterable

from memsearch import MemSearch
from memsearch.config import MemoryConfig, RerankConfig


def _echo(title: str) -> None:
    print(f"\n=== {title} ===")


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing environment variable: {name}. "
            f"Please set it before running this demo."
        )
    return value


def _optional_env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _load_dotenv(path: Path) -> bool:
    """Load simple KEY=VALUE pairs from a .env file."""
    if not path.is_file():
        return False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and not os.environ.get(key):
            os.environ[key] = value
    return True


def _apply_env_aliases() -> None:
    """Map demo-friendly env names to runtime names when needed."""
    embedding_base_url = _optional_env("EMBEDDING_BASE_URL")
    if embedding_base_url and not _optional_env("OPENAI_BASE_URL"):
        # OpenAI-compatible providers share this env var in SDK clients.
        os.environ["OPENAI_BASE_URL"] = embedding_base_url


def _build_memsearch(
    *,
    user_id: str,
    user_root: Path,
    collection: str,
    milvus_uri: str,
    milvus_token: str,
    embedding_provider: str,
    embedding_model: str | None,
    compact_llm_provider: str,
    compact_llm_model: str | None,
    enable_rerank: bool,
    rerank_api_base: str,
    rerank_api_key_env: str,
    rerank_model: str,
) -> MemSearch:
    memory_cfg = MemoryConfig(
        base_dir=str(user_root.parent),
        user_id="",
        short_memory_dir="short-memory",
        long_memory_dir="long-memory",
        keywords=["记住", "remember", "备忘"],
        short_interval_seconds=1,
        long_interval_seconds=1,
        auto_consolidate=True,
        consolidation_days=7,
    )
    rerank_cfg = RerankConfig(
        enabled=enable_rerank,
        provider="api",
        model=rerank_model,
        top_k_multiplier=3,
        api_base=rerank_api_base,
        api_key_env=rerank_api_key_env,
        top_k_field="top_n",
        result_path="results",
        score_field="relevance_score",
        index_field="index",
    )
    return MemSearch(
        paths=[str(user_root)],
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        milvus_uri=milvus_uri,
        milvus_token=milvus_token,
        collection=collection,
        user_id=user_id,
        memory_base_dir=user_root.parent,
        memory_config=memory_cfg,
        compact_llm_provider=compact_llm_provider,
        compact_llm_model=compact_llm_model,
        reranker="api" if enable_rerank else None,
        rerank_model=rerank_model if enable_rerank else None,
        rerank_config=rerank_cfg,
    )


def _print_results(prefix: str, rows: Iterable[dict], limit: int = 3) -> None:
    rows = list(rows)
    if not rows:
        print(f"{prefix}: no results")
        return
    for i, row in enumerate(rows[:limit], start=1):
        score = float(row.get("score", 0.0))
        source = row.get("source", "?")
        heading = row.get("heading", "")
        content = str(row.get("content", "")).replace("\n", " ").strip()
        if len(content) > 120:
            content = content[:120] + "..."
        print(f"{prefix} #{i} score={score:.4f} source={source} heading={heading}")
        print(f"  {content}")


def _escape_filter_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


async def _run(args: argparse.Namespace) -> None:
    _echo("Environment Validation")
    _require_env("OPENAI_API_KEY")
    milvus_uri = args.milvus_uri or _require_env("MILVUS_URI")
    milvus_token = args.milvus_token or _require_env("MILVUS_TOKEN")
    rerank_api_base = args.rerank_api_base or _require_env("RERANK_API_BASE")
    _require_env(args.rerank_api_key_env)
    print("OPENAI_API_KEY: set")
    print(f"MILVUS_URI: {milvus_uri}")
    print("MILVUS_TOKEN: set")
    print(f"RERANK_API_BASE: {rerank_api_base}")
    print(f"{args.rerank_api_key_env}: set")
    print(f"EMBEDDING_PROVIDER: {args.embedding_provider}")
    print(f"EMBEDDING_MODEL: {args.embedding_model or '(provider default)'}")
    print(f"COMPACT_LLM_MODEL: {args.compact_llm_model or '(provider default)'}")

    base_dir = Path(args.memory_base).expanduser().resolve()
    user_a_root = base_dir / args.user_a
    user_b_root = base_dir / args.user_b
    user_a_root.mkdir(parents=True, exist_ok=True)
    user_b_root.mkdir(parents=True, exist_ok=True)
    print(f"Demo memory base: {base_dir}")
    print(f"Collection: {args.collection}")

    mem_a_plain = _build_memsearch(
        user_id=args.user_a,
        user_root=user_a_root,
        collection=args.collection,
        milvus_uri=milvus_uri,
        milvus_token=milvus_token,
        embedding_provider=args.embedding_provider,
        embedding_model=args.embedding_model,
        compact_llm_provider=args.compact_llm_provider,
        compact_llm_model=args.compact_llm_model,
        enable_rerank=False,
        rerank_api_base=rerank_api_base,
        rerank_api_key_env=args.rerank_api_key_env,
        rerank_model=args.rerank_model,
    )
    mem_a_rerank = _build_memsearch(
        user_id=args.user_a,
        user_root=user_a_root,
        collection=args.collection,
        milvus_uri=milvus_uri,
        milvus_token=milvus_token,
        embedding_provider=args.embedding_provider,
        embedding_model=args.embedding_model,
        compact_llm_provider=args.compact_llm_provider,
        compact_llm_model=args.compact_llm_model,
        enable_rerank=True,
        rerank_api_base=rerank_api_base,
        rerank_api_key_env=args.rerank_api_key_env,
        rerank_model=args.rerank_model,
    )
    mem_b_plain = _build_memsearch(
        user_id=args.user_b,
        user_root=user_b_root,
        collection=args.collection,
        milvus_uri=milvus_uri,
        milvus_token=milvus_token,
        embedding_provider=args.embedding_provider,
        embedding_model=args.embedding_model,
        compact_llm_provider=args.compact_llm_provider,
        compact_llm_model=args.compact_llm_model,
        enable_rerank=False,
        rerank_api_base=rerank_api_base,
        rerank_api_key_env=args.rerank_api_key_env,
        rerank_model=args.rerank_model,
    )

    try:
        _echo("Short Memory Write + Dedup (User A)")
        write1 = await mem_a_plain.memory.write_short(
            "我们决定 API 鉴权采用 JWT + refresh token。",
            source="manual",
            session_id="demo-session-a",
            turn_id="turn-001",
        )
        write_dup_turn = await mem_a_plain.memory.write_short(
            "重复 turn 的写入应被跳过。",
            source="manual",
            session_id="demo-session-a",
            turn_id="turn-001",
        )
        write_dup_hash = await mem_a_plain.memory.write_short(
            "我们决定 API 鉴权采用 JWT + refresh token。",
            source="manual",
        )
        await mem_a_plain.memory.write_short(
            "限流策略：每用户 100 req/min，异常请求进入熔断观察。",
            source="manual",
        )
        print(f"first write: {write1}")
        print(f"duplicate turn write skipped: {write_dup_turn is None}")
        print(f"duplicate content write skipped: {write_dup_hash is None}")

        _echo("Trigger Demo (Keyword + Scheduled)")
        trigger_keyword = await mem_a_plain.memory.on_input(
            "这条请记住：数据库版本固定为 PostgreSQL 15。"
        )
        print(
            f"keyword_triggered={trigger_keyword.keyword_triggered}, "
            f"matched_keyword={trigger_keyword.matched_keyword}, "
            f"short_path={trigger_keyword.short_memory_path}"
        )

        trigger_scheduled_1 = await mem_a_plain.memory.on_input(
            "scheduled input one without keyword"
        )
        print(f"scheduled_short_path_1={trigger_scheduled_1.short_memory_path}")
        time.sleep(1.2)
        trigger_scheduled_2 = await mem_a_plain.memory.on_input(
            "scheduled input two without keyword"
        )
        print(f"scheduled_short_path_2={trigger_scheduled_2.short_memory_path}")
        time.sleep(1.2)
        tick_result = await mem_a_plain.memory.on_tick()
        print(f"tick_long_paths={tick_result.long_memory_paths or {}}")

        _echo("User Isolation Setup (User B)")
        await mem_b_plain.memory.write_short(
            "用户B的关键事实：认证采用 API Key，不使用 JWT。",
            source="manual",
        )
        await mem_b_plain.memory.write_short(
            "用户B的限流策略：每用户 10 req/min。",
            source="manual",
        )
        print("User B memory written.")

        _echo("Long Memory Consolidation (User A)")
        long_topics = await mem_a_plain.memory.consolidate(days=7, force=True)
        print(f"topics_written={list(long_topics.keys())}")
        if long_topics:
            topic_name = next(iter(long_topics))
            topic_text = mem_a_plain.memory.long.read(topic_name)
            snippet = topic_text[:280].replace("\n", " ")
            print(f"topic_sample={topic_name}")
            print(f"topic_snippet={snippet}...")
        long_topics_second = await mem_a_plain.memory.consolidate(days=7, force=False)
        print(f"second_consolidate_topics={list(long_topics_second.keys())}")

        _echo("Index")
        indexed_a = await mem_a_plain.index(force=True)
        indexed_b = await mem_b_plain.index(force=True)
        print(f"user_a indexed chunks={indexed_a}")
        print(f"user_b indexed chunks={indexed_b}")

        _echo("Search Isolation Compare")
        query = args.query
        results_a = await mem_a_plain.search(query, top_k=5, user_id=args.user_a)
        results_b = await mem_b_plain.search(query, top_k=5, user_id=args.user_b)
        _print_results("user_a", results_a)
        _print_results("user_b", results_b)

        _echo("Search with filter_expr")
        if results_a:
            source = str(results_a[0].get("source", ""))
            filter_expr = f'source == "{_escape_filter_literal(source)}"'
            filtered = await mem_a_plain.search(
                query,
                top_k=5,
                user_id=args.user_a,
                filter_expr=filter_expr,
            )
            print(f"filter_expr={filter_expr}")
            _print_results("filtered_user_a", filtered)
        else:
            print("No user_a results, skip filter demo.")

        _echo("Reranker Compare (API)")
        baseline = await mem_a_plain.search(query, top_k=5, user_id=args.user_a)
        reranked = await mem_a_rerank.search(query, top_k=5, user_id=args.user_a)
        print("baseline (hybrid RRF only):")
        _print_results("baseline", baseline)
        print("reranked (hybrid + API reranker):")
        _print_results("reranked", reranked)

        _echo("Done")
        print("Full feature demo finished successfully.")
    finally:
        mem_a_plain.close()
        mem_a_rerank.close()
        mem_b_plain.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run full-feature memsearch demo (OpenAI + Zilliz + API reranker)."
    )
    parser.add_argument("--memory-base", default="./memory/demo-full", help="Base memory directory.")
    parser.add_argument("--collection", default="memsearch_demo_full", help="Milvus collection.")
    parser.add_argument("--user-a", default="demo_alice", help="User A ID.")
    parser.add_argument("--user-b", default="demo_bob", help="User B ID.")
    parser.add_argument("--query", default="认证和限流的关键决策是什么？", help="Demo query.")

    parser.add_argument("--milvus-uri", default=_optional_env("MILVUS_URI"), help="Milvus URI.")
    parser.add_argument("--milvus-token", default=_optional_env("MILVUS_TOKEN"), help="Milvus token.")

    parser.add_argument(
        "--embedding-provider",
        default=_optional_env("EMBEDDING_PROVIDER", "openai"),
        help="Embedding provider.",
    )
    parser.add_argument(
        "--embedding-model",
        default=_optional_env("EMBEDDING_MODEL") or None,
        help="Embedding model override.",
    )
    parser.add_argument(
        "--compact-llm-provider",
        default=_optional_env("COMPACT_LLM_PROVIDER", "openai"),
        help="LLM provider for consolidation.",
    )
    parser.add_argument(
        "--compact-llm-model",
        default=_optional_env("COMPACT_LLM_MODEL")
        or _optional_env("OPENAI_MODEL")
        or None,
        help="LLM model for consolidation.",
    )

    parser.add_argument(
        "--rerank-api-base",
        default=_optional_env("RERANK_API_BASE"),
        help="Reranker API base URL (for provider=api).",
    )
    parser.add_argument(
        "--rerank-api-key-env",
        default=_optional_env("RERANK_API_KEY_ENV", "RERANK_API_KEY"),
        help="Environment variable name holding reranker API key.",
    )
    parser.add_argument(
        "--rerank-model",
        default=_optional_env("RERANK_MODEL", "BAAI/bge-reranker-v2-m3"),
        help="Reranker model name.",
    )
    return parser


if __name__ == "__main__":
    env_loaded = _load_dotenv(Path(__file__).with_name(".env"))
    _apply_env_aliases()
    if env_loaded:
        print(f"Loaded env file: {Path(__file__).with_name('.env')}")
    args = _build_parser().parse_args()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    except Exception as exc:  # pragma: no cover
        print(f"\nDemo failed: {exc}")
        raise
