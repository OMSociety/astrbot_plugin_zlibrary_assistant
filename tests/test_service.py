"""zlib_service：搜索记忆、翻页、序号解析、下载落盘与过期清理。"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest
from stubs import FakeClient, RecordingLogger, ZlibError, make_account, make_book

from astrbot_plugin_zlibrary_assistant import zlib_service
from astrbot_plugin_zlibrary_assistant.zlib_cards import SEARCH_CARD_TMPL
from astrbot_plugin_zlibrary_assistant.zlib_service import (
    DOWNLOAD_RETENTION_DAYS,
    MAX_SEARCH_LIMIT,
    ZlibService,
    format_book_lines,
)


@pytest.fixture
def make_service(tmp_path):
    def factory(client=None, *, renderer=None, **kw):
        client = client or FakeClient()
        return ZlibService(client, data_dir=tmp_path, renderer=renderer, **kw), client

    return factory


# ---------- search ----------


class TestSearch:
    @pytest.mark.asyncio
    async def test_default_limit_from_config_and_filters_forwarded(self, make_service):
        svc, client = make_service(search_limit=4)
        await svc.search("u1", "x", language="chinese", extension="pdf")
        assert client.search_calls == [
            {
                "query": "x",
                "limit": 4,
                "page": 1,
                "language": "chinese",
                "extension": "pdf",
            }
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("given", "used"), [(0, 5), (-3, 1), (99, MAX_SEARCH_LIMIT)]
    )
    async def test_limit_clamped(self, make_service, given, used):
        svc, client = make_service()
        await svc.search("u", "x", limit=given)
        assert client.search_calls[0]["limit"] == used

    @pytest.mark.asyncio
    async def test_result_remembered_per_session(self, make_service):
        svc, _ = make_service()
        outcome = await svc.search("u1", "x")
        assert svc.memory.recall("u1") is outcome.result
        assert svc.memory.recall("u2") is None

    @pytest.mark.asyncio
    async def test_client_errors_propagate_and_do_not_overwrite_memory(
        self, make_service
    ):
        svc, client = make_service()
        first = await svc.search("u", "x")

        async def boom(*a, **k):
            raise ZlibError("network_error", "down")

        client.search = boom
        with pytest.raises(ZlibError):
            await svc.search("u", "y")
        assert svc.memory.recall("u") is first.result


class TestSearchCard:
    @pytest.mark.asyncio
    async def test_card_rendered_with_template_and_covers(self, make_service):
        seen = {}

        async def renderer(tmpl, data, options, umo):
            seen.update(tmpl=tmpl, data=data, options=options, umo=umo)
            return "/tmp/card.jpg"

        books = [make_book(1, cover="https://8.8.8.8/1.jpg"), make_book(2)]
        svc, client = make_service(FakeClient(books), renderer=renderer)
        outcome = await svc.search("umo-1", "x")
        assert outcome.card_path == "/tmp/card.jpg"
        assert seen["tmpl"] == SEARCH_CARD_TMPL
        assert seen["umo"] == "umo-1"
        assert seen["data"]["books"][0]["cover"] == "data:image/jpeg;base64,AAAA"
        assert seen["data"]["books"][1]["cover"] == ""
        assert client.cover_calls == ["https://8.8.8.8/1.jpg", ""]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("card_enabled", [True, False])
    async def test_no_card_without_renderer(self, make_service, card_enabled):
        svc, _ = make_service(renderer=None, card_enabled=card_enabled)
        assert (await svc.search("u", "x")).card_path is None

    @pytest.mark.asyncio
    async def test_no_card_when_disabled(self, make_service):
        async def renderer(*a):
            raise AssertionError("不应渲染")

        svc, _ = make_service(renderer=renderer, card_enabled=False)
        assert (await svc.search("u", "x")).card_path is None

    @pytest.mark.asyncio
    async def test_no_card_for_empty_result(self, make_service):
        async def renderer(*a):
            raise AssertionError("不应渲染")

        svc, _ = make_service(FakeClient([]), renderer=renderer)
        outcome = await svc.search("u", "x")
        assert outcome.card_path is None
        assert outcome.result.books == []

    @pytest.mark.asyncio
    async def test_renderer_failure_degrades_to_text_only(
        self, make_service, monkeypatch
    ):
        monkeypatch.setattr(zlib_service, "logger", RecordingLogger())

        async def renderer(*a):
            raise RuntimeError("t2i down")

        svc, _ = make_service(renderer=renderer)
        outcome = await svc.search("u", "x")
        assert outcome.card_path is None
        assert len(outcome.result.books) == 3

    @pytest.mark.asyncio
    async def test_renderer_timeout_degrades(self, make_service, monkeypatch):
        monkeypatch.setattr(zlib_service, "logger", RecordingLogger())
        monkeypatch.setattr(zlib_service, "CARD_RENDER_TIMEOUT", 0.05)

        async def renderer(*a):
            await asyncio.sleep(5)

        svc, _ = make_service(renderer=renderer)
        assert (await svc.search("u", "x")).card_path is None


# ---------- turn_page ----------


class TestTurnPage:
    @pytest.mark.asyncio
    async def test_requires_previous_search(self, make_service):
        svc, _ = make_service()
        with pytest.raises(ZlibError, match="没有可翻页的搜索记录"):
            await svc.turn_page("u", 1)

    @pytest.mark.asyncio
    async def test_next_page_reuses_query_filters_and_page_size(self, make_service):
        svc, client = make_service(FakeClient(total_pages=3))
        await svc.search("u", "kw", language="chinese", extension="epub")
        outcome = await svc.turn_page("u", 1)
        assert outcome.result.page == 2
        assert client.search_calls[-1] == {
            "query": "kw",
            "limit": 3,
            "page": 2,
            "language": "chinese",
            "extension": "epub",
        }
        assert svc.memory.recall("u").page == 2

    @pytest.mark.asyncio
    async def test_previous_page(self, make_service):
        svc, client = make_service(FakeClient(total_pages=3))
        await svc.search("u", "kw", page=2)
        await svc.turn_page("u", -1)
        assert client.search_calls[-1]["page"] == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("step", "page", "total_pages", "message"),
        [(-1, 1, 3, "第一页"), (1, 2, 2, "最后一页")],
    )
    async def test_page_boundaries(
        self, make_service, step, page, total_pages, message
    ):
        svc, client = make_service(FakeClient(total_pages=total_pages, page=page))
        await svc.search("u", "kw", page=page)
        with pytest.raises(ZlibError, match=message):
            await svc.turn_page("u", step)
        assert len(client.search_calls) == 1

    @pytest.mark.asyncio
    async def test_sessions_do_not_share_pages(self, make_service):
        svc, _ = make_service()
        await svc.search("a", "kw")
        with pytest.raises(ZlibError):
            await svc.turn_page("b", 1)


# ---------- resolve_book ----------


class TestResolveBook:
    @pytest.mark.asyncio
    async def test_ordinal_within_page(self, make_service):
        svc, _ = make_service(
            FakeClient([make_book(1001), make_book(1002), make_book(1003)])
        )
        await svc.search("u", "x")
        assert svc.resolve_book("u", "1").id == 1001
        assert svc.resolve_book("u", "3").id == 1003

    @pytest.mark.asyncio
    async def test_number_beyond_page_is_looked_up_as_book_id(self, make_service):
        svc, client = make_service(FakeClient([make_book(1001), make_book(1002)]))
        await svc.search("u", "x")
        client.book_cache.put_many([make_book(1003)])
        assert svc.resolve_book("u", "1003").id == 1003

    @pytest.mark.asyncio
    async def test_ordinal_takes_precedence_over_same_numeric_id(self, make_service):
        svc, client = make_service(FakeClient([make_book(1001), make_book(1002)]))
        await svc.search("u", "x")
        client.book_cache.put_many([make_book(2, title="id-two")])
        assert svc.resolve_book("u", "2").id == 1002

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ref", ["0", "4"])
    async def test_out_of_range_ordinal_reports_page_size(self, make_service, ref):
        svc, _ = make_service()
        await svc.search("u", "x")
        with pytest.raises(ZlibError, match="序号超出范围（本页共 3 本）"):
            svc.resolve_book("u", ref)

    @pytest.mark.parametrize("ref", ["", "abc", "1.5", "-1", "①"])
    def test_non_numeric_rejected(self, make_service, ref):
        svc, _ = make_service()
        with pytest.raises(ZlibError, match="序号或 id"):
            svc.resolve_book("u", ref)

    def test_without_search_only_cached_ids_resolve(self, make_service):
        svc, client = make_service()
        client.book_cache.put_many([make_book(55)])
        assert svc.resolve_book("u", " 55 ").id == 55
        with pytest.raises(ZlibError, match="请先用 /zlib 搜索"):
            svc.resolve_book("u", "56")

    @pytest.mark.asyncio
    async def test_other_session_has_no_ordinals(self, make_service):
        svc, _ = make_service(FakeClient([make_book(1001), make_book(1002)]))
        await svc.search("a", "x")
        with pytest.raises(ZlibError, match="请先用 /zlib 搜索"):
            svc.resolve_book("b", "1")


# ---------- _target_path ----------


class TestTargetPath:
    def test_resolves_inside_download_dir(self, make_service):
        svc, _ = make_service()
        path = svc._target_path("book.pdf")
        assert path.name == "book.pdf"
        assert path.parent.resolve() == svc.download_dir.resolve()

    @pytest.mark.parametrize(
        "name",
        [
            "../evil.pdf",
            "..\\evil.pdf",
            "/abs/evil.pdf",
            "C:\\evil.pdf",
        ],
    )
    def test_escaping_names_rejected(self, make_service, name):
        svc, _ = make_service()
        with pytest.raises(ZlibError) as exc:
            svc._target_path(name)
        assert exc.value.category == "api_error"

    def test_sibling_directory_with_shared_prefix_is_not_inside(
        self, make_service, tmp_path
    ):
        svc, _ = make_service()
        (tmp_path / "books_evil").mkdir()
        with pytest.raises(ZlibError, match="不合法"):
            svc._target_path("../books_evil/x.pdf")
        assert not (tmp_path / "books_evil" / "x.pdf").exists()


# ---------- download / purge ----------


class TestDownload:
    @pytest.mark.asyncio
    async def test_streams_to_disk_and_reports_account_info(self, make_service):
        svc, client = make_service()
        account = make_account("acc", downloads_today=4, downloads_limit=10)
        client.download_result = (account, "My Book.pdf", b"CONTENT")
        done = await svc.download(make_book(1))
        assert done.path.read_bytes() == b"CONTENT"
        assert (done.filename, done.size, done.account_name, done.downloads_left) == (
            "My Book.pdf",
            7,
            "acc",
            6,
        )
        assert done.path.parent.name == "books"

    @pytest.mark.asyncio
    async def test_progress_callback_sees_final_size(self, make_service):
        svc, client = make_service()
        client.download_result = (make_account("a"), "b.pdf", b"0123456789")
        seen = []

        async def on_progress(read, total):
            seen.append((read, total))

        await svc.download(make_book(1), on_progress=on_progress)
        assert seen[-1] == (10, 10)

    @pytest.mark.asyncio
    async def test_client_error_propagates_without_writing(self, make_service):
        svc, client = make_service()
        client.download_error = ZlibError("quota_exhausted", "用完了")
        with pytest.raises(ZlibError) as exc:
            await svc.download(make_book(1))
        assert exc.value.category == "quota_exhausted"
        assert not svc.download_dir.exists() or not list(svc.download_dir.iterdir())

    @pytest.mark.asyncio
    async def test_hostile_filename_from_client_is_rejected(
        self, make_service, tmp_path
    ):
        svc, client = make_service()
        client.download_result = (make_account("acc"), "../../escape.pdf", b"x")
        with pytest.raises(ZlibError, match="不合法"):
            await svc.download(make_book(1))
        assert not (tmp_path.parent / "escape.pdf").exists()

    @pytest.mark.asyncio
    async def test_interrupted_transfer_leaves_no_partial_file(self, make_service):
        svc, client = make_service()

        async def broken(book, *, sink=None, on_progress=None):
            await sink("half.pdf", b"PART")
            raise ZlibError("network_error", "断了")

        client.download = broken
        with pytest.raises(ZlibError):
            await svc.download(make_book(1))
        assert not (svc.download_dir / "half.pdf").exists()

    @pytest.mark.asyncio
    async def test_os_error_on_open_reported_without_leaking_path(self, make_service):
        svc, client = make_service()
        svc.download_dir.mkdir(parents=True, exist_ok=True)
        (svc.download_dir / "taken").mkdir()
        client.download_result = (make_account("a"), "taken", b"x")
        with pytest.raises(ZlibError, match="保存文件失败"):
            await svc.download(make_book(1))

    @pytest.mark.asyncio
    async def test_same_name_overwritten(self, make_service):
        svc, client = make_service()
        client.download_result = (make_account("a"), "a.pdf", b"1")
        await svc.download(make_book(1))
        client.download_result = (make_account("a"), "a.pdf", b"22")
        done = await svc.download(make_book(1))
        assert done.path.read_bytes() == b"22"


class TestQuotaHint:
    def test_no_accounts_means_no_hint(self, make_service):
        svc, _ = make_service()
        assert svc.quota_hint() == ""

    def test_counts_only_logged_in_accounts(self, make_service):
        svc, client = make_service()
        client.accounts = [
            make_account("a", logged_in=True, downloads_today=1, downloads_limit=5),
            make_account("b", logged_in=True, downloads_today=4, downloads_limit=5),
            make_account("c", logged_in=False, downloads_today=0, downloads_limit=99),
        ]
        assert svc.quota_hint() == "今日剩余 5 次下载"

    def test_exhausted(self, make_service):
        svc, client = make_service()
        client.accounts = [
            make_account("a", logged_in=True, downloads_today=5, downloads_limit=5)
        ]
        assert svc.quota_hint() == "今日下载额度已用完"


class TestPurgeExpired:
    @staticmethod
    def _age(path, days):
        ts = time.time() - days * 86400
        os.utime(path, (ts, ts))

    @staticmethod
    def _write(svc, name: str) -> Path:
        svc.download_dir.mkdir(parents=True, exist_ok=True)
        path = svc.download_dir / name
        path.write_bytes(b"1")
        return path

    @pytest.mark.asyncio
    async def test_old_files_removed_recent_kept(self, make_service):
        svc, _ = make_service()
        old = self._write(svc, "old.pdf")
        fresh = self._write(svc, "fresh.pdf")
        self._age(old, DOWNLOAD_RETENTION_DAYS + 1)
        self._age(fresh, DOWNLOAD_RETENTION_DAYS - 1)
        svc._purge_expired()
        assert not old.exists()
        assert fresh.exists()

    def test_missing_download_dir_is_fine(self, make_service):
        svc, _ = make_service()
        svc._purge_expired()

    @pytest.mark.asyncio
    async def test_download_triggers_purge(self, make_service):
        svc, client = make_service()
        old = self._write(svc, "old.pdf")
        self._age(old, DOWNLOAD_RETENTION_DAYS + 2)
        client.download_result = (make_account("a"), "new.pdf", b"2")
        await svc.download(make_book(1))
        assert not old.exists()

    @pytest.mark.asyncio
    async def test_unremovable_file_only_logged(self, make_service, monkeypatch):
        rec = RecordingLogger()
        monkeypatch.setattr(zlib_service, "logger", rec)
        svc, _ = make_service()
        old = self._write(svc, "old.pdf")
        self._age(old, DOWNLOAD_RETENTION_DAYS + 1)

        def refuse(*a, **k):
            raise PermissionError("locked")

        monkeypatch.setattr(zlib_service.os, "remove", refuse)
        svc._purge_expired()
        assert old.exists()
        assert rec.lines


# ---------- 文本格式 ----------


class TestFormatBookLines:
    def test_numbered_lines_with_details(self):
        books = [
            make_book(
                1, title="T1", author="A1", extension="epub", size="2 MB", year="1999"
            ),
            make_book(2, title="T2"),
        ]
        assert format_book_lines(books)[0] == "1. T1 - A1 [EPUB 2 MB 1999]"
        assert format_book_lines(books)[1].startswith("2. T2 - ")

    def test_defaults_for_missing_title_and_author(self):
        (line,) = format_book_lines(
            [make_book(1, title="", author="", extension="", size="", year="")]
        )
        assert line == "1. 未知标题 - 未知作者"
