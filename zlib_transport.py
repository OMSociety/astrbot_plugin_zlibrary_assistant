"""HTTP 传输层：E-API 请求、响应分类、SSRF 受控的 GET。

职责边界：
- request_json：向某个镜像域名发 E-API 请求，返回 (HTTP 状态码, JSON)；
  基础设施层面的失败（网络、TLS、DiamWall / Cloudflare 验证页、限流、非 JSON）
  统一抛 ZlibError，由上层的域名池决定是否换域名重试。
- guarded_get：封面与下载直链来自 API 响应（第三方内容），每一跳都做 SSRF 校验。

DiamWall 是 JS 挑战型 WAF，纯 HTTP 客户端无法通过；本层只负责准确识别，
绕行靠换用未启用该防护的镜像域名（见 zlib_domains）。
"""

from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import urljoin, urlparse

import aiohttp
from aiohttp_socks import ProxyConnector

# 使用 AstrBot 插件 logger（与插件日志格式一致）
from astrbot.api import logger

from .zlib_errors import ZlibError
from .zlib_security import is_private_ip, is_safe_public_url
from .zlib_text import fix_mojibake_recursive

# E-API 要求 `source: android`，缺省时搜索端点直接返回 400 Access denied
API_SOURCE_HEADER = {"source": "android"}
BASE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
    ),
}

# 重定向不自动跟随（防公网 302 跳内网的 SSRF 放大），手动逐跳校验。
# 两条路径的跳数上限分开：守链跟的是远端响应给出的任意 URL，每跳都要过
# URL 校验与对端 IP 核验，深度多一跳就多放大一次"远端可控跳转"；API 通道
# 跟的是配置域名内的同源跳转（跨主机直接拒），可以放宽到防护握手所需的深度。
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_GUARDED_REDIRECT_HOPS = 3
MAX_API_REDIRECT_HOPS = 5

# E-API JSON 响应体读取上限：业务响应是 KB 级，上限防被投毒/劫持的端点返回
# 无限流，把插件内存吃光
MAX_API_BYTES = 8 * 1024 * 1024

# 建立 TCP 连接的超时：连不上的域名尽快判负并转移，不必等满整体超时
CONNECT_TIMEOUT = 8.0

# Set-Cookie 名称的合法字符（RFC 6265 token）：用来区分 `name=value` 与属性
_COOKIE_NAME_RE = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")


def is_rate_limited(text: str) -> bool:
    """识别 IP 限流响应。

    只匹配明确的限流文案，不能加宽泛的 "Err #"——E-API 的业务错误
    （文件不存在、参数错误等）也以 "Err #N" 开头，误判会让用户看到
    "IP 被限流"的误导提示。
    """
    return "Too many requests" in text or "#ipd3" in text


def is_diamwall_page(server: str, text: str) -> bool:
    """识别 DiamWall 验证页 / 拒绝页。"""
    return (
        "diamwall" in server.lower()
        or "DiamWall" in text
        or "Verifying your browser" in text
    )


def is_cf_challenge(server: str, status: int, text: str) -> bool:
    """识别 Cloudflare 人机验证页。"""
    lowered = text.lower()
    if "checking your browser" in lowered or "cf-challenge" in lowered:
        return True
    if "just a moment" in lowered and "<html" in lowered:
        return True
    return (
        "cloudflare" in server.lower() and status in (403, 503) and "<html" in lowered
    )


def is_same_origin(target: str, base: str) -> bool:
    """判断重定向目标是否与配置的接口地址同源。

    同源口径是 scheme + 主机 + 端口完全相同（主机名大小写不敏感，
    显式默认端口 `:443` / `:80` 与其省略写法等价）。
    跨主机跳转会带着账号 cookies 去往未经校验的主机；跨 scheme 跳转
    （https → http）是明文降级——两者都拒绝。
    """
    target_parts = urlparse(target)
    base_parts = urlparse(base)
    return (
        target_parts.scheme in ("http", "https")
        and target_parts.scheme == base_parts.scheme
        and (target_parts.hostname or "").lower() == (base_parts.hostname or "").lower()
        and _effective_port(target_parts) == _effective_port(base_parts)
    )


def _effective_port(parts) -> int | None:
    """取 URL 的有效端口：显式端口优先，否则用 scheme 的默认端口。"""
    if parts.port is not None:
        return parts.port
    return {"http": 80, "https": 443}.get(parts.scheme)


