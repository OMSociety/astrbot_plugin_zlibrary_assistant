"""各模块测试共用的桩件：aiohttp 响应/会话、E-API 脚本化应答、日志收集、数据构造器。

所有桩都不发起真实网络请求。
"""

from __future__ import annotations

from conftest import FakeHeaders

from astrbot_plugin_zlibrary_assistant.zlib_errors import ZlibError
from astrbot_plugin_zlibrary_assistant.zlib_models import Account, Book, SearchResult
from astrbot_plugin_zlibrary_assistant.zlib_store import BookCache

PUBLIC_PEER = ("93.184.216.34", 443)


# ---------- aiohttp 响应 / 会话 ----------


class FakeStream:
    """模拟 aiohttp StreamReader：iter_chunked 按块吐出，read 每次只给一块。"""

    def __init__(self, body: bytes, chunk_size: int | None = None):
        self._body = body
        self._chunk = chunk_size or max(1, len(body))
        self.read_calls = 0

    async def iter_chunked(self, n: int):
        step = min(n, self._chunk)
        for i in range(0, len(self._body), step):
            yield self._body[i : i + step]

    async def read(self, n: int = -1) -> bytes:
        """一次只返回第一块：不保证读满 n（与真实 StreamReader 一致）。"""
        self.read_calls += 1
        return self._body[: self._chunk]


class _FakeTransport:
    def __init__(self, peer):
        self._peer = peer

    def get_extra_info(self, name):
        return self._peer if name == "peername" else None


class _FakeConnection:
    def __init__(self, peer):
        self.transport = _FakeTransport(peer)


class FakeResponse:
    """既可作 session.get 的返回值，也可作 session.request 的异步上下文。"""

    def __init__(
        self,
        status: int = 200,
        headers=None,
        body: bytes | str = b"",
        *,
        peer=PUBLIC_PEER,
        chunk_size: int | None = None,
    ):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.status = status
        self.headers = (
            headers if isinstance(headers, FakeHeaders) else FakeHeaders(headers)
        )
        self.content = FakeStream(body, chunk_size)
        self.connection = _FakeConnection(peer) if peer is not None else None
        self.released = False

    async def release(self):
        self.released = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.released = True
        return False


class FakeSession:
    """按 URL 路由预置的响应；值可以是响应、响应列表（依次取）或异常（调用时抛出）。"""

    closed = False

    def __init__(self, routes: dict | None = None):
        self._routes = routes or {}
        self.calls: list[dict] = []

    def _next(self, url: str):
        route = self._routes[url]
        if isinstance(route, list):
            route = route.pop(0) if len(route) > 1 else route[0]
        if isinstance(route, BaseException):
            raise route
        return route

    def request(self, method, url, data=None, **kwargs):
        self.calls.append({"method": method, "url": url, "data": data, **kwargs})
        return self._next(url)

    async def get(self, url, **kwargs):
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self._next(url)

    @property
    def urls(self) -> list[str]:
        return [c["url"] for c in self.calls]


# ---------- E-API 脚本化应答（替换 ZlibTransport.request_json） ----------


class FakeApi:
    """以 (方法, 路径) 为键的应答脚本；同一键多次登记则依次取用，最后一条重复使用。

    应答可以是 (状态码, JSON) 元组、ZlibError 实例（调用时抛出）或可调用对象。
    """

    def __init__(self):
        self.calls: list[dict] = []
        self._routes: dict[tuple[str, str], list] = {}

    def on(self, method: str, path: str, *responses) -> FakeApi:
        self._routes.setdefault((method, path), []).extend(responses)
        return self

    async def request_json(
        self, base_url, method, path, *, cookies=None, data=None, params=None
    ):
        call = {
            "base_url": base_url,
            "method": method,
            "path": path,
            "cookies": dict(cookies) if cookies else {},
            "data": dict(data) if data else None,
        }
        self.calls.append(call)
        queue = self._routes.get((method, path))
        if not queue:
            raise AssertionError(f"未预置的请求: {method} {path}")
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if callable(item):
            item = item(call)
        if isinstance(item, BaseException):
            raise item
        return item

    def paths(self) -> list[tuple[str, str]]:
        return [(c["method"], c["path"]) for c in self.calls]


