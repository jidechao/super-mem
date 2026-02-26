"""Short-term memory manager.

Stores daily markdown logs under:
    <base_dir>/<user_id>/short-memory/YYYY-MM-DD.md
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from pathlib import Path


class ShortMemoryManager:
    """Manage per-user daily short-term memory markdown files."""

    def __init__(
        self,
        base_dir: Path | str,
        user_id: str,
        short_memory_dir: str = "short-memory",
    ) -> None:
        self.base_dir = Path(base_dir)
        self.user_id = user_id
        self.dir = self.base_dir / user_id / short_memory_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    async def write(
        self,
        content: str,
        *,
        source: str = "manual",
        tags: list[str] | None = None,
        timestamp: datetime | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        transcript_path: str | None = None,
    ) -> Path | None:
        """Append one short-memory entry and return the file path.

        Returns ``None`` when dedup checks match an existing entry.
        """
        body = content.strip()
        if not body:
            return None

        ts = timestamp or datetime.now()
        target = self._file_for_date(ts.date())

        if turn_id and self._turn_exists(target, turn_id):
            return None

        content_hash = self._content_hash(body)
        if self._hash_exists(target, content_hash):
            return None

        entry_lines = [f"## {ts.strftime('%H:%M')} [{source}]"]
        if tags:
            entry_lines.append(f"<!-- tags:{','.join(tags)} -->")

        anchor_parts: list[str] = []
        if session_id:
            anchor_parts.append(f"session:{session_id}")
        if turn_id:
            anchor_parts.append(f"turn:{turn_id}")
        if transcript_path:
            anchor_parts.append(f"transcript:{transcript_path}")
        if anchor_parts:
            entry_lines.append(f"<!-- {' '.join(anchor_parts)} -->")

        entry_lines.append(f"<!-- hash:{content_hash} -->")
        entry_lines.extend(self._format_body(body))

        entry = "\n".join(entry_lines).rstrip() + "\n\n"
        self._append(target, entry)
        return target

    def read(self, day: str | None = None) -> str:
        """Read memory for a specific day (default: today)."""
        if day is None:
            target = self._file_for_date(date.today())
        else:
            target = self._file_for_date(date.fromisoformat(day))

        if not target.exists():
            return ""
        return target.read_text(encoding="utf-8")

    def list_files(self, days: int = 30) -> list[Path]:
        """List daily memory files within the last ``days`` days."""
        if days <= 0:
            return []
        threshold = date.today() - timedelta(days=days - 1)
        files: list[Path] = []
        for file in self.dir.glob("*.md"):
            file_day = self._parse_file_date(file)
            if file_day is None:
                continue
            if file_day >= threshold:
                files.append(file)
        return sorted(files)

    def list_files_since(self, since_date: date) -> list[Path]:
        """List daily memory files strictly after ``since_date``."""
        files: list[Path] = []
        for file in self.dir.glob("*.md"):
            file_day = self._parse_file_date(file)
            if file_day is None:
                continue
            if file_day > since_date:
                files.append(file)
        return sorted(files)

    def get_recent_content(self, days: int = 3, max_lines: int = 60) -> str:
        """Return compact content from recent daily files for cold start."""
        snippets: list[str] = []
        for file in sorted(self.list_files(days=days), reverse=True):
            text = file.read_text(encoding="utf-8").strip()
            if not text:
                continue
            snippets.append(f"## {file.name}\n{text}")

        if not snippets:
            return ""

        merged = "\n\n".join(snippets)
        lines = merged.splitlines()
        if max_lines > 0 and len(lines) > max_lines:
            lines = lines[-max_lines:]
        return "\n".join(lines).strip()

    def _turn_exists(self, file: Path, turn_id: str) -> bool:
        """Check whether a session-turn anchor already exists in this file."""
        if not file.exists():
            return False
        text = file.read_text(encoding="utf-8")
        pattern = rf"turn:{re.escape(turn_id)}(?:\s|-->)"
        return re.search(pattern, text) is not None

    def _content_hash(self, content: str) -> str:
        """Compute a short content hash for lightweight dedup."""
        return hashlib.md5(content.encode("utf-8")).hexdigest()[:16]  # noqa: S324

    def _hash_exists(self, file: Path, content_hash: str) -> bool:
        """Check whether the hash marker already exists in this file."""
        if not file.exists():
            return False
        text = file.read_text(encoding="utf-8")
        return f"<!-- hash:{content_hash} -->" in text

    def _file_for_date(self, day: date) -> Path:
        return self.dir / f"{day.isoformat()}.md"

    def _append(self, target: Path, entry: str) -> None:
        if not target.exists():
            target.write_text(f"# {target.stem}\n\n", encoding="utf-8")
        with open(target, "a", encoding="utf-8") as f:
            f.write(entry)

    def _parse_file_date(self, file: Path) -> date | None:
        try:
            return date.fromisoformat(file.stem)
        except ValueError:
            return None

    def _format_body(self, content: str) -> list[str]:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if not lines:
            return ["- "]
        out: list[str] = []
        for line in lines:
            if line.startswith(("-", "*", ">", "#")):
                out.append(line)
            else:
                out.append(f"- {line}")
        return out
