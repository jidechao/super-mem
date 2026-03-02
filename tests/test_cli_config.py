from __future__ import annotations

from memsearch.cli import _cfg_to_memsearch_kwargs_with_context
from memsearch.config import MemSearchConfig


def test_cli_passes_compact_runtime_settings_to_memsearch_kwargs():
    cfg = MemSearchConfig()
    cfg.compact.timeout_seconds = 42.5
    cfg.compact.max_retries = 7
    cfg.compact.retry_base_delay = 0.3
    cfg.compact.retry_max_delay = 3.3

    kwargs = _cfg_to_memsearch_kwargs_with_context(cfg, user="alice")
    assert kwargs["compact_timeout_seconds"] == 42.5
    assert kwargs["compact_max_retries"] == 7
    assert kwargs["compact_retry_base_delay"] == 0.3
    assert kwargs["compact_retry_max_delay"] == 3.3
