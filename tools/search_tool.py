"""zlib_search_books 工具：搜索图书，卡片由插件自己发出，书目文本连简介一起交给模型。"""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.message.message_event_result import MessageChain
from mcp.types import CallToolResult, TextContent
from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass

from ..zlib_errors import ZlibError
from ..zlib_models import book_text_lines, fenced_block
from ..zlib_service import ZlibService
from .common import TOOL_DEADLINE, deliver, session_key

# 超时后交给模型的固定短语：此时没拿到任何书目，别让模型编造结果
TIMEOUT_TEXT = (
    f"搜索超时：镜像在 {TOOL_DEADLINE:.0f} 秒内没有返回结果，"
    "没有拿到任何书目。稍后再试或换个关键词，不要凭猜测回答用户。"
)


@dataclass(config=ConfigDict(arbitrary_types_allowed=True))
class ZlibSearchBooksTool(FunctionTool[AstrAgentContext]):
    name: str = "zlib_search_books"
    description: str = (
        "在 Z-Library 检索电子书，返回书目清单（含 id、语言、格式、大小与简介），"
        "同时把带封面的结果卡片直接发给用户。"
        "用户提到书名、作者或主题想找书、要电子书时调用。"
        "用户只是闲聊、问概念或可以用自身知识回答时不要调用。"
        "一次没搜到时换个关键词可以再搜；"
        "用户要的是特定版本（语言、格式不同）时，先看简介再决定，不必反复搜同一关键词。"
        "结果分页时用 page 取其他页；条数上限由插件配置决定，无需猜测默认值。"
        "用户确认要某本书后，用返回里的 id 调用 zlib_download_book。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词（书名、作者或主题，中英文均可）",
                },
                "limit": {
                    "type": "integer",
                    "description": "本页条数上限（可选；不传则用插件配置的 search_limit，最大 10）",
                },
                "page": {
                    "type": "integer",
                    "description": "页码，从 1 开始（可选，默认 1）",
                },
                "language": {
                    "type": "string",
                    "description": "语言过滤，如 zh / en / fr（可空）",
                },
                "extension": {
                    "type": "string",
                    "description": "格式过滤，如 pdf / epub / mobi（可空）",
                },
            },
            "required": ["query"],
        }
    )
    service: ZlibService | None = None

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return "请提供搜索关键词（如：帮我搜一下《资本论》）"
        if self.service is None:
            return "插件未初始化，请检查插件配置"

        try:
            outcome = await asyncio.wait_for(
                self.service.search(
                    session_key(context),
                    query,
                    limit=_as_int(kwargs.get("limit")),
                    page=_as_int(kwargs.get("page")) or 1,
                    language=str(kwargs.get("language") or ""),
                    extension=str(kwargs.get("extension") or ""),
                ),
                TOOL_DEADLINE,
            )
        except asyncio.TimeoutError:
            return TIMEOUT_TEXT
        except ZlibError as e:
            return f"搜索失败：{e.message}"

        result = outcome.result
        if not result.books:
            return f"未在 Z-Library 找到与「{query}」相关的图书，可以换个关键词试试。"

        if outcome.card_path:
            await deliver(context, MessageChain().file_image(outcome.card_path))

        text = (
            f"搜索「{query}」第 {result.page}/{result.total_pages} 页，"
            f"共 {result.total_items} 条（本页 {len(result.books)} 本）：\n"
            + fenced_block(book_text_lines(result.books))
        )
        if result.has_next:
            text += f"\n本页之后还有内容，可用 page={result.page + 1} 继续搜索。"
        return CallToolResult(
            content=[TextContent(type="text", text=text)], isError=False
        )


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
