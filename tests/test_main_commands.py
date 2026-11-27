"""main 指令入口：详情与下载指令的参数剥离、错误出口与交付方式。

只测指令层自身的行为（取参、出错文案、谁来发消息），搜索与下载的内部逻辑
已由 test_service / test_client 覆盖。
"""

from __future__ import annotations

import asyncio
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

    def plain_result(self, text: str):
        return Result("plain", text)

    def chain_result(self, chain):
        return Result("chain", chain)

    def is_admin(self) -> bool:
        return self._admin


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
            pool_left=3,
            elapsed=1.0,
        )


def make_plugin(service=None, *, admin_only: bool = False):
    plugin = object.__new__(plugin_main.ZLibraryAssistantPlugin)
    plugin.service = service or Service()
    plugin.download_admin_only = admin_only
    plugin.search_timeout = 30.0
    return plugin


async def run(agen):
    return [r async for r in agen]


class TestWarmup:
    @pytest.mark.asyncio
    async def test_warmup_probes_the_pool_before_discovery_and_login(self):
        order = []

        class Client:
            async def ensure_probed(self):
                order.append("probe")

            async def discover_domains(self):
                order.append("discover")

            async def ensure_login(self):
                order.append("login")

        plugin = make_plugin()
        plugin.client = Client()
        await plugin._warmup()
        assert order == ["probe", "discover", "login"]

    @pytest.mark.asyncio
    async def test_terminate_cancels_background_downloads_before_close(self):
        order = []

        class Service:
            async def cancel_downloads(self):
                order.append("cancel")

        class Client:
            async def close(self):
                order.append("close")

        plugin = make_plugin()
        plugin.service = Service()
        plugin.client = Client()
        plugin._warmup_task = None
        await plugin.terminate()
        assert order == ["cancel", "close"]

    @pytest.mark.asyncio
    async def test_push_sends_through_the_plugin_context(self):
        sent = []

        class Context:
            async def send_message(self, umo, chain):
                sent.append((umo, chain))
                return True

        plugin = make_plugin()
        plugin.context = Context()
        assert await plugin._push("umo-1", "chain") is True
        assert sent == [("umo-1", "chain")]

    @pytest.mark.asyncio
    async def test_push_failure_is_reported_as_false(self):
        class Context:
            async def send_message(self, umo, chain):
                raise RuntimeError("boom")

        plugin = make_plugin()
        plugin.context = Context()
        assert await plugin._push("umo-1", "chain") is False


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


class TestSearchTimeout:
    @pytest.mark.asyncio
    async def test_search_command_reports_timeout_instead_of_hanging(self, monkeypatch):
        class Hanging(Service):
            async def search(self, *_a, **_k):
                await asyncio.sleep(5)

        plugin = make_plugin(Hanging())
        plugin.search_timeout = 0.01
        (result,) = await run(plugin.cmd_search(Event("zlib 搜索 资本论")))
        assert result.kind == "plain"
        assert result.payload.startswith("搜索超时")

    @pytest.mark.asyncio
    async def test_timeout_message_reports_the_configured_seconds(self):
        class Hanging(Service):
            async def search(self, *_a, **_k):
                await asyncio.sleep(5)

        plugin = make_plugin(Hanging())
        plugin.search_timeout = 0.6
        (result,) = await run(plugin.cmd_search(Event("zlib 搜索 资本论")))
        assert "镜像在 1 秒内" in result.payload
        assert "search_timeout" in result.payload


class TestHelp:
    @pytest.mark.asyncio
    async def test_help_lists_every_sub_command_with_the_configured_prefix(self):
        plugin = make_plugin()
        (result,) = await run(plugin.cmd_help(Event("zlib 帮助")))
        for sub in ("搜索", "详情", "下载", "下一页", "上一页", "状态", "诊断"):
            assert f"/zlib {sub}" in result.payload
