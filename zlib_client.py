"""Z-Library 客户端：账号池、搜索、下载、诊断。

请求经 ZlibTransport 发往 DomainPool 选出的镜像域名；基础设施类失败由域名池
换域名重试，业务类失败（登录失效、额度用尽等）在这里按账号处理。
搜索不需要登录（匿名可用）；下载需要账号。
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

from astrbot.api import logger

from .zlib_domains import DomainPool, ProbeReport
from .zlib_errors import ZlibError
from .zlib_models import (
    Account,
    Book,
    SearchResult,
    normalize_extension,
    normalize_language,
)
from .zlib_store import BookCache
from .zlib_text import guess_image_mime, resize_cover, sanitize_filename
from .zlib_transport import ZlibTransport, read_limited

EP_PROFILE = "/eapi/user/profile"
EP_LOGIN = "/eapi/user/login"
EP_SEARCH = "/eapi/book/search"
EP_FILE = "/eapi/book/{book_id}/{hash_id}/file"
EP_DOMAINS = "/eapi/info/domains"

# 同一账号两次登录尝试之间的最短间隔：凭据错误时避免每次请求都去撞登录端点
LOGIN_RETRY_COOLDOWN = 60.0

# 共享探测：域名池里没有 PROBE_TTL 秒内成功过的域名时，由第一个调用方触发一次
# 全池并发探测，结果写回域名池，所有会话共用；两次探测至少间隔 PROBE_MIN_INTERVAL
# 秒，镜像全部不可达时不会被每次搜索反复触发
PROBE_TTL = 300.0
PROBE_MIN_INTERVAL = 60.0
PROBE_CONCURRENCY = 12
PROBE_TIMEOUT = 10.0

# 下载：传输按「空闲」判停——连续 DOWNLOAD_IDLE_TIMEOUT 秒没收到数据才放弃，
# 只要数据在持续到达，大文件慢速下载也不会被总时限掐断；DOWNLOAD_TOTAL_CAP 只是
# 兜底上限。取直链与额度刷新各自带时限，镜像池逐个试域名时不会无限拖长一次下载。
DOWNLOAD_IDLE_TIMEOUT = 30.0
DOWNLOAD_TOTAL_CAP = 1800.0
LINK_TIMEOUT = 30.0
QUOTA_REFRESH_TIMEOUT = 10.0
_AUTH_KEYWORDS = ("login", "password", "authoriz", "unauthor", "credential")
_QUOTA_KEYWORDS = ("limit", "daily", "quota", "exceed")

MAX_PAGE_LIMIT = 50

# 下载流式写盘的回调签名：
# ChunkSink 收到 (文件名, 数据块)，负责把它写进文件；
# ProgressHook 收到 (已读字节, 声明总字节)，声明总字节未知时为 0。
ChunkSink = Callable[[str, bytes], Awaitable[None]]
ProgressHook = Callable[[int, int], Awaitable[None]]


@dataclass
class Diagnosis:
    """诊断结果：镜像域名探测 + 账号状态。"""

    domains: list[ProbeReport]
    accounts: list[dict]
    proxy: str


def _classify_business_error(status: int, resp: dict, what: str) -> ZlibError:
    error = str(resp.get("error") or resp.get("message") or "").strip()
    lowered = error.lower()
    detail = json.dumps(resp, ensure_ascii=False)[:200]
    if status == 401 or any(k in lowered for k in _AUTH_KEYWORDS):
        return ZlibError("auth_failed", f"{what}失败：账号未登录或凭据无效", detail)
    if any(k in lowered for k in _QUOTA_KEYWORDS):
        return ZlibError(
            "quota_exhausted", f"{what}失败：账号今日下载额度已用完", detail
        )
    return ZlibError(
        "api_error", f"{what}失败" + (f"：{error[:80]}" if error else ""), detail
    )


def _is_success(resp: dict) -> bool:
    return resp.get("success") in (1, True, "1")


def mask_proxy(proxy: str) -> str:
    """代理地址脱敏：去掉用户名与密码，只留协议与主机端口。"""
    if not proxy:
        return ""
    parsed = urlparse(proxy)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}"


class ZlibClient:
    def __init__(
        self,
        accounts: list[Account],
        *,
        domains: list[str] | tuple[str, ...] = (),
        proxy: str = "",
        max_download_mb: int = 80,
        timeout: float = 30.0,
        cache_path: Path | None = None,
    ):
        self.accounts = accounts
        self.proxy = proxy
        self.max_download_bytes = min(512, max(1, int(max_download_mb))) * 1024 * 1024
        self.timeout = timeout
        self.transport = ZlibTransport(proxy, timeout)
        self.domains = DomainPool(domains, allow_onion=self.transport.socks)
        self.book_cache = BookCache(cache_path)
        self._login_lock = asyncio.Lock()
        self._last_login_attempt: dict[str, float] = {}
        self._probe_task: asyncio.Task | None = None
        # 下载进行中占用的额度：选号与占位在同一个同步段内完成，并发下载不会同时选中
        # 只剩一次额度的账号
        self._reserved: dict[str, int] = {}
        self._last_probe_at = float("-inf")

    async def close(self) -> None:
        if self._probe_task is not None and not self._probe_task.done():
            self._probe_task.cancel()
        self.book_cache.flush()
        await self.transport.close()

    # ---------- 底层请求 ----------

    @staticmethod
    def _cookies(account: Account | None) -> dict:
        if account is not None and account.has_remix:
            return {
                "remix_userid": account.remix_userid,
                "remix_userkey": account.remix_userkey,
            }
        return {}

    async def _api(
        self,
        method: str,
        path: str,
        *,
        account: Account | None = None,
        data: dict | None = None,
        trusted_only: bool = False,
        what: str = "请求",
    ) -> dict:
        """经域名池发起 E-API 请求；业务失败（success 非真）抛分类后的 ZlibError。

        带账号或携带凭据的请求只发往内置与用户配置的镜像（trusted_only），
        镜像告知的域名只承接匿名请求。
        """
        cookies = self._cookies(account)

        async def op(base_url: str) -> tuple[int, dict]:
            return await self.transport.request_json(
                base_url, method, path, cookies=cookies, data=data
            )

        status, resp = await self.domains.call(
            op, trusted_only=trusted_only or account is not None
        )
        if not _is_success(resp):
            raise _classify_business_error(status, resp, what)
        return resp

    # ---------- 账号与登录 ----------

    @staticmethod
    def _apply_profile(account: Account, resp: dict) -> None:
        user = resp.get("user") or {}
        if user.get("id") is not None:
            account.remix_userid = str(user["id"])
        if user.get("remix_userkey"):
            account.remix_userkey = str(user["remix_userkey"])
        account.email = str(user.get("email") or account.email)
        account.downloads_today = int(user.get("downloads_today") or 0)
        account.downloads_limit = int(user.get("downloads_limit") or 10)
        account.is_premium = bool(user.get("isPremium"))
        account.logged_in = True
        account.last_error = ""

    async def login_account(self, account: Account) -> Account:
        """登录一个账号：remix 凭证走 GET profile 校验，其次邮箱密码登录。"""
        if account.has_remix:
            resp = await self._api(
                "GET", EP_PROFILE, account=account, what=f"账号 {account.name} 登录"
            )
        elif account.has_password_login:
            resp = await self._api(
                "POST",
                EP_LOGIN,
                data={"email": account.email, "password": account.password},
                trusted_only=True,
                what=f"账号 {account.name} 登录",
            )
        else:
            raise ZlibError(
                "auth_failed",
                f"账号 {account.name} 缺少凭据：需填 remix_userid+remix_userkey 或 email+password",
            )
        self._apply_profile(account, resp)
        logger.info(
            f"账号 {account.name} 登录成功，今日已用 "
            f"{account.downloads_today}/{account.downloads_limit} 次下载"
        )
        return account

    async def ensure_login(self, *, force: bool = False) -> None:
        """确保至少尝试过登录所有未登录账号；单个失败不影响其余账号。

        带锁防并发重入，带冷却防止凭据错误时反复撞登录端点。
        """
        async with self._login_lock:
            for account in self.accounts:
                if account.logged_in and not force:
                    continue
                last = self._last_login_attempt.get(account.name)
                if (
                    not force
                    and last is not None
                    and time.monotonic() - last < LOGIN_RETRY_COOLDOWN
                ):
                    continue
                self._last_login_attempt[account.name] = time.monotonic()
                try:
                    await self.login_account(account)
                except ZlibError as e:
                    account.logged_in = False
                    account.last_error = e.message
                    logger.warning(f"账号 {account.name} 登录失败: {e}")

    def _usable_accounts(self) -> list[Account]:
        """已登录且有额度的账号，剩余额度多者优先。"""
        usable = [a for a in self.accounts if a.logged_in and self._left(a) > 0]
        return sorted(usable, key=self._left, reverse=True)

    def _left(self, account: Account) -> int:
        """剩余额度减去正在进行的下载已占用的次数。"""
        return account.downloads_left - self._reserved.get(account.name, 0)

    # ---------- 域名发现 ----------

    async def discover_domains(self) -> int:
        """向接口索取镜像域名并登记到域名池；返回新增数量。失败不抛。"""
        try:
            resp = await self._api("GET", EP_DOMAINS, what="获取镜像域名")
        except ZlibError as e:
            logger.info(f"镜像域名发现失败: {e}")
            return 0
        names = [
            item.get("domain")
            for item in resp.get("domains") or []
            if isinstance(item, dict)
        ]
        return self.domains.learn(names)

    # ---------- 搜索 ----------

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        page: int = 1,
        language: str = "",
        extension: str = "",
    ) -> SearchResult:
        """按关键词搜索（匿名即可）；结果登记进书籍缓存以便按 id 下载。"""
        query = query.strip()
        if not query:
            raise ZlibError("api_error", "搜索关键词不能为空")
        language = normalize_language(language)
        extension = normalize_extension(extension)
        data: dict = {
            "message": query,
            "limit": max(1, min(int(limit), MAX_PAGE_LIMIT)),
            "page": max(1, int(page)),
        }
        if language:
            data["languages[]"] = language
        if extension:
            data["extensions[]"] = extension

        try:
            resp = await self._api("POST", EP_SEARCH, data=data, what="搜索")
        except ZlibError as e:
            if e.category != "auth_failed":
                raise
            # 个别镜像要求登录后才能搜索：换已登录账号重试一次
            await self.ensure_login()
            account = next((a for a in self.accounts if a.logged_in), None)
            if account is None:
                raise ZlibError(
                    "auth_failed", "该镜像要求登录才能搜索，请先在插件配置中添加账号"
                ) from e
            resp = await self._api(
                "POST", EP_SEARCH, account=account, data=data, what="搜索"
            )

        books = [
            b for b in (Book.from_api(raw) for raw in resp.get("books") or []) if b
        ]
        self.book_cache.put_many(books)
        self.book_cache.flush()
        pagination = resp.get("pagination") or {}
        return SearchResult(
            query=query,
            books=books,
            page=int(pagination.get("current") or data["page"]),
            total_pages=max(1, int(pagination.get("total_pages") or 1)),
            total_items=int(pagination.get("total_items") or len(books)),
            language=language,
            extension=extension,
        )

    # ---------- 封面 ----------

    async def fetch_cover_data_uri(self, url: str, max_bytes: int = 1024 * 1024) -> str:
        """下载封面并转 base64 data URI，失败返回空串（卡片自动用占位）。

        云端渲染器无法直接访问封面 CDN 外链，需由插件端抓取并内嵌。
        """
        if not url:
            return ""
        headers = {"Referer": self.domains.primary + "/"}
        resp, err = await self.transport.guarded_get(
            url, headers=headers, timeout_total=8
        )
        if resp is None:
            logger.debug(f"封面下载失败: {err}")
            return ""
        try:
            async with resp:
                if resp.status != 200:
                    return ""
                content = await read_limited(resp, max_bytes)
        except Exception as e:  # noqa: BLE001 - 封面是锦上添花，任何失败都降级
            logger.debug(f"封面读取失败: {type(e).__name__}: {e}")
            return ""
        if not content or len(content) > max_bytes:
            return ""
        # 解码与缩放是 CPU 密集操作，放线程池避免卡事件循环
        resized = await asyncio.to_thread(resize_cover, content)
        if not resized:
            return ""
        mime = guess_image_mime(resized)
        return f"data:{mime};base64,{base64.b64encode(resized).decode('ascii')}"

    # ---------- 下载 ----------

    async def get_download_link(self, account: Account, book: Book) -> tuple[str, str]:
        """获取下载直链，返回 (直链, 安全文件名)。"""
        try:
            resp = await asyncio.wait_for(
                self._api(
                    "GET",
                    EP_FILE.format(book_id=book.id, hash_id=book.hash),
                    account=account,
                    what="获取下载链接",
                ),
                LINK_TIMEOUT,
            )
        except asyncio.TimeoutError as e:
            raise ZlibError(
                "network_error", f"获取下载链接超过 {LINK_TIMEOUT:.0f} 秒未响应"
            ) from e
        file_info = resp.get("file") or {}
        link = file_info.get("downloadLink")
        if not link:
            raise ZlibError(
                "api_error",
                "获取下载链接失败（可能该书无可用文件）",
                json.dumps(resp, ensure_ascii=False)[:200],
            )
        # extension 是远端字段，可能带路径分隔符（"pdf/../../evil"）：
        # 独立做字符白名单（只留字母数字，限长 8）
        ext = re.sub(r"[^A-Za-z0-9]", "", str(file_info.get("extension") or ""))[:8]
        ext = ext or re.sub(r"[^A-Za-z0-9]", "", book.extension)[:8] or "bin"
        # 描述形如 "书名-作者 (z-library.sk...)"，清理成文件名
        description = str(file_info.get("description") or "")
        name = description.split(" (")[0].strip() or f"book_{book.id}"
        name = sanitize_filename(name)
        if not name.endswith("." + ext):
            name = f"{name}.{ext}"
        # 拼上扩展名后再清洗一遍：文件名是写盘入口，任何一段都不能带分隔符或 ..
        return str(link), sanitize_filename(name)

    async def _fetch_file(
        self,
        link: str,
        *,
        filename: str = "",
        sink: ChunkSink | None = None,
        on_progress: ProgressHook | None = None,
    ) -> bytes:
        """下载文件字节。

        sink 非空时边读边交给它（大文件不整块驻留内存），返回值为空 bytes；
        为空时把全部内容收进返回值（小文件与测试用）。
        """
        resp, err = await self.transport.guarded_get(
            link,
            timeout_total=DOWNLOAD_TOTAL_CAP,
            sock_read=DOWNLOAD_IDLE_TIMEOUT,
        )
        if resp is None:
            raise ZlibError(
                "network_error", "下载失败（网络异常或链接未通过安全校验）", err
            )
        try:
            async with resp:
                if resp.status != 200:
                    raise ZlibError(
                        "api_error", f"下载失败（HTTP {resp.status}）", link[:200]
                    )

                received = 0

                async def write(chunk: bytes) -> None:
                    nonlocal received
                    received += len(chunk)
                    if sink is not None:
                        await sink(filename, chunk)

                content = await read_limited(
                    resp,
                    self.max_download_bytes,
                    sink=write if sink is not None else None,
                    on_progress=on_progress,
                )
                if sink is not None:
                    size = received
                else:
                    size = len(content)
        except (aiohttp.ServerTimeoutError, asyncio.TimeoutError) as e:
            raise ZlibError(
                "network_error",
                f"下载中断：超过 {DOWNLOAD_IDLE_TIMEOUT:.0f} 秒没有收到数据",
                str(e),
            ) from e
        except aiohttp.ClientConnectorError as e:
            raise ZlibError("network_error", "下载网络异常", str(e)) from e
        except aiohttp.ClientError as e:
            raise ZlibError("network_error", "下载失败", str(e)) from e
        if size == 0:
            raise ZlibError("api_error", "下载到的文件为空", link[:200])
        if size > self.max_download_bytes:
            raise ZlibError(
                "api_error",
                f"文件超过插件允许的 {self.max_download_bytes // 1024 // 1024} MiB 上限",
                link[:200],
            )
        return content

    async def _transfer(
        self,
        account: Account,
        book: Book,
        *,
        sink: ChunkSink | None,
        on_progress: ProgressHook | None,
    ) -> tuple[str, int, bytes]:
        """用指定账号完成一次传输：取直链 → 下载 → 记账。"""
        link, filename = await self.get_download_link(account, book)
        content = await self._fetch_file(
            link, filename=filename, sink=sink, on_progress=on_progress
        )
        account.downloads_today += 1
        await self._refresh_quota(account)
        size = len(content) if sink is None else 0
        logger.info(
            f"下载成功: {filename}"
            + (f" ({size} bytes)" if size else "")
            + f"，账号 {account.name} 剩余 {account.downloads_left} 次"
        )
        return filename, size, content

    async def download(
        self,
        book: Book,
        *,
        sink: ChunkSink | None = None,
        on_progress: ProgressHook | None = None,
    ) -> tuple[Account, str, bytes]:
        """下载一本书：依次尝试有额度的账号，登录失效或额度用尽就换下一个。

        sink: `async (filename, chunk)`，边下边写盘；给了 sink 时不返回内容字节。
        on_progress: `async (已读字节, 声明总字节)`，声明总字节未知时为 0。
        返回 (使用的账号, 文件名, 内容)。
        """
        await self.ensure_login()
        if not any(a.logged_in for a in self.accounts):
            raise ZlibError(
                "auth_failed",
                "没有可用的账号，下载需要登录：请在插件配置中填写 remix 凭证或邮箱密码",
                "; ".join(f"{a.name}: {a.last_error}" for a in self.accounts)[:200],
            )
        last_error: ZlibError | None = None
        tried: set[str] = set()
        while True:
            account = next(
                (a for a in self._usable_accounts() if a.name not in tried), None
            )
            if account is None:
                break
            tried.add(account.name)
            self._reserved[account.name] = self._reserved.get(account.name, 0) + 1
            try:
                filename, size, content = await self._transfer(
                    account, book, sink=sink, on_progress=on_progress
                )
            except ZlibError as e:
                if e.category == "auth_failed":
                    account.logged_in = False
                    account.last_error = e.message
                    last_error = e
                    continue
                if e.category == "quota_exhausted":
                    account.downloads_today = account.downloads_limit
                    last_error = e
                    continue
                raise
            finally:
                self._reserved[account.name] -= 1
            return account, filename, content
        if last_error is not None and last_error.category == "auth_failed":
            raise last_error
        used = "; ".join(
            f"{a.name}({a.downloads_today}/{a.downloads_limit})"
            for a in self.accounts
            if a.logged_in
        )
        raise ZlibError(
            "quota_exhausted",
            "账号池今日下载额度已全部用完，请明天再试或添加账号",
            used,
        )

    async def download_by_id(self, book_id: int) -> tuple[Account, str, bytes]:
        book = self.book_cache.get(int(book_id))
        if book is None:
            raise ZlibError("api_error", f"没有 id={book_id} 的书籍信息，请先搜索")
        return await self.download(book)

    async def _refresh_quota(self, account: Account) -> None:
        """下载后与服务端对齐额度；失败则保留本地累计值。"""
        try:
            resp = await asyncio.wait_for(
                self._api("GET", EP_PROFILE, account=account, what="刷新额度"),
                QUOTA_REFRESH_TIMEOUT,
            )
        except ZlibError as e:
            logger.debug(f"额度刷新失败: {e}")
            return
        except asyncio.TimeoutError:
            logger.debug("额度刷新超时")
            return
        self._apply_profile(account, resp)

    # ---------- 状态与诊断 ----------

    def account_status(self) -> list[dict]:
        return [
            {
                "name": a.name,
                "logged_in": a.logged_in,
                "premium": a.is_premium,
                "login_mode": "remix" if a.has_remix else "password",
                "downloads_today": a.downloads_today,
                "downloads_limit": a.downloads_limit,
                "downloads_left": a.downloads_left,
                "last_error": a.last_error,
            }
            for a in self.accounts
        ]

    async def _probe_domain(self, base_url: str) -> None:
        """对单个镜像发一次最小搜索；超过 PROBE_TIMEOUT 按网络错误计。"""

        async def request() -> None:
            status, resp = await self.transport.request_json(
                base_url,
                "POST",
                EP_SEARCH,
                data={"message": "test", "limit": 1},
            )
            if not _is_success(resp):
                raise _classify_business_error(status, resp, "探测")

        try:
            await asyncio.wait_for(request(), PROBE_TIMEOUT)
        except asyncio.TimeoutError as e:
            raise ZlibError(
                "network_error", f"探测超过 {PROBE_TIMEOUT:.0f} 秒未响应"
            ) from e

    async def _probe_pool(self) -> None:
        try:
            await self.domains.probe_all(
                self._probe_domain, concurrency=PROBE_CONCURRENCY
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - 探测失败不应影响调用方
            logger.warning(f"镜像探测失败: {e}")

    async def ensure_probed(self) -> None:
        """保证域名池有近期可用的镜像记录；探测结果全局共享。

        并发调用共用同一个进行中的探测任务；调用方被取消（如搜索超时）时探测
        继续跑完，结果留给后续请求。
        """
        if self.domains.has_recent_ok(PROBE_TTL):
            return
        task = self._probe_task
        if task is None or task.done():
            now = time.monotonic()
            if now - self._last_probe_at < PROBE_MIN_INTERVAL:
                return
            self._last_probe_at = now
            task = self._probe_task = asyncio.create_task(self._probe_pool())
        await asyncio.shield(task)

    async def diagnose(self) -> Diagnosis:
        """并发探测全部镜像域名（各发一次最小搜索），并刷新账号状态。"""

        self._last_probe_at = time.monotonic()
        reports = await self.domains.probe_all(
            self._probe_domain, concurrency=PROBE_CONCURRENCY
        )
        await self.ensure_login(force=True)
        return Diagnosis(
            domains=reports,
            accounts=self.account_status(),
            proxy=mask_proxy(self.proxy),
        )
