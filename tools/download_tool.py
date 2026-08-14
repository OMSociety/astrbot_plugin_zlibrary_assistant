"""zlib_download_book 工具：按 id 下载书籍到 AstrBot 数据目录。"""

from __future__ import annotations

import os
from typing import Any

from mcp.types import CallToolResult, TextContent
from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass

from astrbot.api import logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from ..zlib_client import ZlibClient, ZlibError


@dataclass(config=ConfigDict(arbitrary_types_allowed=True))
class ZlibDownloadBookTool(FunctionTool[AstrAgentContext]):
    name: str = "zlib_download_book"
    description: str = (
        "从 Z-Library 下载一本书籍文件到本地。"
        "重要：仅在用户明确要求下载某本书时才调用，不要擅自下载！"
        "每次下载会消耗账号的每日下载额度（每个账号每日约 10 次，账号池共享轮换）。"
        "book_id 必须来自 zlib_search_books 的搜索结果。"
        "下载完成后请使用 send_message_to_user（type=file）把文件发送给用户，并告知剩余下载额度。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "book_id": {
                    "type": "integer",
                    "description": "书籍 id，必须来自 zlib_search_books 返回的编号列表",
                },
            },
            "required": ["book_id"],
        }
    )
    client: ZlibClient | None = None

    def _download_dir(self) -> str:
        base = get_astrbot_data_path()
        d = os.path.join(base, "zlibrary_assistant", "books")
        os.makedirs(d, exist_ok=True)
        return d

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:
        if self.client is None:
            return "插件客户端未初始化，请检查插件配置"

        try:
            book_id = int(kwargs.get("book_id"))
        except (TypeError, ValueError):
            return "参数 book_id 必须是数字（来自 zlib_search_books 的搜索结果 id）"

        try:
            account, filename, content = await self.client.download_by_id(book_id)
        except ZlibError as e:
            return f"下载失败：{e.message}"

        # 保存文件到 AstrBot 数据目录
        try:
            out_path = os.path.join(self._download_dir(), filename)
            with open(out_path, "wb") as f:
                f.write(content)
        except OSError as e:
            logger.error(f"保存下载文件失败: {e}")
            return f"下载成功但保存文件失败：{e}"

        left = account.downloads_left
        return (
            f"下载完成 ✅\n"
            f"- 书名：{filename}\n"
            f"- 大小：{len(content) / 1024 / 1024:.1f} MB\n"
            f"- 保存路径：{out_path}\n"
            f"- 使用账号：{account.name}（今日剩余额度 {left} 次）\n"
            f"请用 send_message_to_user（type=file, path={out_path}）把文件发给用户。"
        )
