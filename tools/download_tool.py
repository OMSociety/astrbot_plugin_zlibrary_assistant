"""zlib_download_book 工具：按 id 下载书籍，文件由插件直接发给用户。"""

from __future__ import annotations

from typing import Any

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.message.components import File
from astrbot.core.message.message_event_result import MessageChain
from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass

from ..zlib_errors import ZlibError
from ..zlib_service import ZlibService
from .common import deliver, is_admin_context


@dataclass(config=ConfigDict(arbitrary_types_allowed=True))
class ZlibDownloadBookTool(FunctionTool[AstrAgentContext]):
    name: str = "zlib_download_book"
    description: str = (
        "把 Z-Library 上的一本书下载并直接发给用户。"
        "只在用户明确表示要这本书（要文件、要下载、接受你推荐的版本）时调用，不要替用户擅自下载。"
        "每次调用消耗一次账号的每日下载额度，额度由账号池共用。"
        "book_id 必须是 zlib_search_books 返回的 id；用户只说了书名没确认版本时，先搜索再问。"
        "文件由插件自己发给用户，你无需重发；下载成功后向用户说明发的是什么书即可。"
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
        try:
            done = await self.service.download(book)
        except ZlibError as e:
            return f"下载失败：{e.message}"

        await deliver(
            context,
            MessageChain(chain=[File(name=done.filename, file=str(done.path))]),
        )
        return (
            "已把文件发给用户\n"
            f"- 书名：{book.title or '未知'}\n"
            f"- 格式：{(book.extension or '').upper() or '未知'}\n"
            f"- 大小：{done.size / 1024 / 1024:.1f} MB\n"
            f"- 今日剩余额度：{done.downloads_left} 次\n"
            "无需再让用户确认文件路径，也不要把本地路径写进回复。"
        )
