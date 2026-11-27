"""业务服务层：用户指令与 LLM 工具共用的“搜索 + 卡片 + 下载 + 落盘”。

指令与工具只负责参数解析和回复格式；这里不依赖 AstrBot 事件对象。
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import aiofiles

from astrbot.api import logger

from .zlib_cards import (
    MAX_CARD_BOOKS,
    RENDER_OPTIONS,
    SEARCH_CARD_TMPL,
    build_card_data,
)
from .zlib_client import ZlibClient
from .zlib_errors import ZlibError
from .zlib_models import Book, SearchResult
from .zlib_store import SearchMemory

# 渲染函数：(模板, 数据, 渲染选项, umo) -> 本地图片路径
Renderer = Callable[[str, dict, dict, str], Awaitable[str]]

# 渲染是一次网络调用（实测云端 t2i 单张约 8-9 秒），超时按纯文本回退。
# 这一预算要连同搜索一起塞进工具调用的整体时限，故不设得太宽。
CARD_RENDER_TIMEOUT = 15.0
COVER_CONCURRENCY = 8

# 下载目录保留期：书籍文件只增不减会把磁盘吃满，超期文件在每次成功下载后清理
DOWNLOAD_RETENTION_DAYS = 7

MAX_SEARCH_LIMIT = MAX_CARD_BOOKS


@dataclass
class SearchOutcome:
    result: SearchResult
    card_path: str | None  # 卡片渲染失败或关闭时为 None


@dataclass
class Downloaded:
    book: Book | None
    path: Path
    filename: str
    size: int
    account_name: str
    pool_left: int  # 下载后账号池各已登录账号的剩余次数之和
    elapsed: float = 0.0


class ZlibService:
    def __init__(
        self,
        client: ZlibClient,
        *,
        data_dir: Path,
        renderer: Renderer | None,
        search_limit: int = 5,
        card_enabled: bool = True,
        command_prefix: str = "/zlib",
    ):
        self.client = client
        self.download_dir = data_dir / "books"
        self.renderer = renderer
        self.search_limit = max(1, min(int(search_limit), MAX_SEARCH_LIMIT))
        self.card_enabled = card_enabled
        self.prefix = command_prefix
        self.memory = SearchMemory()

    # ---------- 搜索 ----------

    async def search(
        self,
        umo: str,
        query: str,
        *,
        limit: int | None = None,
        page: int = 1,
        language: str = "",
        extension: str = "",
    ) -> SearchOutcome:
        limit = max(1, min(int(limit or self.search_limit), MAX_SEARCH_LIMIT))
        result = await self.client.search(
            query, limit=limit, page=page, language=language, extension=extension
        )
        self.memory.remember(umo, result)
        card_path = None
        if result.books and self.card_enabled and self.renderer is not None:
            card_path = await self._render_card(umo, result)
        return SearchOutcome(result, card_path)

    async def turn_page(self, umo: str, delta: int) -> SearchOutcome:
        """基于该会话最近一次搜索翻页（delta=+1 下一页，-1 上一页）。"""
        last = self.memory.recall(umo)
        if last is None:
            raise ZlibError(
                "api_error", f"没有可翻页的搜索记录，请先用 {self.prefix} 搜索 关键词"
            )
        target = last.page + delta
        if target < 1:
            raise ZlibError("api_error", "已经是第一页")
        if target > last.total_pages:
            raise ZlibError("api_error", "已经是最后一页")
        return await self.search(
            umo,
            last.query,
            limit=len(last.books) or None,
            page=target,
            language=last.language,
            extension=last.extension,
        )

    async def _render_card(self, umo: str, result: SearchResult) -> str | None:
        """封面抓取 + 卡片渲染；任何失败都降级为 None（调用方回退纯文本）。"""
        shown = result.books[:MAX_CARD_BOOKS]
        sem = asyncio.Semaphore(COVER_CONCURRENCY)

        async def cover(book: Book) -> tuple[int, str]:
            async with sem:
                return book.id, await self.client.fetch_cover_data_uri(book.cover)

        try:
            covers = dict(await asyncio.gather(*(cover(b) for b in shown)))
            data = build_card_data(result, covers, self.prefix, self.quota_hint())
            return await asyncio.wait_for(
                self.renderer(SEARCH_CARD_TMPL, data, RENDER_OPTIONS, umo),
                timeout=CARD_RENDER_TIMEOUT,
            )
        except Exception as e:  # noqa: BLE001 - 渲染失败/超时降级，不向上抛
            logger.warning(
                f"搜索结果卡片渲染失败，降级为纯文本: {type(e).__name__}: {e}"
            )
            return None

    # ---------- 引用解析 ----------

    def resolve_book(self, umo: str, ref: str) -> Book:
        """把“下载 N”里的 N 解析成书：不超过本会话最近结果数时按序号，否则按书 id。"""
        ref = ref.strip()
        if not (ref.isascii() and ref.isdigit()):
            raise ZlibError("api_error", "请提供书的序号或 id（数字）")
        number = int(ref)
        last = self.memory.recall(umo)
        if last is not None and 1 <= number <= len(last.books):
            return last.books[number - 1]
        book = self.client.book_cache.get(number)
        if book is None:
            hint = (
                f"序号超出范围（本页共 {len(last.books)} 本）"
                if last is not None
                else f"没有找到这本书，请先用 {self.prefix} 搜索 关键词"
            )
            raise ZlibError("api_error", hint)
        return book

    # ---------- 下载 ----------

    def _purge_expired(self) -> None:
        """清理下载目录里超过保留期的旧文件；清理失败只记日志，不影响本次下载。"""
        cutoff = time.time() - DOWNLOAD_RETENTION_DAYS * 86400
        try:
            for entry in os.scandir(self.download_dir):
                try:
                    if entry.is_file() and entry.stat().st_mtime < cutoff:
                        os.remove(entry.path)
                except OSError as e:
                    logger.warning(f"清理过期下载文件失败 {entry.name}: {e}")
        except OSError as e:
            logger.warning(f"清理过期下载文件失败: {e}")

    async def download(
        self,
        book: Book,
        *,
        on_progress: Callable[[int, int], Awaitable[None]] | None = None,
    ) -> Downloaded:
        """下载一本书并写入下载目录；边收边写，内存里只留一个数据块。

        on_progress: `async (已读字节, 声明总字节)`，可用来记录进度或判断卡死。
        """
        started = time.monotonic()
        handle = None
        target: Path | None = None
        written = 0

        async def sink(filename: str, chunk: bytes) -> None:
            nonlocal handle, target, written
            if handle is None:
                target = self._target_path(filename)
                try:
                    handle = await aiofiles.open(target, "wb")
                except OSError as e:
                    logger.error(f"保存下载文件失败: {e}")
                    raise ZlibError(
                        "api_error", "下载成功但保存文件失败", str(e)
                    ) from e
            await handle.write(chunk)
            written += len(chunk)

        try:
            account, filename, _ = await self.client.download(
                book, sink=sink, on_progress=on_progress
            )
        except BaseException:
            # 传输中断时删掉半截文件，避免下载目录里留下无法打开的残件
            if handle is not None:
                await handle.close()
                handle = None
            if target is not None:
                try:
                    os.remove(target)
                except OSError as e:
                    logger.warning(f"清理未完成的下载文件失败: {e}")
            raise
        finally:
            if handle is not None:
                await handle.close()

        self._purge_expired()
        return Downloaded(
            book=book,
            path=target,
            filename=target.name,
            size=written,
            account_name=account.name,
            pool_left=self.pool_left(),
            elapsed=time.monotonic() - started,
        )

    def _target_path(self, filename: str) -> Path:
        """下载落点；文件名来自远端字段，越出下载目录即拒绝。"""
        self.download_dir.mkdir(parents=True, exist_ok=True)
        base = os.path.realpath(self.download_dir)
        out = os.path.join(base, filename)
        real = os.path.realpath(out)
        if real != base and not real.startswith(base + os.sep):
            logger.warning("下载文件名越出下载目录，已拒绝保存")
            raise ZlibError("api_error", "文件名不合法，已拒绝保存")
        return Path(out)

    def pool_left(self) -> int:
        """账号池剩余的下载次数总和；只算已登录的账号。"""
        return sum(a.downloads_left for a in self.client.accounts if a.logged_in)

    def quota_hint(self) -> str:
        """给卡片页脚的额度提示；没有账号时不提额度（下载另行报错）。"""
        if not any(a.logged_in for a in self.client.accounts):
            return ""
        left = self.pool_left()
        return f"今日剩余 {left} 次下载" if left else "今日下载额度已用完"


def format_book_lines(books: list[Book]) -> list[str]:
    """纯文本书目行（卡片不可用时的回退）：序号 标题 - 作者 [格式 大小]。"""
    lines = []
    for i, b in enumerate(books, start=1):
        tail = " ".join(x for x in (b.extension.upper(), b.size, b.year) if x)
        lines.append(
            f"{i}. {b.title or '未知标题'} - {b.author or '未知作者'}"
            + (f" [{tail}]" if tail else "")
        )
    return lines
