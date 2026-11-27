"""搜索结果的存取：书籍缓存（持久化）与会话内最近一次搜索。

下载只需要书的 id 与 hash。LLM 工具与用户指令都只给 id（或序号），
hash 来自此前的搜索结果，因此搜索时登记，下载时按 id 取回。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections import OrderedDict
from pathlib import Path

from astrbot.api import logger

from .zlib_models import Book, SearchResult

MAX_CACHED_BOOKS = 500
MAX_MEMORY_SESSIONS = 200
MEMORY_TTL_SECONDS = 2 * 3600


class BookCache:
    """book id -> Book，容量受限（FIFO 淘汰），可持久化到 JSON。"""

    def __init__(self, path: Path | None = None, capacity: int = MAX_CACHED_BOOKS):
        self._path = path
        self._capacity = capacity
        self._books: OrderedDict[int, Book] = OrderedDict()
        self._dirty = False
        self._load()

    def __len__(self) -> int:
        return len(self._books)

    def get(self, book_id: int) -> Book | None:
        return self._books.get(book_id)

    def put_many(self, books: list[Book]) -> None:
        for book in books:
            self._books.pop(book.id, None)
            self._books[book.id] = book
        while len(self._books) > self._capacity:
            self._books.popitem(last=False)
        self._dirty = True

    def clear(self) -> None:
        self._books.clear()
        self._dirty = True

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"书籍缓存读取失败，已忽略: {e}")
            return
        if not isinstance(raw, list):
            return
        for item in raw[-self._capacity :]:
            book = Book.from_cache(item)
            if book is not None:
                self._books[book.id] = book

    def flush(self) -> None:
        """原子写盘：先写临时文件再替换，进程中断不会留下半截 JSON。"""
        if self._path is None or not self._dirty:
            return
        payload = json.dumps(
            [b.to_cache() for b in self._books.values()], ensure_ascii=False
        )
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=self._path.parent, prefix=".book_cache.", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(payload)
                os.replace(tmp, self._path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
            self._dirty = False
        except OSError as e:
            logger.warning(f"书籍缓存写入失败: {e}")


class SearchMemory:
    """按会话（umo）记住最近一次搜索，供“下载 N / 下一页”引用。"""

    def __init__(
        self,
        capacity: int = MAX_MEMORY_SESSIONS,
        ttl: float = MEMORY_TTL_SECONDS,
        clock=time.monotonic,
    ):
        self._capacity = capacity
        self._ttl = ttl
        self._clock = clock
        self._items: OrderedDict[str, tuple[float, SearchResult]] = OrderedDict()

    def remember(self, umo: str, result: SearchResult) -> None:
        self._items.pop(umo, None)
        self._items[umo] = (self._clock(), result)
        while len(self._items) > self._capacity:
            self._items.popitem(last=False)

    def recall(self, umo: str) -> SearchResult | None:
        entry = self._items.get(umo)
        if entry is None:
            return None
        stamp, result = entry
        if self._clock() - stamp > self._ttl:
            del self._items[umo]
            return None
        return result
