"""URL 安全校验：SSRF 纵深防护（参照 reverse_searcher 的 utils/security.py）。

Z-Library 的封面 CDN 地址与下载直链均来自 API 响应（第三方内容），
请求前必须校验为公网 http/https 地址，或显式启用的 v3 onion 地址：内网 / 环回 / 链路本地 / 云元数据 /
保留 / 多播 IP 全部拒绝；域名做 DNS 解析二次校验（防 rebinding）；
重定向不自动跟随，由调用方逐跳校验（见 zlib_client._guarded_get）。

本模块只做 URL 校验：插件无本地文件读取路径，不需要 security.py 的
本地路径白名单部分。
"""

from __future__ import annotations

import ipaddress
import socket
import time
from urllib.parse import urlparse

# DNS 二次校验总开关：封面 CDN / 下载域名固定，若部署环境本地 DNS 解析
# 异常导致封面或下载被整体误拒，可将此开关置 False，仅保留 IP 黑名单校验。
DNS_REBINDING_CHECK = True

# 拒绝的本地主机名（不区分大小写）
_BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "broadcasthost",
}

# 拒绝的主机名后缀（如 myhost.local、router.localhost）
_BLOCKED_HOST_SUFFIXES = (".local", ".localhost", ".internal", ".home.arpa")

# 常见云元数据主机名（除 IP 段外的主机形式，如 GCP 的 metadata.google.internal）
_BLOCKED_METADATA_HOSTNAMES = {
    "metadata.google.internal",
    "metadata.azure.internal",
    "instance-data",
    "instance-data.ec2.internal",
    "169.254.169.254.nip.io",
}


def is_private_ip(ip: str) -> bool:
    """判断 IP 是否属于内网 / 保留 / 元数据等不可信范围。

    无法解析为合法 IP 时保守按不可信处理。
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


# 进程级 DNS 结果缓存：host -> (IP 列表, 时间戳)。
# 本函数经 asyncio.to_thread 调用，可能多线程并发；dict 读写受 GIL 保护，
# 偶发并发下的重复解析无害（最坏多解析一次）。
_DNS_CACHE: dict[str, tuple[list[str], float]] = {}
_DNS_CACHE_TTL = 300.0  # 5 分钟


def _resolve_host_ips(host: str) -> list[str] | None:
    """带缓存的域名解析，返回 IP 列表；解析失败返回 None（调用方保守拒绝）。"""
    now = time.monotonic()
    cached = _DNS_CACHE.get(host)
    if cached and now - cached[1] < _DNS_CACHE_TTL:
        return cached[0]
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return None
    ips = [info[4][0] for info in infos]
    # 保底防膨胀：正常流量远达不到该量级；超过即整体清空（条目本就有 TTL）
    if len(_DNS_CACHE) >= 512:
        _DNS_CACHE.clear()
    _DNS_CACHE[host] = (ips, now)
    return ips


def is_safe_public_url(
    url: str, *, dns_check: bool = True, allow_onion: bool = False
) -> tuple[bool, str]:
    """校验 URL 是否为可安全请求的公网 http/https 地址。

    返回 (是否安全, 拒绝原因)；拒绝原因仅用于日志，通过时为空串。

    Args:
        url: 待校验的 URL
        dns_check: 是否做 DNS 解析二次校验（测试用；生产以模块级
            DNS_REBINDING_CHECK 总开关为准）
    """
    if not url or not isinstance(url, str):
        return False, "URL 为空"
    url = url.strip()
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, "URL 无法解析"
    if parsed.scheme not in ("http", "https"):
        return False, "非 http/https 协议"
    host = parsed.hostname
    if not host:
        return False, "URL 缺少主机名"
    host_lower = host.lower()

    if host_lower.endswith(".onion"):
        label = host_lower[:-6]
        if (
            allow_onion
            and len(label) == 56
            and all(c in "abcdefghijklmnopqrstuvwxyz234567" for c in label)
        ):
            return True, ""
        return False, "onion 地址未启用或格式不是 v3"

    if host_lower in _BLOCKED_HOSTNAMES or host_lower in _BLOCKED_METADATA_HOSTNAMES:
        return False, "主机名在黑名单"
    if host_lower.endswith(_BLOCKED_HOST_SUFFIXES):
        return False, "主机名后缀在黑名单"

    # 主机为 IP 字面量：直接校验
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass  # 是域名，继续 DNS 解析校验
    else:
        if is_private_ip(host):
            return False, "IP 指向内网/保留地址"
        return True, ""

    # DNS 解析二次校验：任一结果指向内网即拒绝（防 DNS rebinding）。
    # getaddrinfo 是阻塞调用，调用方应用 asyncio.to_thread 包装本函数。
    if not (dns_check and DNS_REBINDING_CHECK):
        return True, ""
    ips = _resolve_host_ips(host)
    if ips is None:
        return False, "域名解析失败，保守拒绝"
    for ip in ips:
        if is_private_ip(ip):
            return False, "域名解析到内网地址"
    return True, ""
