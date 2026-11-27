"""main 指令入口：详情与 `/zlib 3` 简写的参数剥离、错误出口与交付方式。

只测指令层自身的行为（取参、出错文案、谁来发消息），搜索与下载的内部逻辑
已由 test_service / test_client 覆盖。
"""

from __future__ import annotations

import re
import tempfile
import types
from pathlib import Path

import pytest
from stubs import ZlibError, make_book

from astrbot_plugin_zlibrary_assistant import main as plugin_main

BOOK = make_book(7, title="State and Revolution", description="A short intro.")


class Result:
    """记录 plain_result/chain_result 产出的结果对象。"""

    def __init__(self, kind: str, payload):
        self.kind = kind
        self.payload = payload

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"Result({self.kind!r}, {self.payload!r})"


class Event:
    def __init__(self, message_str: str, *, admin: bool = False):
        self.message_str = message_str
        self.unified_msg_origin = "umo-1"
        self._admin = admin
        self.stopped = False

    def plain_result(self, text: str):
        return Result("plain", text)

    def chain_result(self, chain):
        return Result("chain", chain)

    def is_admin(self) -> bool:
        return self._admin

    def stop_event(self) -> None:
        self.stopped = True


class Service:
    """按需返回一本书的服务桩，记录下载过哪本。"""

    def __init__(self, *, book=BOOK, error: ZlibError | None = None):
        self.book = book
        self.error = error
        self.downloaded = []

    def resolve_book(self, umo, ref):
        if self.error is not None:
            raise self.error
        return self.book

    async def download(self, book, **_):
        self.downloaded.append(book)
        return types.SimpleNamespace(
            book=book,
            path=Path(tempfile.gettempdir()) / "book.pdf",
            filename="book.pdf",
            size=2 * 1024 * 1024,
            account_name="main",
            downloads_left=3,
            elapsed=1.0,
        )


def make_plugin(service=None, *, admin_only: bool = False):
    plugin = object.__new__(plugin_main.ZLibraryAssistantPlugin)
    plugin.service = service or Service()
    plugin.download_admin_only = admin_only
    return plugin


async def run(agen):
    return [r async for r in agen]


class TestDetail:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "message_str", ["zlib 详情 3", "/zlib 详情 3", "zl 详 3", "zlib detail 3"]
    )
    async def test_returns_full_detail_of_the_referenced_book(self, message_str):
        plugin = make_plugin()
        (result,) = await run(plugin.cmd_detail(Event(message_str)))
        text = result.payload
        assert result.kind == "plain"
        assert "State and Revolution" in text
        assert "A short intro." in text

    @pytest.mark.asyncio
    async def test_resolution_error_is_reported_as_plain_text(self):
        plugin = make_plugin(Service(error=ZlibError("api_error", "序号超出范围")))
        (result,) = await run(plugin.cmd_detail(Event("zlib 详情 99")))
        assert result.payload == "序号超出范围"


class TestBareRef:
    @pytest.mark.asyncio
    async def test_downloads_last_token_and_stops_the_event(self):
        service = Service()
        plugin = make_plugin(service)
        event = Event("zlib 7")
        results = await run(plugin.cmd_bare_ref(event))
        assert service.downloaded == [BOOK]
        assert event.stopped
        assert any(r.kind == "chain" for r in results)

    @pytest.mark.asyncio
    async def test_admin_only_gate_applies_to_bare_ref(self):
        service = Service()
        plugin = make_plugin(service, admin_only=True)
        event = Event("/zlib 7")
        (result,) = await run(plugin.cmd_bare_ref(event))
        assert result.payload == "下载仅管理员可用。"
        assert service.downloaded == []
        assert event.stopped

    @pytest.mark.parametrize(
        ("text", "matched"),
        [
            ("zlib 3", True),
            ("/zlib 3", True),
            ("zl 3", True),
            ("/zl 12 ", True),
            ("zlib 3 4", False),
            ("zlib 详情 3", False),
            ("zlib 3x", False),
            ("zlib", False),
            ("zlibrary 3", False),
        ],
    )
    def test_regex_only_matches_a_bare_number(self, text, matched):
        assert bool(re.search(plugin_main._BARE_REF_REGEX, text)) is matched


class TestAdminOnly:
    @pytest.mark.asyncio
    async def test_download_command_rejects_non_admin_when_restricted(self):
        service = Service()
        plugin = make_plugin(service, admin_only=True)
        (result,) = await run(plugin.cmd_download(Event("zlib 下载 3")))
        assert result.payload == "下载仅管理员可用。"
        assert service.downloaded == []

    @pytest.mark.asyncio
    async def test_download_command_reports_quota_updates_when_done(self):
        plugin = make_plugin()
        results = await run(plugin.cmd_download(Event("zlib 下载 3")))
        assert results[0].payload.startswith("正在下载《State and Revolution》")
        assert results[-1].payload.startswith("已发送 book.pdf")
        assert "剩余 3 次" in results[-1].payload


class TestHelp:
    @pytest.mark.asyncio
    async def test_help_lists_every_sub_command_with_the_configured_prefix(self):
        plugin = make_plugin()
        (result,) = await run(plugin.cmd_help(Event("zlib 帮助")))
        for sub in ("搜索", "详情", "下载", "下一页", "上一页", "状态", "诊断"):
            assert f"/zlib {sub}" in result.payload
