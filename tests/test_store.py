"""zlib_store：BookCache 原子落盘与容量裁剪；SearchMemory 会话隔离与 TTL。"""

from __future__ import annotations

import json
import os

import pytest
from stubs import RecordingLogger, make_book

from astrbot_plugin_zlibrary_assistant import zlib_store
from astrbot_plugin_zlibrary_assistant.zlib_models import SearchResult
from astrbot_plugin_zlibrary_assistant.zlib_store import BookCache, SearchMemory


class TestBookCache:
    def test_put_and_get(self):
        cache = BookCache()
        cache.put_many([make_book(1), make_book(2)])
        assert len(cache) == 2
        assert cache.get(2).id == 2
        assert cache.get(99) is None

    def test_capacity_evicts_oldest_first(self):
        cache = BookCache(capacity=3)
        cache.put_many([make_book(i) for i in range(1, 6)])
        assert len(cache) == 3
        assert cache.get(1) is None and cache.get(2) is None
        assert cache.get(5) is not None

    def test_reput_refreshes_position(self):
        cache = BookCache(capacity=3)
        cache.put_many([make_book(1), make_book(2), make_book(3)])
        cache.put_many([make_book(1)])
        cache.put_many([make_book(4)])
        assert cache.get(1) is not None
        assert cache.get(2) is None

    def test_clear(self):
        cache = BookCache()
        cache.put_many([make_book(1)])
        cache.clear()
        assert len(cache) == 0

    def test_flush_persists_and_reload_restores(self, tmp_path):
        path = tmp_path / "cache.json"
        cache = BookCache(path)
        cache.put_many([make_book(1, title="资本论"), make_book(2)])
        cache.flush()
        reloaded = BookCache(path)
        assert len(reloaded) == 2
        assert reloaded.get(1).title == "资本论"

    def test_flush_is_atomic_replace_without_leftovers(self, tmp_path):
        path = tmp_path / "cache.json"
        cache = BookCache(path)
        cache.put_many([make_book(1)])
        cache.flush()
        assert sorted(p.name for p in tmp_path.iterdir()) == ["cache.json"]
        json.loads(path.read_text(encoding="utf-8"))

    def test_failed_write_keeps_previous_file_and_cleans_temp(
        self, tmp_path, monkeypatch
    ):
        """写入中途失败时，原文件保持完整，临时文件被清理。"""
        path = tmp_path / "cache.json"
        first = BookCache(path)
        first.put_many([make_book(1)])
        first.flush()
        before = path.read_bytes()

        def boom(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(zlib_store.os, "replace", boom)
        monkeypatch.setattr(zlib_store, "logger", RecordingLogger())
        second = BookCache(path)
        second.put_many([make_book(2)])
        second.flush()
        assert path.read_bytes() == before
        assert [p.name for p in tmp_path.iterdir()] == ["cache.json"]
        assert zlib_store.logger.lines

    def test_flush_skipped_when_not_dirty(self, tmp_path, monkeypatch):
        path = tmp_path / "cache.json"
        cache = BookCache(path)
        calls = []
        monkeypatch.setattr(zlib_store.os, "replace", lambda *a: calls.append(a))
        cache.flush()
        assert calls == []
        assert not path.exists()

    def test_flush_without_path_is_noop(self):
        cache = BookCache()
        cache.put_many([make_book(1)])
        cache.flush()

    @pytest.mark.parametrize("content", ["{not json", '{"a": 1}', "", "null"])
    def test_corrupt_file_ignored(self, tmp_path, content):
        path = tmp_path / "cache.json"
        path.write_text(content, encoding="utf-8")
        assert len(BookCache(path)) == 0

    def test_load_keeps_latest_entries_within_capacity(self, tmp_path):
        path = tmp_path / "cache.json"
        big = BookCache(path, capacity=10)
        big.put_many([make_book(i) for i in range(1, 8)])
        big.flush()
        small = BookCache(path, capacity=3)
        assert len(small) == 3
        assert small.get(7) is not None and small.get(1) is None

    def test_written_file_has_no_temp_name_pattern_left_after_many_flushes(
        self, tmp_path
    ):
        cache = BookCache(tmp_path / "c.json")
        for i in range(5):
            cache.put_many([make_book(i)])
            cache.flush()
        assert not [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _result(q="q"):
    return SearchResult(q, [make_book(1)])


class TestSearchMemory:
    def test_sessions_isolated(self):
        mem = SearchMemory()
        mem.remember("u1", _result("a"))
        mem.remember("u2", _result("b"))
        assert mem.recall("u1").query == "a"
        assert mem.recall("u2").query == "b"
        assert mem.recall("u3") is None

    def test_remember_overwrites_same_session(self):
        mem = SearchMemory()
        mem.remember("u", _result("old"))
        mem.remember("u", _result("new"))
        assert mem.recall("u").query == "new"

    def test_ttl_expiry(self):
        clock = _Clock()
        mem = SearchMemory(ttl=100, clock=clock)
        mem.remember("u", _result())
        clock.now += 99
        assert mem.recall("u") is not None
        clock.now += 2
        assert mem.recall("u") is None

    def test_expired_entry_is_dropped(self):
        clock = _Clock()
        mem = SearchMemory(ttl=10, clock=clock)
        mem.remember("u", _result())
        clock.now += 11
        mem.recall("u")
        clock.now -= 11
        assert mem.recall("u") is None

    def test_capacity_evicts_oldest_session(self):
        mem = SearchMemory(capacity=2)
        for u in ("a", "b", "c"):
            mem.remember(u, _result(u))
        assert mem.recall("a") is None
        assert mem.recall("b") is not None and mem.recall("c") is not None