def merge_set_cookies(cookies: dict, set_cookie_headers: list[str]) -> dict:
    """把重定向响应里的 Set-Cookie 并进请求 cookies（名称 -> 值）。

    只取名称与值：Domain / Path / Secure 等属性对本客户端无意义（请求只发往
    同一主机），而"带着防护 cookie 重试"才是握手放行的必要条件。

    按 RFC 6265 只认整条里的**第一个** `name=value`，后面全是属性——否则
    `a=b; Priority=High` 这种响应会把 `Priority` 也当成一个 cookie。
    """
    merged = dict(cookies)
    for header in set_cookie_headers:
        pair = header.split(";", 1)[0]
        name, sep, value = pair.partition("=")
        name = name.strip()
        if not sep or not name or not _COOKIE_NAME_RE.fullmatch(name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        if value:
            merged[name] = value
    return merged


def normalize_socks_proxy_url(value: str) -> str:
    """规范化 SOCKS URL：方案名不区分大小写，h/a 语义由 rdns=True 承担。"""
    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    if scheme == "socks5h":
        scheme = "socks5"
    elif scheme == "socks4a":
        scheme = "socks4"
    return parsed._replace(scheme=scheme).geturl()


async def read_limited(resp: aiohttp.ClientResponse, limit: int) -> bytes:
    """最多读取 limit+1 字节（多读 1 字节用于超限判定）。

    StreamReader.read(n) 每次只返回当前已到达的一块，不保证读满 n，
    因此循环读取直到流结束或超出上限。
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.content.iter_chunked(64 * 1024):
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            break
    return b"".join(chunks)[: limit + 1]


def classify_body(
    status: int, server: str, text: str
) -> tuple[dict | None, ZlibError | None]:
    """把一次 E-API 响应归类为 (JSON, None) 或 (None, 基础设施错误)。

    业务层错误（登录失效、额度用尽等）也是合法 JSON，原样交给上层按字段判断。
    """
    if is_rate_limited(text) or status == 429:
        return None, ZlibError(
            "rate_limited",
            "Z-Library 对当前出口 IP 限流（请求过于频繁或代理 IP 被标记）",
            text[:200],
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        return fix_mojibake_recursive(payload), None
    if is_diamwall_page(server, text):
        return None, ZlibError(
            "diamwall_blocked",
            "该域名启用了 DiamWall 浏览器验证，纯 HTTP 客户端无法通过",
            text[:200],
        )
    if is_cf_challenge(server, status, text):
        return None, ZlibError(
            "cloudflare_blocked", "该域名触发了 Cloudflare 人机验证", text[:200]
        )
    return None, ZlibError(
        "domain_invalid",
        f"该域名返回了非 E-API 内容（HTTP {status}）",
        text[:200],
    )


class ZlibTransport:
    """共享 aiohttp 会话 + 代理装配 + 建连期对端观测。"""

    def __init__(self, proxy: str = "", timeout: float = 30.0):
        self.proxy = proxy or None
        self.timeout = timeout
        self.socks = bool(
            self.proxy
            and urlparse(self.proxy).scheme.lower()
            in {"socks4", "socks4a", "socks5", "socks5h"}
        )
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()
        # 建连期捕获的对端 IP：host -> peername。aiohttp 在响应体读完后立即释放
        # 连接（ClientResponse.connection 变 None），此后无从取对端；建连期是唯一
        # 稳定观测点（keep-alive 复用连接不重新建连，见 peer_for_get）
        self._peers: dict[str, str] = {}

    # ---------- 会话管理 ----------

    def _install_peer_capture(self, connector) -> None:
        """在 connector 上挂建连期对端观测。

        aiohttp 的 TraceConfig 也挂在建连期，但其 `on_connection_create_end`
        只收到 (session, trace_config_ctx, params)，params 里没有任何连接对象，
        取不到 transport；因此只能在 connector 的建连方法上包一层。
        """
        if getattr(connector, "_zl_peer_capture", False):
            return
        inner_create = connector._create_connection

        async def _create_with_peer(req, traces, timeout):
            proto = await inner_create(req, traces, timeout)
            try:
                transport = getattr(proto, "transport", None)
                peer = (
                    transport.get_extra_info("peername")
                    if transport is not None
                    else None
                )
                if peer and peer[0]:
                    # 按主机记录，不按连接：复用连接不重新建连，无从观测
                    self._peers[req.url.host] = str(peer[0])
            except Exception:  # noqa: BLE001 - 观测失败不影响建连
                pass
            return proto

        connector._create_connection = _create_with_peer
        connector._zl_peer_capture = True

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            async with self._session_lock:
                if self._session is None or self._session.closed:
                    timeout = aiohttp.ClientTimeout(
                        total=self.timeout, sock_connect=CONNECT_TIMEOUT
                    )
                    connector = None
                    if self.socks:
                        connector = ProxyConnector.from_url(
                            normalize_socks_proxy_url(self.proxy), rdns=True
                        )
                    self._session = aiohttp.ClientSession(
                        timeout=timeout, headers=BASE_HEADERS, connector=connector
                    )
                    # 直连时建立建连期观测；包代理时 guarded_get 不对端核验，
                    # 记录下来的也只是代理地址，不参与判定
                    if not self.socks and self._session.connector is not None:
                        self._peers.clear()
                        self._install_peer_capture(self._session.connector)
        return self._session

    async def close(self) -> None:
        self._peers.clear()
        if self._session and not self._session.closed:
            await self._session.close()

    def proxy_kwargs(self) -> dict:
        """SOCKS 由 session connector 接管；HTTP(S) 代理按请求传入。"""
        return {} if self.socks or not self.proxy else {"proxy": self.proxy}

    # ---------- E-API ----------

    async def request_json(
        self,
        base_url: str,
        method: str,
        path: str,
        *,
        cookies: dict | None = None,
        data: dict | None = None,
        params: dict | None = None,
    ) -> tuple[int, dict]:
        """向 base_url 发 E-API 请求，返回 (HTTP 状态码, JSON 字典)。

        3xx 手动逐跳跟随，但只允许跳回同一个源（scheme + 主机 + 端口）：
        前置防护常先回一个自指 307 种下 cookie，客户端带着 cookie 重发才放行，
        因此不能一律拒绝 3xx；而跨主机跳转会把带账号 cookies 的请求送去未经
        校验的主机，必须拒绝。

        不关 TLS 校验：这条通道承载 remix userid/userkey 与登录 POST，它们是
        账号的等价凭据；aiohttp 默认（校验证书链与主机名）即为正确行为。
        onion 模式是明文 http，由 SOCKS 代理承担链路。
        """
        session = await self.get_session()
        url = base_url + path
        domain = urlparse(base_url).hostname or base_url
        kwargs: dict = {**self.proxy_kwargs()}
        if cookies:
            kwargs["cookies"] = dict(cookies)
        if params:
            kwargs["params"] = params
        headers = dict(API_SOURCE_HEADER)
        if data is not None:
            # 表单 Content-Type 只在带请求体的 POST 上有意义
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        kwargs["headers"] = headers

        # 重定向响应里的 Set-Cookie 必须自己收进 cookies 再重发：只要调用方显式传了
        # cookies=，aiohttp 就不再合并 cookie jar，防护握手会卡在跳数上限。
        hop = 0
        while True:
            try:
                async with session.request(
                    method, url, data=data, allow_redirects=False, **kwargs
                ) as resp:
                    # 强制 UTF-8 解码：响应头常缺失 charset，aiohttp 会误判为 latin-1。
                    # 先按硬上限读字节再解码，避免异常端点用无限响应体撑爆内存。
                    raw = await read_limited(resp, MAX_API_BYTES)
                    if len(raw) > MAX_API_BYTES:
                        raise ZlibError(
                            "domain_invalid",
                            f"{domain} 的响应超过 {MAX_API_BYTES // 1024 // 1024} MiB 上限",
                        )
                    text = (
                        raw.decode("utf-8", errors="replace")
                        if isinstance(raw, bytes)
                        else raw
                    )
                    status = resp.status
                    server = resp.headers.get("Server") or ""
                    location = (
                        resp.headers.get("Location")
                        if status in REDIRECT_STATUSES
                        else None
                    )
                    set_cookies = list(resp.headers.getall("Set-Cookie", []))
            except aiohttp.ClientSSLError as e:
                # ClientConnectorCertificateError 是其子类：证书与握手失败同属 tls_error
                raise ZlibError(
                    "tls_error", f"{domain} 的 TLS 握手或证书校验失败", str(e)
                ) from e
            except (
                aiohttp.ClientConnectorError,
                aiohttp.ServerTimeoutError,
                asyncio.TimeoutError,
            ) as e:
                raise ZlibError(
                    "network_error",
                    f"连接 {domain} 失败或超时",
                    f"{type(e).__name__}: {e}",
                ) from e
            except aiohttp.ClientError as e:
                raise ZlibError("network_error", f"请求 {domain} 失败", str(e)) from e

            if status not in REDIRECT_STATUSES:
                break
            if not location:
                raise ZlibError(
                    "domain_invalid",
                    f"{domain} 的重定向缺少 Location（HTTP {status}）",
                    text[:200],
                )
            target = urljoin(url, location)
            if not is_same_origin(target, base_url):
                raise ZlibError(
                    "domain_invalid",
                    f"{domain} 重定向到了其他主机（HTTP {status}）",
                    target[:200],
                )
            if hop >= MAX_API_REDIRECT_HOPS:
                raise ZlibError(
                    "diamwall_blocked"
                    if is_diamwall_page(server, text)
                    else "domain_invalid",
                    f"{domain} 的重定向次数过多（超过 {MAX_API_REDIRECT_HOPS} 跳），"
                    "通常是验证握手无法完成",
                )
            hop += 1
            url = target
            if set_cookies and isinstance(kwargs.get("cookies"), dict):
                kwargs["cookies"] = merge_set_cookies(kwargs["cookies"], set_cookies)
            elif set_cookies:
                kwargs["cookies"] = merge_set_cookies({}, set_cookies)

        payload, error = classify_body(status, server, text)
        if error is not None:
            raise error
        return status, payload

    # ---------- SSRF 受控 GET ----------

    @staticmethod
    def _peer_ip(resp: aiohttp.ClientResponse) -> str | None:
        """从响应对象取对端（TCP peer）IP；取不到返回 None。

        resp.connection 在响应体读完（aiohttp 随即释放连接）或响应由测试桩
        构造时为 None，transport 也可能不支持 peername，故整段兜底。
        """
        try:
            connection = getattr(resp, "connection", None)
            if connection is None:
                return None
            peer = connection.transport.get_extra_info("peername")
            if not peer or not peer[0]:
                return None
            return str(peer[0])
        except Exception:  # noqa: BLE001 - 取不到对端信息
            return None

    def peer_for_get(self, resp: aiohttp.ClientResponse, url: str) -> str | None:
        """核验用的对端 IP：优先活连接，退回建连期记录。

        活连接最准确；响应已读完时连接已被释放，则用建连期按主机记录的
        对端（连接池按主机键控，复用连接必然是同一主机此前建过的连接）。
        """
        peer = self._peer_ip(resp)
        if peer:
            return peer
        host = urlparse(url).hostname or ""
        return self._peers.get(host)

    async def guarded_get(
        self,
        url: str,
        *,
        headers: dict | None = None,
        cookies: dict | None = None,
        timeout_total: float,
    ) -> tuple[aiohttp.ClientResponse | None, str]:
        """带 SSRF 校验的 GET：不自动跟随重定向，逐跳校验（最多 3 跳）。

        每一跳都重新校验目标为公网 http/https 地址（is_safe_public_url），
        防止 API 返回的 URL 或重定向把请求带去内网。
        返回 (响应, 错误信息)；成功时响应由调用方负责关闭。
        """
        session = await self.get_session()
        current = url
        for hop in range(MAX_GUARDED_REDIRECT_HOPS + 1):
            # getaddrinfo 是阻塞调用，放线程池避免卡事件循环
            ok, reason = await asyncio.to_thread(
                is_safe_public_url, current, allow_onion=self.socks
            )
            if not ok:
                return None, f"URL 未通过安全校验（{reason}）: {current[:200]}"
            try:
                resp = await session.get(
                    current,
                    allow_redirects=False,
                    **self.proxy_kwargs(),
                    # 不关 TLS 校验：明文或降级连接会让链路上的中间人可替换
                    # 封面/书籍字节（内容会内嵌进发给用户的模板或落盘）
                    headers=headers,
                    cookies=cookies,
                    timeout=aiohttp.ClientTimeout(total=timeout_total),
                )
            except (
                aiohttp.ClientConnectorError,
                aiohttp.ServerTimeoutError,
                asyncio.TimeoutError,
            ) as e:
                return None, f"网络异常: {type(e).__name__}: {e}"
            except aiohttp.ClientError as e:
                return None, f"{type(e).__name__}: {e}"
            # is_safe_public_url 只证明"此刻该域名解析到的 IP 都是公网"，aiohttp
            # 随后会自行重新解析——两次解析之间的 DNS 应答可变（rebinding）。
            # 因此再核验真实建连的对端 IP，而不是只信任解析结果。
            #
            # 拿不到对端时不拒绝：响应体读完（常见的单段小响应）aiohttp 就已释放
            # 连接，ClientResponse.connection 变 None 是常态而非异常信号；此时由
            # 上一层 URL 校验承担，判定退化为"拿到且是私网才拒绝"。
            #
            # 配置了代理时整段不适用：此时 TCP 对端是代理本身（通常是 127.0.0.1），
            # 按内网拒绝会把所有正常请求误杀；该分支只能依赖 URL 校验与代理自身。
            if self.proxy is None:
                peer = self.peer_for_get(resp, current)
                if peer is not None and is_private_ip(peer):
                    try:
                        await resp.release()
                    except Exception as e:  # noqa: BLE001 - 释放失败不影响拒绝判定
                        logger.debug(f"释放未通过校验的响应失败: {e}")
                    return None, f"实际连接的对端不是公网地址: {current[:200]}"
            if resp.status not in REDIRECT_STATUSES:
                return resp, ""
            if hop == MAX_GUARDED_REDIRECT_HOPS:
                await resp.release()
                return None, f"重定向超过 {MAX_GUARDED_REDIRECT_HOPS} 跳"
            location = resp.headers.get("Location")
            await resp.release()
            if not location:
                return None, "重定向缺少 Location"
            current = urljoin(current, location)
        return None, "重定向异常"  # pragma: no cover - 循环内所有路径均已返回
