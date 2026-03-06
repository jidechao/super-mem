from __future__ import annotations

from pathlib import Path

from memsearch.core import MemSearch
from memsearch.watcher import FileWatcher


class _DummyStore:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []

    def delete_by_source(self, source: str, *, user_id: str = "") -> None:
        self.deleted.append((source, user_id))


class _FakeWatcher:
    def __init__(self, paths, callback, **kwargs):  # noqa: ANN001, ANN003
        self.paths = paths
        self.callback = callback
        self.kwargs = kwargs
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def test_watch_on_change_reports_error_without_raising(monkeypatch):
    import memsearch.watcher as watcher_mod

    monkeypatch.setattr(watcher_mod, "FileWatcher", _FakeWatcher)

    ms = MemSearch.__new__(MemSearch)
    ms._paths = ["dummy"]  # type: ignore[attr-defined]
    ms._user_id = "alice"  # type: ignore[attr-defined]
    ms._store = _DummyStore()  # type: ignore[attr-defined]

    async def _boom(_path):  # noqa: ANN001
        raise RuntimeError("index failed")

    ms.index_file = _boom  # type: ignore[method-assign]

    events: list[tuple[str, str, Path]] = []
    watcher = MemSearch.watch(ms, on_event=lambda t, s, p: events.append((t, s, p)))
    assert watcher.started is True

    watcher.callback("modified", Path("x.md"))
    assert len(events) == 1
    assert events[0][0] == "modified"
    assert "Error processing modified" in events[0][1]
    assert str(events[0][2]) == "x.md"


def test_watch_deleted_path_still_deletes(monkeypatch):
    import memsearch.watcher as watcher_mod

    monkeypatch.setattr(watcher_mod, "FileWatcher", _FakeWatcher)

    ms = MemSearch.__new__(MemSearch)
    ms._paths = ["dummy"]  # type: ignore[attr-defined]
    ms._user_id = "alice"  # type: ignore[attr-defined]
    store = _DummyStore()
    ms._store = store  # type: ignore[attr-defined]

    async def _ok(_path):  # noqa: ANN001
        return 1

    ms.index_file = _ok  # type: ignore[method-assign]

    events: list[tuple[str, str, Path]] = []
    watcher = MemSearch.watch(ms, on_event=lambda t, s, p: events.append((t, s, p)))
    watcher.callback("deleted", Path("x.md"))

    assert store.deleted == [("x.md", "alice")]
    assert len(events) == 1
    assert events[0][0] == "deleted"
    assert "Removed chunks for x.md" in events[0][1]


def test_file_watcher_watches_parent_directory_for_file_path(tmp_path, monkeypatch):
    target_file = tmp_path / "notes.md"
    target_file.write_text("# note\n", encoding="utf-8")
    scheduled: list[tuple[str, str, bool]] = []

    class _Observer:
        def schedule(self, handler, path, recursive=False):  # noqa: ANN001, ANN003
            scheduled.append((handler.__class__.__name__, path, recursive))

        def start(self):
            return None

        def stop(self):
            return None

        def join(self):
            return None

    monkeypatch.setattr("memsearch.watcher.Observer", _Observer)

    watcher = FileWatcher([target_file], lambda event_type, path: None)
    watcher.start()

    assert scheduled == [('_MarkdownHandler', str(tmp_path.resolve()), False)]
