"""Z-Library E-API 异步客户端（自研，基于 aiohttp）。

参考：
- 端点文档: https://github.com/baroxyton/zlibrary-eapi-documentation
- 实现参考: https://github.com/bipinkrish/Zlibrary-API（同步版，已按 AstrBot 规范改写为异步）

设计要点：
- 所有网络请求异步（aiohttp），符合 AstrBot 开发原则（禁止 requests）
- 账号池：每个账号独立记录每日下载额度（downloads_today / downloads_limit）
- 错误分类：统一抛 ZlibError，带 category 便于上层翻译成友好提示
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import aiohttp
from PIL import Image

# 使用 AstrBot 插件 logger（与插件日志格式一致，避免 loguru record 缺字段）
from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

# 各端点（域名由配置提供，前缀 https://{domain}）
EP_LOGIN = "/eapi/user/login"
EP_PROFILE = "/eapi/user/profile"
EP_SEARCH = "/eapi/book/search"
EP_FILE = "/eapi/book/{book_id}/{hash_id}/file"

# 默认请求头（模拟浏览器，降低被风控概率）
DEFAULT_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

# 封面 CDN 兜底域名：Z-Library 封面图由独立 CDN 提供（如 covers.zlibcdn2.com），
# 该域名经常不可达；原 URL 下载失败时依次替换为这些域名重试（实测 z-lib.fm 可达）
COVER_FALLBACK_HOSTS = ("z-lib.fm",)

# 错误分类（category）：
#   network_error    网络异常（超时/连接失败/代理错误）
#   rate_limited     IP 被 Z-Library 限流（Err #ipd3 等）
#   auth_failed      账号凭据无效或登录失效
#   quota_exhausted  账号池今日下载额度全部用完
#   domain_invalid   域名失效（404 / CF 挑战页 / 重定向异常）
#   api_error        eapi 返回的其他业务错误


class ZlibError(Exception):
    """Z-Library API 统一异常。"""

    def __init__(self, category: str, message: str, detail: str = ""):
        super().__init__(message)
        self.category = category
        self.message = message
        self.detail = detail

    def __str__(self) -> str:
        return f"[{self.category}] {self.message}"


@dataclass
class Account:
    """账号池中的一个账号及其实时额度状态。"""

    name: str  # 配置中的备注名（如 account1）
    email: str = ""
    password: str = ""
    remix_userid: str = ""
    remix_userkey: str = ""
    logged_in: bool = False
    downloads_today: int = 0
    downloads_limit: int = 10
    last_error: str = ""

    @property
    def downloads_left(self) -> int:
        return max(0, self.downloads_limit - self.downloads_today)

    def has_quota(self) -> bool:
        return self.downloads_left > 0


def _is_rate_limited(text: str) -> bool:
    """识别 IP 限流响应。"""
    return "Too many requests" in text or "#ipd3" in text or "Err #" in text


def _is_cf_challenge(text: str) -> bool:
    """识别 Cloudflare 人机验证页。"""
    return (
        "Checking your browser" in text
        or "cf-challenge" in text
        or ("<html" in text.lower() and "challenge" in text.lower())
    )


def _fix_mojibake(text: str) -> str:
    """修复 Z-Library 返回的双重编码文本。

    现象：含重音字符的字段（如作者名 Frédéric）会变成 FrÃ©dÃ©ric，
    原因是服务器端把 UTF-8 字节按 Latin-1 解码后又按 UTF-8 编码发送。

    修复：尝试按 latin-1 编码回原始字节，再按 utf-8 解码。
    仅当文本全部落在 Latin-1 范围内时才转换，中文等不受影响（会原样返回）。
    """
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _fix_mojibake_recursive(obj):
    """递归修复 JSON 结构中所有字符串的 mojibake（在 json.loads 之后调用）。"""
    if isinstance(obj, str):
        return _fix_mojibake(obj)
    if isinstance(obj, list):
        return [_fix_mojibake_recursive(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _fix_mojibake_recursive(value) for key, value in obj.items()}
    return obj


def _sanitize_filename(filename: str) -> str:
    """清洗文件名为跨平台安全格式。

    书名可能含 Windows 非法字符（\\ / : * ? " < > |）或控制字符，
    直接保存会触发 OSError；统一替换为下划线，并去掉首尾点/空格。
    """
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", filename).strip(" .")
    return cleaned or "book"


def _replace_host(url: str, new_host: str) -> str | None:
    """替换 URL 的主机名（封面 CDN 域名失效时切换镜像域名重试）。"""
    try:
        p = urlparse(url)
        if not p.hostname:
            return None
        return urlunparse(
            (p.scheme or "https", new_host, p.path, p.params, p.query, p.fragment)
        )
    except Exception:  # noqa: BLE001 - 无法解析的 URL 返回 None
        return None


def _guess_image_mime(content: bytes) -> str:
    """按文件头魔数判断图片真实类型（比响应头 Content-Type 可靠）。

    Z-Library 封面 CDN 常返回 application/octet-stream 或缺省类型，
    若按响应头猜成 image/jpeg 而实际是 PNG，浏览器解码失败图片不显示。
    """
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _resize_cover(content: bytes, max_width: int = 168) -> bytes:
    """缩放封面并转 JPEG，压缩 base64 体积（卡片封面显示宽仅 84px，2x 清晰度 168px 足够）。

    压缩失败返回原图（调用方按魔数判断类型）。
    """
    try:
        img = Image.open(io.BytesIO(content))
        if img.width > max_width:
            ratio = max_width / img.width
            img = img.resize(
                (max_width, max(1, int(img.height * ratio))), Image.LANCZOS
            )
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return buf.getvalue()
    except Exception:  # noqa: BLE001 - 压缩失败退化为原图
        return content


def _normalize_domain(domain: str) -> str:
    """规范化域名配置：去掉 http(s):// 协议前缀与多余斜杠。

    用户可能从浏览器地址栏复制带协议的域名（如 https://z-library.sk/），
    若不处理会拼出 https://https://z-library.sk 的无效 URL。
    """
    d = domain.strip()
    for prefix in ("https://", "http://"):
        if d.lower().startswith(prefix):
            d = d[len(prefix):]
            break
    return d.strip("/")


class ZlibClient:
    """Z-Library E-API 异步客户端。"""

    def __init__(
        self,
        accounts: list[dict],
        domain: str = "z-library.sk",
        proxy: str = "",
        timeout: float = 30.0,
    ):
        self.domain = _normalize_domain(domain)
        self.proxy = proxy or None
        self.timeout = timeout
        self.pool: list[Account] = []
        for acc in accounts:
            if not isinstance(acc, dict):
                continue
            self.pool.append(
                Account(
                    name=str(acc.get("name", acc.get("email", "account"))),
                    email=str(acc.get("email", "") or ""),
                    password=str(acc.get("password", "") or ""),
                    remix_userid=str(acc.get("remix_userid", "") or ""),
                    remix_userkey=str(acc.get("remix_userkey", "") or ""),
                )
            )
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()
        # 登录互斥锁：防止插件更新/重载导致新旧实例并发触发 login_all
        self._login_lock = asyncio.Lock()
        # 搜索结果的书籍缓存：id -> book dict（下载时按 id 取完整信息，含 hash）
        # 持久化到磁盘，AstrBot 重启后仍可凭 id 下载
        self.book_cache: dict[int, dict] = {}
        self._load_book_cache()

    # ---------- 书籍缓存持久化 ----------

    def _cache_file(self) -> str:
        try:
            base = get_astrbot_data_path()
        except Exception:  # noqa: BLE001 - 非 AstrBot 环境（如独立测试）时退化到内存缓存
            base = os.path.join(os.path.expanduser("~"), ".astrbot_zlibrary")
        return os.path.join(base, "zlibrary_assistant", "book_cache.json")

    def _load_book_cache(self):
        try:
            path = self._cache_file()
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                    self.book_cache = {int(k): v for k, v in data.items()}
        except Exception:  # noqa: BLE001 - 缓存损坏不影响运行
            self.book_cache = {}

    def _save_book_cache(self):
        try:
            path = self._cache_file()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {str(k): v for k, v in self.book_cache.items()},
                    f,
                    ensure_ascii=False,
                )
        except Exception as e:  # noqa: BLE001 - 缓存写入失败不影响运行
            logger.warning(f"书籍缓存写入失败: {e}")

    # ---------- 会话管理 ----------

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            async with self._session_lock:
                if self._session is None or self._session.closed:
                    timeout = aiohttp.ClientTimeout(total=self.timeout)
                    self._session = aiohttp.ClientSession(
                        timeout=timeout,
                        headers=DEFAULT_HEADERS,
                    )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ---------- 基础请求 ----------

    def _base_url(self) -> str:
        return f"https://{self.domain}"

    def _cookies(self, account: Account) -> dict:
        cookies = {"siteLanguageV2": "en"}
        if account.remix_userid and account.remix_userkey:
            cookies["remix_userid"] = account.remix_userid
            cookies["remix_userkey"] = account.remix_userkey
        return cookies

    def _cover_cookies(self) -> dict | None:
        """封面下载用的 cookies：取账号池第一个账号的登录凭证。

        封面 CDN（covers.z-lib.sk 等）要求登录态，裸请求会被拒
        （HTTP 513）；搜索能成功正是因为 E-API 请求带了账号 cookies。
        没配置账号时返回 None（匿名请求）。
        """
        if not self.pool:
            return None
        return self._cookies(self.pool[0])

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        account: Account | None = None,
        data: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """发请求并解析 JSON；对常见异常做分类。"""
        session = await self._get_session()
        url = self._base_url() + path
        kwargs: dict = {"proxy": self.proxy, "ssl": False}
        if account is not None:
            kwargs["cookies"] = self._cookies(account)
        if params:
            kwargs["params"] = params

        try:
            async with session.request(method, url, data=data, **kwargs) as resp:
                # 强制 UTF-8 解码：Z-Library 响应头常缺失 charset，aiohttp 会误判为 latin-1
                text = await resp.text(encoding="utf-8", errors="replace")
        except (
            aiohttp.ClientConnectorError,
            aiohttp.ServerTimeoutError,
            asyncio.TimeoutError,
        ) as e:
            raise ZlibError(
                "network_error",
                "网络异常（连接失败或超时）",
                f"{type(e).__name__}: {e}",
            )
        except aiohttp.ClientError as e:
            raise ZlibError("network_error", "网络请求失败", str(e))

        # 域名失效：CF 挑战页 / 非 JSON
        if resp.status == 404 or resp.status >= 500:
            raise ZlibError(
                "domain_invalid",
                f"当前域名 {self.domain} 可能已失效（HTTP {resp.status}）",
                text[:200],
            )

        if _is_cf_challenge(text):
            raise ZlibError(
                "domain_invalid",
                f"当前域名 {self.domain} 触发了人机验证，可能已失效",
                text[:200],
            )

        if _is_rate_limited(text):
            raise ZlibError(
                "rate_limited",
                "Z-Library 对当前 IP 限流了（可能因为请求频繁或代理 IP 被标记），请稍后再试或更换代理节点",
                text[:200],
            )

        try:
            return _fix_mojibake_recursive(json.loads(text))
        except json.JSONDecodeError:
            raise ZlibError(
                "api_error",
                "Z-Library 返回了无法解析的内容",
                text[:200],
            )

    # ---------- 账号与登录 ----------

    async def _apply_login_response(self, account: Account, resp: dict):
        """从登录/profile 响应中提取凭据与额度。"""
        if not resp.get("success"):
            raise ZlibError(
                "auth_failed",
                "账号登录失败（邮箱或密码错误）",
                json.dumps(resp, ensure_ascii=False)[:200],
            )
        user = resp.get("user") or {}
        account.email = user.get("email", account.email)
        account.remix_userid = str(user.get("id", account.remix_userid))
        account.remix_userkey = str(
            user.get("remix_userkey", account.remix_userkey) or ""
        )
        account.downloads_today = int(user.get("downloads_today", 0))
        account.downloads_limit = int(user.get("downloads_limit", 10))
        account.logged_in = True
        account.last_error = ""

    async def login_account(self, account: Account) -> Account:
        """登录一个账号（remix id/key 优先，email+password 后备）。"""
        # remix 凭证优先：GET /eapi/user/profile 验证，不走 login 端点，
        # 不受 Z-Library 对 login 端点的 IP 风控（#ipd3）影响
        if account.remix_userid and account.remix_userkey:
            resp = await self._request_json(
                "GET",
                EP_PROFILE,
                account=account,
            )
            await self._apply_login_response(account, resp)
        elif account.email and account.password:
            try:
                resp = await self._request_json(
                    "POST",
                    EP_LOGIN,
                    account=account,
                    data={"email": account.email, "password": account.password},
                )
            except ZlibError as e:
                if e.category == "rate_limited":
                    raise ZlibError(
                        "rate_limited",
                        f"{e.message}。提示：Z-Library 对 login 端点风控较严，"
                        f"建议在插件配置中改用 remix_userid+remix_userkey 方式登录"
                        f"（走 GET profile 验证，几乎不受该限流影响）",
                    )
                raise
            await self._apply_login_response(account, resp)
        else:
            raise ZlibError(
                "auth_failed",
                f"账号 {account.name} 缺少凭据：需填 email+password 或 remix_userid+remix_userkey",
            )
        logger.info(
            f"账号 {account.name} 登录成功，今日已用 {account.downloads_today}/{account.downloads_limit} 次下载"
        )
        return account

    async def login_all(self):
        """登录账号池全部账号；单个失败不影响其他账号。

        防重入：并发触发（如插件更新/重载时新旧实例并行 initialize）
        时通过互斥锁保证只执行一轮；已登录成功的账号跳过。
        """
        if self._login_lock.locked():
            logger.info("账号池登录已在执行中，跳过重复登录")
            return
        async with self._login_lock:
            for account in self.pool:
                if account.logged_in:
                    continue
                try:
                    await self.login_account(account)
                except ZlibError as e:
                    account.logged_in = False
                    account.last_error = e.message
                    logger.warning(f"账号 {account.name} 登录失败: {e}")
        if not any(a.logged_in for a in self.pool):
            raise ZlibError(
                "auth_failed",
                "账号池中所有账号均登录失败，请检查插件配置",
            )

    def pick_download_account(self) -> Account:
        """挑选用于下载的账号：今日剩余额度最多者；无额度则抛 quota_exhausted。"""
        candidates = [a for a in self.pool if a.logged_in and a.has_quota()]
        if not candidates:
            used = [
                f"{a.name}({a.downloads_today}/{a.downloads_limit})"
                for a in self.pool
                if a.logged_in
            ]
            raise ZlibError(
                "quota_exhausted",
                "账号池今日下载额度已全部用完（每个账号每日 10 次）",
                "; ".join(used),
            )
        # 额度剩余最多者优先
        return max(candidates, key=lambda a: a.downloads_left)

    # ---------- 搜索 ----------

    async def search(
        self,
        message: str,
        limit: int = 5,
        language: str = "",
        extension: str = "",
    ) -> list[dict]:
        """按关键词搜索图书，返回书籍列表（含 id/hash 供下载）。"""
        account = next((a for a in self.pool if a.logged_in), None)
        if account is None:
            # 未登录时尝试匿名搜索（部分域名允许）
            account = Account(name="anonymous")
        data: dict = {"message": message, "limit": limit}
        if language:
            data["languages[]"] = language
        if extension:
            data["extensions[]"] = extension
        resp = await self._request_json("POST", EP_SEARCH, account=account, data=data)
        if not resp.get("success"):
            raise ZlibError(
                "api_error",
                "搜索失败",
                json.dumps(resp, ensure_ascii=False)[:200],
            )
        books = resp.get("books") or []
        # 缓存书籍信息（供 download_by_id 使用），并持久化到磁盘
        for b in books:
            try:
                self.book_cache[int(b["id"])] = b
            except (KeyError, TypeError, ValueError):
                continue
        self._save_book_cache()
        return books

    async def fetch_cover_base64(self, url: str, max_bytes: int = 1024 * 1024) -> str:
        """下载封面图并转成 base64 data URI，供 HTML 卡片内嵌。

        背景：AstrBot 云端文转图服务在远程服务器渲染模板，访问不了
        Z-Library 封面 CDN 外链（实测封面全部加载失败）；把封面以
        base64 内嵌进 HTML 后，渲染不再依赖外网。
        原 URL 下载失败时自动把域名替换为可达镜像（COVER_FALLBACK_HOSTS）重试；
        下载后缩放压缩，按文件头魔数判断真实图片类型。
        失败返回空字符串（调用方降级为占位图），原因记 WARNING 日志。
        """
        if not url or not url.startswith(("http://", "https://")):
            return ""
        candidates = [url]
        for host in COVER_FALLBACK_HOSTS:
            alt = _replace_host(url, host)
            if alt and alt not in candidates:
                candidates.append(alt)
        content: bytes | None = None
        last_err = "未知错误"
        for cand in candidates:
            content, last_err = await self._download_cover(cand, max_bytes)
            if content:
                break
        if not content:
            logger.warning(
                f"封面下载失败（已尝试 {len(candidates)} 个地址）: {url[:100]} - {last_err}"
            )
            return ""
        compressed = _resize_cover(content)
        mime = _guess_image_mime(compressed)
        return f"data:{mime};base64,{base64.b64encode(compressed).decode('ascii')}"

    async def _download_cover(self, url: str, max_bytes: int) -> tuple[bytes | None, str]:
        """下载封面图片，返回 (内容, 错误信息)；失败时内容为 None。

        请求统一带账号池第一个账号的 cookies（封面 CDN 需要登录态，
        否则返回 HTTP 513）；没配置账号时不带。
        """
        session = await self._get_session()
        kwargs: dict = {"proxy": self.proxy, "ssl": False}
        cover_cookies = self._cover_cookies()
        if cover_cookies:
            kwargs["cookies"] = cover_cookies
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=8),
                **kwargs,
            ) as resp:
                if resp.status != 200:
                    return None, f"HTTP {resp.status}"
                content = await resp.read()
                if not content:
                    return None, "空内容"
                if len(content) > max_bytes:
                    return None, f"文件过大({len(content)}B)"
                return content, ""
        except Exception as e:  # noqa: BLE001 - 下载失败由调用方统一处理
            return None, f"{type(e).__name__}: {e}"

    async def download_by_id(self, book_id: int) -> tuple[Account, str, bytes]:
        """按 id 下载书籍（id 必须来自之前 search 的结果缓存，缓存已持久化）。

        若缓存中无该书，抛 ZlibError 提示先搜索。
        """
        book = self.book_cache.get(int(book_id))
        if book is None:
            raise ZlibError(
                "api_error",
                f"没有 id={book_id} 的书籍信息，请先调用 zlib_search_books 搜索",
            )
        return await self.download(book)

    # ---------- 下载 ----------

    async def get_download_link(
        self, account: Account, book: dict
    ) -> tuple[str, str, str]:
        """获取下载直链。返回 (download_url, filename, extension)。"""
        book_id = book.get("id")
        hash_id = book.get("hash")
        if not book_id or not hash_id:
            raise ZlibError(
                "api_error", "书籍信息缺少 id/hash，无法下载", str(book)[:200]
            )
        resp = await self._request_json(
            "GET",
            EP_FILE.format(book_id=book_id, hash_id=hash_id),
            account=account,
        )
        if not resp.get("success") or not resp.get("file", {}).get("downloadLink"):
            raise ZlibError(
                "api_error",
                "获取下载链接失败（可能该书无可用文件）",
                json.dumps(resp, ensure_ascii=False)[:200],
            )
        file_info = resp["file"]
        dl = file_info["downloadLink"]
        ext = file_info.get("extension", "bin")
        description = file_info.get("description", "")
        # 描述形如 "书名-作者 (z-library.sk...)"，清理成文件名
        filename = description.split(" (")[0].strip() or f"book_{book_id}"
        filename = _sanitize_filename(filename)
        if not filename.endswith("." + ext):
            filename = f"{filename}.{ext}"
        return dl, filename, ext

    async def download(self, book: dict) -> tuple[Account, str, bytes]:
        """按书籍信息下载文件。

        返回 (account, filename, file_bytes)。自动挑选额度账号并扣减额度。
        """
        account = self.pick_download_account()
        dl, filename, _ext = await self.get_download_link(account, book)

        session = await self._get_session()
        try:
            async with session.get(dl, proxy=self.proxy, ssl=False) as resp:
                if resp.status != 200:
                    raise ZlibError(
                        "api_error",
                        f"下载失败（HTTP {resp.status}）",
                        dl[:200],
                    )
                content = await resp.read()
        except (
            aiohttp.ClientConnectorError,
            aiohttp.ServerTimeoutError,
            asyncio.TimeoutError,
        ) as e:
            raise ZlibError("network_error", "下载网络异常", str(e))
        except aiohttp.ClientError as e:
            raise ZlibError("network_error", "下载失败", str(e))

        if not content:
            raise ZlibError("api_error", "下载到的文件为空", dl[:200])
        # 扣减额度
        account.downloads_today += 1
        logger.info(
            f"下载成功: {filename} ({len(content)} bytes)，账号 {account.name} 剩余 {account.downloads_left} 次"
        )
        return account, filename, content

    # ---------- 状态 ----------

    async def get_status(self) -> list[dict]:
        """返回账号池状态（用于 zlib_get_status 工具）。"""
        result = []
        for account in self.pool:
            result.append(
                {
                    "name": account.name,
                    "logged_in": account.logged_in,
                    "email": account.email,
                    "downloads_today": account.downloads_today,
                    "downloads_limit": account.downloads_limit,
                    "downloads_left": account.downloads_left,
                    "last_error": account.last_error,
                }
            )
        return result
