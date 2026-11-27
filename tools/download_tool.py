"""zlib_download_book 工具：按 id 下载书籍到 AstrBot 数据目录。

文件保存在 <AstrBot data>/plugin_data/zlibrary_assistant/books/，向 LLM 返回绝对路径。
AstrBot 的 send_message_to_user 在 Computer Use 本地运行时（computer_use_runtime=local）
下允许发送本地绝对路径文件（需在 WebUI 启用 Computer Use）。
"""

from __future__ import annotations

from typing import Any

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass

from ..zlib_errors import ZlibError
from ..zlib_service import ZlibService
from .common import is_admin_context


@dataclass(config=ConfigDict(arbitrary_types_allowed=True))
class ZlibDownloadBookTool(FunctionTool[AstrAgentContext]):
    name: str = "zlib_download_book"
    description: str = (
        "从 Z-Library 下载一本书籍文件到本地。"
        "重要：仅在用户明确要求下载某本书时才调用，不要擅自下载！"
        "每次下载会消耗账号的每日下载额度（账号池共享轮换）。"
        "book_id 必须来自 zlib_search_books 的搜索结果。"
        "下载完成后请使用 send_message_to_user（type=file, path=绝对路径）把文件发送给用户，并告知剩余下载额度。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "book_id": {
                    "type": "integer",
                    "description": "书籍 id，必须来自 zlib_search_books 返回的书目列表",
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
            return "下载仅管理员可用。"
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

        return (
            "下载完成\n"
            f"- 文件：{done.filename}\n"
            f"- 大小：{done.size / 1024 / 1024:.1f} MB\n"
            f"- 保存路径：{done.path}\n"
            f"- 今日剩余额度 {done.downloads_left} 次\n"
            "请用 send_message_to_user 发送文件给用户："
            f'{{"type": "file", "path": "{done.path}"}}（绝对路径）。'
        )
