"""User identity resolution and sanitization for memory isolation."""

from __future__ import annotations

import getpass
import os
import re

_MAX_USER_ID_LEN = 128
_SANITIZE_PATTERN = re.compile(r"[^a-z0-9_-]+")


def resolve_user_id(
    explicit: str | None = None,
    config_value: str = "",
) -> str:
    """Resolve user ID by priority: explicit > env > config > system user."""
    if explicit:
        return _sanitize(explicit)

    env_user = os.environ.get("MEMSEARCH_USER", "")
    if env_user:
        return _sanitize(env_user)

    if config_value:
        return _sanitize(config_value)

    try:
        return _sanitize(getpass.getuser())
    except Exception:
        return "default"


def _sanitize(user_id: str) -> str:
    """Normalize user IDs for filesystem paths and Milvus filters."""
    normalized = user_id.strip().lower()
    normalized = _SANITIZE_PATTERN.sub("_", normalized)
    normalized = normalized.strip("_")
    if not normalized:
        return "default"
    return normalized[:_MAX_USER_ID_LEN]


__all__ = ["resolve_user_id"]
