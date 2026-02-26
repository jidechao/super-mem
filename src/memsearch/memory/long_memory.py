"""Long-term memory manager.

Builds topic-based markdown files from short-term memories:
    <base_dir>/<user_id>/long-memory/<topic>.md
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from ..compact import compact_chunks
from .short_memory import ShortMemoryManager

TOPIC_EXTRACTION_PROMPT = """\
分析以下短期记忆内容，提取关键决策和重要事实，按主题分组。

短期记忆：
{chunks}

输出 JSON：
{{
  "topics": [
    {{
      "name": "主题名称",
      "content": "Markdown 内容",
      "sources": ["YYYY-MM-DD"]
    }}
  ]
}}

只返回 JSON，不要额外解释。"""

MERGE_PROMPT = """\
你是知识合并助手。给定一个主题的[已有记忆]和[新增内容]：
1. 合并两者，消除语义重复（同一事实只保留一次）
2. 如果新内容与已有内容矛盾，以新内容为准并保留更新后的结论
3. 保持结构化格式（标题 + 要点列表）
4. 在来源部分保留并追加新来源
5. 输出完整 Markdown（不要解释）

内容：
{chunks}
"""


class LongMemoryManager:
    """Manage per-user topic files and LLM consolidation."""

    WATERMARK_FILE = ".last_consolidation"
    _TOPIC_FILENAME_PATTERN = re.compile(r'[<>:"/\\|?*\n\r\t]+')

    def __init__(
        self,
        base_dir: Path | str,
        user_id: str,
        short_memory: ShortMemoryManager,
        *,
        llm_provider: str = "openai",
        llm_model: str | None = None,
        long_memory_dir: str = "long-memory",
    ) -> None:
        self.base_dir = Path(base_dir)
        self.user_id = user_id
        self.short_memory = short_memory
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.dir = self.base_dir / user_id / long_memory_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    async def consolidate(self, days: int = 7, *, force: bool = False) -> dict[str, Path]:
        """Consolidate short memories into topic files with dual dedup."""
        watermark = None if force else self._read_watermark()
        if watermark is not None:
            files = self.short_memory.list_files_since(watermark)
        else:
            files = self.short_memory.list_files(days=days)

        if not files:
            return {}

        chunks: list[dict[str, Any]] = []
        for file in files:
            text = file.read_text(encoding="utf-8").strip()
            if not text:
                continue
            chunks.append({"content": f"[来源日期:{file.stem}]\n{text}"})
        if not chunks:
            return {}

        extraction = await compact_chunks(
            chunks,
            llm_provider=self.llm_provider,
            model=self.llm_model,
            prompt_template=TOPIC_EXTRACTION_PROMPT,
        )
        topics = self._parse_topics(extraction)

        written: dict[str, Path] = {}
        for topic in topics:
            name = str(topic.get("name", "")).strip()
            content = str(topic.get("content", "")).strip()
            if not name or not content:
                continue

            sources = topic.get("sources")
            if isinstance(sources, list):
                normalized_sources = [str(s).strip() for s in sources if str(s).strip()]
            else:
                normalized_sources = []

            merged = await self._merge_into_topic(
                name,
                self._inject_sources(content, normalized_sources),
            )
            path = await self.write(name, merged)
            written[name] = path

        self._write_watermark(date.today())
        return written

    async def _merge_into_topic(self, topic: str, new_content: str) -> str:
        """Merge new topic content with existing topic memory semantically."""
        existing = self.read(topic)
        if not existing.strip():
            return self._ensure_document_shape(topic, new_content)

        merged = await compact_chunks(
            [
                {"content": f"[已有记忆]\n{existing}"},
                {"content": f"[新增内容]\n{new_content}"},
            ],
            llm_provider=self.llm_provider,
            model=self.llm_model,
            prompt_template=MERGE_PROMPT,
        )
        if not merged.strip():
            merged = f"{existing.rstrip()}\n\n{new_content.strip()}\n"

        return self._ensure_document_shape(topic, merged)

    async def write(self, topic: str, content: str) -> Path:
        """Write full content to a topic file."""
        path = self._topic_file(topic)
        final = self._ensure_document_shape(topic, content)
        path.write_text(final, encoding="utf-8")
        return path

    def list_topics(self) -> list[str]:
        """List all topic names (without .md)."""
        return sorted(file.stem for file in self.dir.glob("*.md"))

    def read(self, topic: str) -> str:
        """Read a topic file. Returns empty string when not found."""
        path = self._topic_file(topic)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def _read_watermark(self) -> date | None:
        """Read consolidate watermark date."""
        file = self.dir / self.WATERMARK_FILE
        if not file.exists():
            return None
        raw = file.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None

    def _write_watermark(self, watermark_date: date) -> None:
        """Persist consolidate watermark date."""
        file = self.dir / self.WATERMARK_FILE
        file.write_text(watermark_date.isoformat(), encoding="utf-8")

    def _topic_file(self, topic: str) -> Path:
        safe = self._TOPIC_FILENAME_PATTERN.sub("_", topic).strip(" ._")
        if not safe:
            safe = "untitled-topic"
        return self.dir / f"{safe}.md"

    def _inject_sources(self, content: str, sources: list[str]) -> str:
        if not sources:
            return content
        if "## 来源" in content:
            return content

        source_lines = "\n".join(f"- {src}" for src in sources)
        return f"{content.rstrip()}\n\n## 来源\n{source_lines}\n"

    def _ensure_document_shape(self, topic: str, content: str) -> str:
        body = content.strip()
        if not body:
            body = "## 关键决策\n\n- 暂无内容\n"

        if not body.startswith("# "):
            body = f"# {topic}\n\n{body}"

        if "> 最后更新:" in body:
            body = re.sub(r"> 最后更新:.*", f"> 最后更新: {date.today().isoformat()}", body, count=1)
        else:
            lines = body.splitlines()
            header = lines[0]
            rest = "\n".join(lines[1:]).lstrip()
            body = f"{header}\n\n> 最后更新: {date.today().isoformat()}\n\n{rest}".rstrip()

        return body.rstrip() + "\n"

    def _parse_topics(self, raw: str) -> list[dict[str, Any]]:
        payload = self._extract_json_payload(raw)
        if not payload:
            return []

        topics = payload.get("topics", [])
        if not isinstance(topics, list):
            return []

        normalized: list[dict[str, Any]] = []
        for topic in topics:
            if not isinstance(topic, dict):
                continue
            normalized.append(topic)
        return normalized

    def _extract_json_payload(self, raw: str) -> dict[str, Any]:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        try:
            loaded = json.loads(text)
            return loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            pass

        # Fallback: extract first JSON object block.
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}

        try:
            loaded = json.loads(match.group(0))
            return loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            return {}
