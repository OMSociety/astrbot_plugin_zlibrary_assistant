"""zlib_search_books 工具：关键词搜索 Z-Library 图书，返回 HTML 卡片图片。"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.types import CallToolResult, TextContent
from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass

from astrbot.api import logger
from astrbot.core import html_renderer
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..templates import SEARCH_CARD_TMPL
from ..zlib_client import ZlibClient, ZlibError

MAX_COVER_CARDS = 8  # 渲染卡片的上限，防止图片过大
COVER_DOWNLOAD_CONCURRENCY = 8  # 封面下载并发数（8 本一轮并发跑完）
CARD_RENDER_TIMEOUT = (
    25  # 卡片渲染最长等待（秒）；超时降级纯文本，避免吃掉工具整体超时预算
)


async def _attach_covers(client: ZlibClient, books: list[dict]) -> list[dict]:
    """并发下载封面并内嵌为 base64 data URI（渲染不再依赖外链）。

    背景：AstrBot 云端文转图服务访问不了 Z-Library 封面 CDN，直接引用
    外链会全部加载失败；先在本机（走配置代理）下载封面转 base64 内嵌，
    云端渲染器即可正常显示。下载失败的书 cover 置空，模板自动显示占位。
    失败日志合并为一条（示例原因），避免每张封面一条 WARNING 刷屏。
    """
    sem = asyncio.Semaphore(COVER_DOWNLOAD_CONCURRENCY)
    errors: list[str] = []

    async def fetch(b: dict) -> dict:
        url = b.get("cover", "")
        if not url or url.startswith("data:"):
            return b  # 无封面或已是 data URI 的条目直接跳过
        if not url.startswith(("http://", "https://")):
            # 相对路径（如 /covers/xx.jpg）→ 拼成完整 URL
            url = f"https://{client.domain}/{url.lstrip('/')}"
        async with sem:
            data_uri, err = await client.fetch_cover_base64(url)
        if data_uri:
            b["cover"] = data_uri
        else:
            b["cover"] = ""
            errors.append(err)
        return b

    books = await asyncio.gather(*(fetch(b) for b in books))
    failed = len(errors)
    if failed:
        logger.warning(
            f"封面下载失败 {failed}/{len(books)} 张（示例原因：{errors[0]}），显示占位"
        )
    else:
        ok = sum(1 for b in books if str(b.get("cover", "")).startswith("data:"))
        logger.info(f"封面下载完成：成功 {ok}/{len(books)} 张")
    return books


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
    """渲染书籍卡片图片，返回本地图片路径；失败/超时返回 None（调用方降级）。

    用 asyncio.wait_for 限定渲染时长：AstrBot 对工具整体有 60 秒超时
    （tool_call_timeout），若渲染服务慢/不可达而一直等，工具会被整体掐断
    报 timeout（连降级的机会都没有）。限定 25 秒后超时即降级纯文本。
    """
    try:
        img_path = await asyncio.wait_for(
            html_renderer.render_custom_template(
                SEARCH_CARD_TMPL,
                {"books": books, "query": query},
                return_url=False,
                options={"full_page": True, "type": "jpeg", "quality": 40},
            ),
            timeout=CARD_RENDER_TIMEOUT,
        )
        return img_path
    except Exception as e:  # noqa: BLE001 - 渲染失败/超时需降级，不向上抛
        logger.warning(
            f"搜索结果卡片渲染失败或超时（{CARD_RENDER_TIMEOUT}s），降级为纯文本: {e}"
        )
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
        cards = books[:MAX_COVER_CARDS]
        await _attach_covers(
            self.client, cards
        )  # 封面转 base64 内嵌，修复云端渲染器无法加载外链
        img_path = await _render_book_card(cards, query)
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
