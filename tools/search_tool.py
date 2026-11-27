"""zlib_search_books 工具：搜索图书，返回卡片图片路径与带 id 的书目文本。"""

from __future__ import annotations

from typing import Any

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from mcp.types import CallToolResult, TextContent
from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass

from ..zlib_errors import ZlibError
from ..zlib_models import book_text_lines, fenced_block
from ..zlib_service import ZlibService
from .common import session_key


@dataclass(config=ConfigDict(arbitrary_types_allowed=True))
class ZlibSearchBooksTool(FunctionTool[AstrAgentContext]):
    name: str = "zlib_search_books"
    description: str = (
        "搜索 Z-Library 图书库并返回结果。"
        "当用户想找书、查书、要电子书、要下载某本书（提到书名/作者/关键词）时调用此工具。"
        "返回结果包含书籍卡片图片（封面/标题/作者/格式/大小）和带 id 的文本列表，"
        "搜索不消耗任何下载额度。若用户之后要求下载，请使用 zlib_download_book 并传入对应 id。"
        "结果不止一页时，可用 page 参数取其他页。"
        "不传 limit 时返回条数由插件配置（search_limit）决定，无需自行猜测默认值。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词（书名、作者或主题，支持英文和中文）",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数上限（可选；不传则使用插件配置 search_limit，最大 10）",
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
            outcome = await self.service.search(
                session_key(context),
                query,
                limit=_as_int(kwargs.get("limit")),
                page=_as_int(kwargs.get("page")) or 1,
                language=str(kwargs.get("language") or ""),
                extension=str(kwargs.get("extension") or ""),
            )
        except ZlibError as e:
            return f"搜索失败：{e.message}"

        result = outcome.result
        if not result.books:
            return f"未在 Z-Library 找到与「{query}」相关的图书，可以换个关键词试试。"

        text = (
            f"搜索「{query}」第 {result.page}/{result.total_pages} 页，"
            f"共 {result.total_items} 条（本页 {len(result.books)} 本）：\n"
            + fenced_block(book_text_lines(result.books))
            + "\n提示：搜索不消耗下载额度；用户要求下载时，请用 zlib_download_book 并传入对应 id。"
        )
        if result.has_next:
            text += f"\n还有下一页，可用 page={result.page + 1} 继续搜索。"
        if outcome.card_path:
            # 不通过 ImageContent 返回：纯文本模型会因 image_url 报 400，
            # 改为给出图片路径，由 LLM 用 send_message_to_user(type=image) 发送
            text += (
                f"\n\n已生成搜索结果卡片图片：{outcome.card_path}\n"
                f"请使用 send_message_to_user 发送该图片给用户："
                f'{{"type": "image", "path": "{outcome.card_path}"}}。'
            )
        return CallToolResult(
            content=[TextContent(type="text", text=text)], isError=False
        )


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