def ok(**payload) -> tuple[int, dict]:
    return 200, {"success": 1, **payload}


def profile(user_id="1", key="k", *, today=0, limit=10, email="u@example.com"):
    return ok(
        user={
            "id": user_id,
            "remix_userkey": key,
            "email": email,
            "downloads_today": today,
            "downloads_limit": limit,
        }
    )


def biz_error(message: str, status: int = 200) -> tuple[int, dict]:
    return status, {"success": 0, "error": message}


# ---------- 日志收集 ----------


class RecordingLogger:
    """收集各级日志文本，用于断言日志里没有凭据。"""

    def __init__(self):
        self.lines: list[str] = []

    def _log(self, msg, *args, **kwargs):
        self.lines.append(str(msg))

    debug = info = warning = error = exception = _log

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


# ---------- 数据构造 ----------


def make_book(book_id: int = 1, **over) -> Book:
    fields = {
        "id": book_id,
        "hash": f"h{book_id}",
        "title": f"Title {book_id}",
        "author": "Author",
        "year": "2020",
        "language": "english",
        "extension": "pdf",
        "size": "1 MB",
        "cover": "",
        "publisher": "",
    }
    fields.update(over)
    return Book(**fields)


def make_account(name: str = "a", **over) -> Account:
    return Account(name=name, **over)


def api_book(book_id: int = 1, **over) -> dict:
    raw = {
        "id": book_id,
        "hash": f"h{book_id}",
        "title": f"Title {book_id}",
        "author": "Author",
        "year": 2020,
        "language": "english",
        "extension": "PDF",
        "filesizeString": "1 MB",
        "cover": "",
        "publisher": "Pub",
    }
    raw.update(over)
    return raw


def download_link_response(
    link="https://8.8.8.8/f", extension="pdf", description="My Book (z-lib)"
):
    return ok(
        file={"downloadLink": link, "extension": extension, "description": description}
    )


# ---------- 服务层客户端桩 ----------


class FakeClient:
    """按调用记录返回预设搜索结果的客户端桩。"""

    def __init__(self, books=None, *, total_pages=1, page=1):
        self.book_cache = BookCache()
        self.accounts = []
        self.books = books if books is not None else [make_book(i) for i in (1, 2, 3)]
        self.total_pages = total_pages
        self.page = page
        self.search_calls = []
        self.cover_calls = []
        self.download_result = None
        self.download_error = None
        self.probe_calls = 0

    async def ensure_probed(self):
        self.probe_calls += 1

    async def search(self, query, *, limit, page, language, extension):
        self.search_calls.append(
            {
                "query": query,
                "limit": limit,
                "page": page,
                "language": language,
                "extension": extension,
            }
        )
        self.book_cache.put_many(self.books)
        return SearchResult(
            query=query,
            books=list(self.books),
            page=page,
            total_pages=self.total_pages,
            total_items=len(self.books),
            language=language,
            extension=extension,
        )

    async def fetch_cover_data_uri(self, url):
        self.cover_calls.append(url)
        return "data:image/jpeg;base64,AAAA" if url else ""

    async def download(self, book, *, sink=None, on_progress=None):
        """按预设结果「下载」：有 sink 时模拟流式落盘，返回值内容恒为空。"""
        if self.download_error:
            raise self.download_error
        account, filename, content = self.download_result
        if sink is not None:
            for i in range(0, len(content), 4):
                await sink(filename, content[i : i + 4])
            if on_progress is not None:
                await on_progress(len(content), len(content))
            content = b""
        return account, filename, content


__all__ = [
    "FakeClient",
    "FakeApi",
    "FakeResponse",
    "FakeSession",
    "FakeStream",
    "PUBLIC_PEER",
    "RecordingLogger",
    "ZlibError",
    "api_book",
    "biz_error",
    "download_link_response",
    "make_account",
    "make_book",
    "ok",
    "profile",
]
