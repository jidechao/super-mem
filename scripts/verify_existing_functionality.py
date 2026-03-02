"""Baseline smoke checks for existing memsearch functionality.

This script is intentionally lightweight and deterministic:
- runs with the current Python interpreter (expected: project .venv)
- avoids external APIs (no embedding providers required)
- validates CLI commands that should always be usable
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=check,
        capture_output=True,
        text=True,
        env=env,
    )


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    python = sys.executable

    with tempfile.TemporaryDirectory(prefix="memsearch-smoke-") as tmp:
        tmp_path = Path(tmp)
        base_dir = tmp_path / "memory-data"
        base_dir.mkdir(parents=True, exist_ok=True)
        milvus_uri = str(tmp_path / "smoke_milvus.db")
        collection = "smoke_collection"
        user = "smoke_user"

        env = os.environ.copy()
        src_path = str(repo_root / "src")
        env["PYTHONPATH"] = src_path + os.pathsep + env.get("PYTHONPATH", "")

        # 1) config command should be available and successful.
        out = _run([python, "-m", "memsearch", "config", "list"], env=env).stdout
        if "milvus" not in out:
            raise RuntimeError("config list output missing expected 'milvus' section")

        # 2) memory write/list/read basic flow should work.
        _run(
            [
                python,
                "-m",
                "memsearch",
                "memory",
                "write",
                "baseline smoke entry",
                "--user",
                user,
            ],
            env=env,
        )
        list_out = _run(
            [python, "-m", "memsearch", "memory", "list", "--days", "1", "--user", user],
            env=env,
        ).stdout
        if ".md" not in list_out:
            raise RuntimeError("memory list did not return any markdown file")

        read_out = _run(
            [python, "-m", "memsearch", "memory", "read", "--user", user],
            env=env,
        ).stdout
        if "baseline smoke entry" not in read_out:
            raise RuntimeError("memory read output missing written content")

        # 3) stats command should work when collection exists (create an empty collection).
        create_res = _run(
            [
                python,
                "-c",
                (
                    "from memsearch.store import MilvusStore;"
                    f"s=MilvusStore(uri=r'{milvus_uri}', collection='{collection}', dimension=4);"
                    "s.close()"
                ),
            ],
            env=env,
            check=False,
        )
        if create_res.returncode == 0:
            stats_out = _run(
                [
                    python,
                    "-m",
                    "memsearch",
                    "stats",
                    "--milvus-uri",
                    milvus_uri,
                    "--collection",
                    collection,
                ],
                env=env,
            ).stdout
            if "Total indexed chunks:" not in stats_out:
                raise RuntimeError("stats output missing expected summary line")
        else:
            # Some environments don't have a working local Milvus Lite runtime.
            # In that case, skip stats smoke but keep the rest of baseline checks.
            print("WARN: stats smoke skipped (local Milvus unavailable).", file=sys.stderr)

        # 4) optional check: transcript parser command should not crash on empty file.
        transcript = tmp_path / "t.jsonl"
        transcript.write_text("", encoding="utf-8")
        transcript_out = _run(
            [python, "-m", "memsearch", "transcript", str(transcript)],
            env=env,
        ).stdout
        if "No conversation turns found." not in transcript_out:
            raise RuntimeError("transcript output did not match expected empty-state")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
