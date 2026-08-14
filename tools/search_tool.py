"""zlib_search_books 工具：关键词搜索 Z-Library 图书，返回 HTML 卡片图片。"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger
from astrbot.core import html_renderer
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from mcp.types import CallToolResult, TextContent
from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass

from ..templates import SEARCH_CARD_TMPL
from ..zlib_client import ZlibClient, ZlibError

MAX_COVER_CARDS = 8  # 渲染卡片的上限，防止图片过大


def _book_text_lines(books: list[dict]) -> list[str]:
    """生成给 LLM 看的书籍纯文本列表（含 id，供下载工具使用）。"""
    lines = []
    for i, b in enumerate(books, start=1):
        title = b.get("title", "未知标题")
        author = b.get("author", "未知作者")
        year = b.get("year", "")
        lang = b.get("language", "")
        ext = b.get("extension", "")
        size = b.get("filesizeString", "")
        lines.append(
            f"编号{i}: id={b.get('id')} | {title} | {author} | {year} | {lang} | {ext} | {size}"
        )
    return lines


async def _render_book_card(books: list[dict], query: str) -> str | None:
    """渲染书籍卡片图片，返回本地图片路径；失败返回 None（调用方降级）。"""
    try:
        img_path = await html_renderer.render_custom_template(
            SEARCH_CARD_TMPL,
            {"books": books, "query": query},
            return_url=False,
            options={"full_page": True, "type": "jpeg", "quality": 40},
        )
        return img_path
    except Exception as e:  # noqa: BLE001 - 渲染失败需降级，不向上抛
        logger.warning(f"搜索结果卡片渲染失败，降级为纯文本: {e}")
        return None


@dataclass(config=ConfigDict(arbitrary_types_allowed=True))
class ZlibSearchBooksTool(FunctionTool[AstrAgentContext]):
    name: str = "zlib_search_books"
    description: str = (
        "搜索 Z-Library 图书库并返回结果。"
        "当用户想找书、查书、要电子书、要下载某本书（提到书名/作者/关键词）时调用此工具。"
        "返回结果包含书籍卡片图片（封面/标题/作者/格式/大小）和带 id 的文本列表，"
        "搜索不消耗任何下载额度。若用户之后要求下载，请使用 zlib_download_book 并传入对应 id。"
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
                    "description": "返回条数上限（可选；不传则使用插件配置 search_limit，最大 8）",
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
    client: ZlibClient | None = None
    search_limit: int = 5

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return "请提供搜索关键词（如：帮我搜一下《资本论》）"

        if self.client is None:
            return "插件客户端未初始化，请检查插件配置"

        # limit 优先用参数，未传则使用插件配置的 search_limit
        limit = int(kwargs.get("limit") or self.search_limit or 5)
        limit = max(1, min(limit, 8))
        language = str(kwargs.get("language", "") or "").strip()
        extension = str(kwargs.get("extension", "") or "").strip()

        try:
            books = await self.client.search(
                query,
                limit=limit,
                language=language,
                extension=extension,
            )
        except ZlibError as e:
            return f"搜索失败：{e.message}"

        if not books:
            return f"未在 Z-Library 找到与「{query}」相关的图书，可以换个关键词试试。"

        # 供 LLM 阅读的文本（含 id）
        text_lines = _book_text_lines(books[:MAX_COVER_CARDS])
        text = (
            f"搜索「{query}」命中 {len(books)} 本（以下展示前 {len(text_lines)} 本）：\n"
            + "\n".join(text_lines)
            + "\n提示：搜索不消耗下载额度；用户要求下载时，请用 zlib_download_book 并传入对应 id。"
        )

        # 渲染 HTML 卡片图片（封面/标题/作者/格式），保存到本地文件
        # 注意：不通过 ImageContent 返回（纯文本模型如 deepseek-chat 会因 image_url 报 400），
        # 而是给出图片路径，由 LLM 用 send_message_to_user(type=image) 发送给用户。
        img_path = await _render_book_card(books[:MAX_COVER_CARDS], query)
        if img_path:
            text += (
                f"\n\n已生成搜索结果卡片图片（含封面）：{img_path}\n"
                f"请使用 send_message_to_user 发送该图片给用户："
                f'{{"type": "image", "path": "{img_path}"}}。'
            )

        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            isError=False,
        )
