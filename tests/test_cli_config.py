from __future__ import annotations

from click.testing import CliRunner

from memsearch.cli import _cfg_to_memsearch_kwargs_with_context, cli
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



def test_cli_search_preserves_explicit_zero_top_k(monkeypatch):
    captured: dict[str, object] = {}

    class FakeMemSearch:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, query, *, top_k, filter_expr, user_id):
            captured["query"] = query
            captured["top_k"] = top_k
            captured["filter_expr"] = filter_expr
            captured["user_id"] = user_id
            return []

        def close(self):
            return None

    monkeypatch.setattr("memsearch.core.MemSearch", FakeMemSearch)
    monkeypatch.setattr("memsearch.cli.resolve_config", lambda overrides=None: MemSearchConfig())

    runner = CliRunner()
    result = runner.invoke(cli, ["search", "probe", "--top-k", "0", "--json-output"])

    assert result.exit_code == 0
    assert captured["top_k"] == 0
