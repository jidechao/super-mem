"""Run a full real-environment E2E chain for memsearch and emit a report.

Usage:
  python scripts/e2e_real_chain.py run --cleanup-after
  python scripts/e2e_real_chain.py cleanup --collection <name> --memory-path <path>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from openai import OpenAI

from memsearch import MemSearch
from memsearch.config import MemoryConfig, RerankConfig


@dataclass
class StepResult:
    name: str
    status: str
    details: dict[str, Any]
    duration_ms: int


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_env_file(path: Path) -> bool:
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


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


def build_memsearch(
    *,
    user_id: str,
    user_root: Path,
    collection: str,
    enable_rerank: bool,
) -> MemSearch:
    memory_cfg = MemoryConfig(
        base_dir=str(user_root.parent),
        user_id="",
        short_memory_dir="short-memory",
        long_memory_dir="long-memory",
        keywords=["remember", "important", "jwt"],
        short_interval_seconds=1,
        long_interval_seconds=1,
        auto_consolidate=True,
        consolidation_days=7,
    )
    rerank_cfg = RerankConfig(
        enabled=enable_rerank,
        provider="api",
        model=os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-v2-m3"),
        api_base=require_env("RERANK_API_BASE"),
        api_key_env="RERANK_API_KEY",
        timeout_seconds=90.0,
        max_retries=5,
        retry_base_delay=0.5,
        retry_max_delay=5.0,
        top_k_multiplier=3,
    )
    return MemSearch(
        paths=[str(user_root)],
        user_id=user_id,
        memory_base_dir=user_root.parent,
        memory_config=memory_cfg,
        collection=collection,
        milvus_uri=require_env("MILVUS_URI"),
        milvus_token=require_env("MILVUS_TOKEN"),
        embedding_provider=os.environ.get("EMBEDDING_PROVIDER", "openai"),
        embedding_model=os.environ.get("EMBEDDING_MODEL") or None,
        compact_llm_provider="openai",
        compact_llm_model=os.environ.get("OPENAI_MODEL") or None,
        compact_timeout_seconds=90.0,
        compact_max_retries=5,
        compact_retry_base_delay=0.5,
        compact_retry_max_delay=5.0,
        reranker="api" if enable_rerank else None,
        rerank_model=os.environ.get("RERANK_MODEL") or None,
        rerank_config=rerank_cfg,
    )


def seed_history(user_root: Path) -> dict[str, str]:
    short_dir = user_root / "short-memory"
    long_dir = user_root / "long-memory"
    short_dir.mkdir(parents=True, exist_ok=True)
    long_dir.mkdir(parents=True, exist_ok=True)

    files: dict[str, str] = {}
    f1 = short_dir / "2026-02-28.md"
    f1.write_text(
        "# 2026-02-28\n\n"
        "## 10:00 [manual]\n"
        "- Auth strategy uses JWT + refresh token.\n\n"
        "## 10:20 [manual]\n"
        "- Rate limit: 100 req/min per user.\n",
        encoding="utf-8",
    )
    files["short_1"] = str(f1)

    f2 = short_dir / "2026-03-01.md"
    f2.write_text(
        "# 2026-03-01\n\n"
        "## 09:10 [manual]\n"
        "- Database version requirement is PostgreSQL 15.\n\n"
        "## 09:45 [manual]\n"
        "- Circuit breaker: trigger for elevated 5xx.\n",
        encoding="utf-8",
    )
    files["short_2"] = str(f2)

    f3 = long_dir / "api-auth-mechanism.md"
    f3.write_text(
        "# API Auth Mechanism\n\n"
        "> Last update: 2026-03-01\n\n"
        "- External API authentication uses JWT + refresh token.\n",
        encoding="utf-8",
    )
    files["long_1"] = str(f3)
    return files


def _record_step(
    steps: list[StepResult],
    *,
    name: str,
    started: float,
    details: dict[str, Any],
    status: str = "ok",
) -> None:
    steps.append(
        StepResult(
            name=name,
            status=status,
            details=details,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    )


def _hits_preview(hits: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in hits[:limit]:
        out.append(
            {
                "score": float(row.get("score", 0.0)),
                "memory_type": row.get("memory_type", ""),
                "source": row.get("source", ""),
                "heading": row.get("heading", ""),
            }
        )
    return out


async def run_chain(args: argparse.Namespace) -> dict[str, Any]:
    env_loaded = load_env_file(Path(args.env_file))
    # Required for real environment
    require_env("OPENAI_API_KEY")
    require_env("MILVUS_URI")
    require_env("MILVUS_TOKEN")
    require_env("RERANK_API_BASE")
    require_env("RERANK_API_KEY")

    ts = int(time.time())
    memory_base = Path(args.memory_root_prefix).expanduser().resolve() / str(ts)
    user_id = args.user_id
    user_root = memory_base / user_id
    collection = f"{args.collection_prefix}_{ts}"
    query = args.query
    started_at = _utc_now_iso()

    steps: list[StepResult] = []
    plain = build_memsearch(
        user_id=user_id,
        user_root=user_root,
        collection=collection,
        enable_rerank=False,
    )
    rerank = build_memsearch(
        user_id=user_id,
        user_root=user_root,
        collection=collection,
        enable_rerank=not args.no_rerank,
    )

    failed_reason = ""
    ok = False
    try:
        t0 = time.perf_counter()
        files = seed_history(user_root)
        _record_step(steps, name="seed_history", started=t0, details={"files": files})

        t0 = time.perf_counter()
        indexed = await plain.index(force=True)
        _record_step(steps, name="index_history", started=t0, details={"indexed_chunks": indexed})

        t0 = time.perf_counter()
        base_hits = await plain.search(query, top_k=5, user_id=user_id)
        _record_step(
            steps,
            name="recall_base",
            started=t0,
            details={"count": len(base_hits), "hits": _hits_preview(base_hits)},
        )

        t0 = time.perf_counter()
        reranked_hits = (
            await rerank.search(query, top_k=5, user_id=user_id)
            if not args.no_rerank
            else base_hits
        )
        _record_step(
            steps,
            name="recall_rerank",
            started=t0,
            details={"count": len(reranked_hits), "hits": _hits_preview(reranked_hits)},
        )

        t0 = time.perf_counter()
        short_hits = [h for h in reranked_hits if h.get("memory_type") == "short"]
        long_hits = [h for h in reranked_hits if h.get("memory_type") == "long"]
        blocks: list[str] = []
        blocks.extend([f"[SHORT]{h.get('content','')[:200]}" for h in short_hits[:2]])
        blocks.extend([f"[LONG]{h.get('content','')[:200]}" for h in long_hits[:2]])
        context = "\n".join(blocks)
        _record_step(
            steps,
            name="assemble_context",
            started=t0,
            details={
                "short_hits": len(short_hits),
                "long_hits": len(long_hits),
                "context_len": len(context),
            },
        )

        t0 = time.perf_counter()
        client = OpenAI(
            api_key=require_env("OPENAI_API_KEY"),
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
        )
        model = os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an enterprise knowledge assistant. "
                        "Answer only from provided memory context."
                    ),
                },
                {"role": "user", "content": f"Question: {query}\n\nContext:\n{context}"},
            ],
            temperature=0.2,
        )
        answer = (resp.choices[0].message.content or "").strip()
        _record_step(
            steps,
            name="llm_answer",
            started=t0,
            details={"model": model, "answer_preview": answer[:280]},
        )

        t0 = time.perf_counter()
        written = await plain.memory.write_short(
            f"User question: {query}\nAssistant answer: {answer}",
            source="agent",
            session_id="e2e-real-session",
            turn_id="turn-001",
        )
        indexed_short = 0
        if written:
            indexed_short = await plain.index_file(written)
        _record_step(
            steps,
            name="write_short_and_index",
            started=t0,
            details={
                "written_short": str(written) if written else "",
                "indexed_new_from_short": indexed_short,
            },
        )

        t0 = time.perf_counter()
        time.sleep(1.2)
        tick = await plain.memory.on_tick()
        long_paths = list((tick.long_memory_paths or {}).values())
        indexed_long = 0
        for p in long_paths:
            indexed_long += await plain.index_file(p)
        _record_step(
            steps,
            name="auto_long_extract_and_index",
            started=t0,
            details={
                "auto_long_paths": [str(p) for p in long_paths],
                "indexed_new_from_long": indexed_long,
            },
        )

        t0 = time.perf_counter()
        final_hits = await plain.search(
            "How does long-term memory describe auth strategy?",
            top_k=5,
            user_id=user_id,
        )
        _record_step(
            steps,
            name="final_verify_search",
            started=t0,
            details={"count": len(final_hits), "hits": _hits_preview(final_hits)},
        )

        ok = True
    except Exception as exc:  # pragma: no cover - real env script
        failed_reason = f"{exc.__class__.__name__}: {exc}"
        _record_step(
            steps,
            name="failure",
            started=time.perf_counter(),
            details={"reason": failed_reason},
            status="failed",
        )
    finally:
        plain.close()
        rerank.close()

    ended_at = _utc_now_iso()
    report: dict[str, Any] = {
        "ok": ok,
        "started_at_utc": started_at,
        "ended_at_utc": ended_at,
        "env_file_loaded": env_loaded,
        "collection": collection,
        "memory_base": str(memory_base),
        "user_id": user_id,
        "query": query,
        "rerank_enabled": not args.no_rerank,
        "failed_reason": failed_reason,
        "steps": [asdict(s) for s in steps],
    }

    if args.cleanup_after:
        cleanup_status = cleanup_targets(
            collection=collection,
            memory_path=memory_base,
            env_file=Path(args.env_file),
        )
        report["cleanup_after"] = cleanup_status

    return report


def write_report(report: dict[str, Any], report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = report_dir / f"e2e_real_chain_{ts}.json"
    md_path = report_dir / f"e2e_real_chain_{ts}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines: list[str] = []
    lines.append("# E2E Real Chain Report")
    lines.append("")
    lines.append(f"- ok: `{report.get('ok')}`")
    lines.append(f"- collection: `{report.get('collection')}`")
    lines.append(f"- memory_base: `{report.get('memory_base')}`")
    lines.append(f"- rerank_enabled: `{report.get('rerank_enabled')}`")
    if report.get("failed_reason"):
        lines.append(f"- failed_reason: `{report.get('failed_reason')}`")
    lines.append("")
    lines.append("## Steps")
    lines.append("")
    for step in report.get("steps", []):
        lines.append(
            f"- `{step.get('name')}` status=`{step.get('status')}` "
            f"duration_ms=`{step.get('duration_ms')}`"
        )
    lines.append("")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def cleanup_targets(*, collection: str, memory_path: Path, env_file: Path) -> dict[str, Any]:
    loaded = load_env_file(env_file)
    out: dict[str, Any] = {"env_file_loaded": loaded, "collection": collection, "memory_path": str(memory_path)}

    # remote collection cleanup
    try:
        from pymilvus import MilvusClient

        uri = require_env("MILVUS_URI")
        token = require_env("MILVUS_TOKEN")
        client = MilvusClient(uri=uri, token=token)
        collections = set(client.list_collections())
        if collection in collections:
            client.drop_collection(collection)
            out["collection_dropped"] = True
        else:
            out["collection_dropped"] = False
        client.close()
    except Exception as exc:  # pragma: no cover - real env script
        out["collection_cleanup_error"] = f"{exc.__class__.__name__}: {exc}"

    # local memory path cleanup
    try:
        if memory_path.exists():
            shutil.rmtree(memory_path)
            out["memory_path_removed"] = True
        else:
            out["memory_path_removed"] = False
    except Exception as exc:  # pragma: no cover - real env script
        out["memory_cleanup_error"] = f"{exc.__class__.__name__}: {exc}"
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Real-environment E2E chain runner for memsearch.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run full E2E chain and emit report.")
    run.add_argument("--env-file", default="examples/.env", help="Path to env file.")
    run.add_argument("--memory-root-prefix", default="./memory/e2e-real", help="Memory root prefix.")
    run.add_argument("--collection-prefix", default="memsearch_e2e_real", help="Collection prefix.")
    run.add_argument("--user-id", default="real_user", help="Test user id.")
    run.add_argument("--query", default="What are our auth and rate-limit strategies?", help="Query text.")
    run.add_argument("--report-dir", default="./reports", help="Output directory for report files.")
    run.add_argument("--cleanup-after", action="store_true", help="Drop collection and delete local data after run.")
    run.add_argument("--no-rerank", action="store_true", help="Disable API rerank step.")

    cleanup = sub.add_parser("cleanup", help="Cleanup remote collection and local memory path.")
    cleanup.add_argument("--env-file", default="examples/.env", help="Path to env file.")
    cleanup.add_argument("--collection", required=True, help="Remote collection name to drop.")
    cleanup.add_argument("--memory-path", required=True, help="Local memory path to remove.")
    cleanup.add_argument("--report-dir", default="./reports", help="Output directory for report files.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    report_dir = Path(getattr(args, "report_dir", "./reports")).expanduser().resolve()

    if args.command == "run":
        report = asyncio.run(run_chain(args))
        json_path, md_path = write_report(report, report_dir)
        print(f"REPORT_JSON={json_path}")
        print(f"REPORT_MD={md_path}")
        print(f"E2E_REAL_CHAIN_OK={report.get('ok')}")
        return 0 if report.get("ok") else 1

    cleanup_result = cleanup_targets(
        collection=args.collection,
        memory_path=Path(args.memory_path).expanduser().resolve(),
        env_file=Path(args.env_file).expanduser().resolve(),
    )
    report = {
        "ok": True,
        "started_at_utc": _utc_now_iso(),
        "ended_at_utc": _utc_now_iso(),
        "command": "cleanup",
        "cleanup": cleanup_result,
    }
    json_path, md_path = write_report(report, report_dir)
    print(f"REPORT_JSON={json_path}")
    print(f"REPORT_MD={md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

