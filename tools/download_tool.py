"""zlib_download_book 工具：在后台下载书籍，文件完成后由插件直接发给用户。

下载可能要几十秒到几分钟。工具只负责提交并立即返回，不等传输结束：模型的
回合随即结束，不会因下载而占着会话；成功时插件直接把文件发给用户，不再唤醒
模型；失败时直接给用户发一句失败原因。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from astrbot.api import logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.message.components import File
from astrbot.core.message.message_event_result import MessageChain
from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass

from ..zlib_service import Downloaded, ZlibService
from .common import deliver, is_admin_context, session_key

# 主动推送：(会话 umo, 消息链) -> 是否发出；由插件主类注入，不可用时退回经消息事件发送
Push = Callable[[str, MessageChain], Awaitable[bool]]


@dataclass(config=ConfigDict(arbitrary_types_allowed=True))
class ZlibDownloadBookTool(FunctionTool[AstrAgentContext]):
    name: str = "zlib_download_book"
    description: str = (
        "把 Z-Library 上的一本书下载并直接发给用户。"
        "只在用户明确表示要这本书（要文件、要下载、接受你推荐的版本）时调用，不要替用户擅自下载。"
        "book_id 必须是 zlib_search_books 返回的 id；用户只说了书名没确认版本时，先搜索再问。"
        "调用后下载在后台进行并立即返回：文件完成后由插件自己发给用户，失败时插件也会直接告知用户，"
        "你无需等待、重发或再次确认；只需用一句话告诉用户已经开始下载。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "book_id": {
                    "type": "integer",
                    "description": "书籍 id，必须来自 zlib_search_books 返回的书目清单",
                },
            },
            "required": ["book_id"],
        }
    )
    service: ZlibService | None = None
    admin_only: bool = False
    push: Push | None = None

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:
        if self.service is None:
            return "插件未初始化，请检查插件配置"
        if self.admin_only and not is_admin_context(context):
            return "下载仅管理员可用，当前用户不是管理员，请说明后停止。"
        try:
            book_id = int(kwargs.get("book_id"))
        except (TypeError, ValueError):
            return "参数 book_id 必须是数字（来自 zlib_search_books 的搜索结果 id）"

        book = self.service.client.book_cache.get(book_id)
        if book is None:
            return f"没有 id={book_id} 的书籍信息，请先调用 zlib_search_books 搜索"

        umo = session_key(context)

        async def send(chain: MessageChain) -> None:
            if self.push is not None:
                try:
                    if await self.push(umo, chain):
                        return
                except Exception as e:  # noqa: BLE001 - 推送失败时退回事件发送
                    logger.debug(f"主动推送失败，改经消息事件发送: {e}")
            await deliver(context, chain)

        async def on_done(done: Downloaded) -> None:
            await send(
                MessageChain(chain=[File(name=done.filename, file=str(done.path))])
            )

        async def on_error(text: str) -> None:
            await send(MessageChain().message(text))

        label = book.title or "未知"
        if self.service.quota_exhausted():
            return (
                "账号池今日下载额度已用完，没有开始下载。"
                "请告诉用户明天再试，不要重试本工具。"
            )
        if not self.service.start_download(book, on_done=on_done, on_error=on_error):
            return f"《{label}》已经在下载中，完成后文件会直接发给用户，无需重复提交。"
        return (
            f"已开始在后台下载《{label}》，不占用对话。\n"
            "文件下载完成后由插件直接发给用户，失败时插件也会直接告知用户。\n"
            "用一句话告诉用户已经开始下载即可；不要等待结果，不要再次调用本工具，"
            "也不要在回复里写本地路径。"
        )
