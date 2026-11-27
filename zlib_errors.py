"""统一异常与错误分类。

category 取值：
    network_error       连接失败 / 超时 / 代理错误
    tls_error           证书或握手失败
    diamwall_blocked    DiamWall 浏览器验证页（纯 HTTP 客户端无法通过）
    cloudflare_blocked  Cloudflare 人机验证页
    rate_limited        出口 IP 被限流
    domain_invalid      域名失效（404 / 5xx / 返回非 JSON）
    all_domains_failed  所有候选镜像域名依次尝试后均失败
    auth_failed         账号凭据无效或登录失效
    quota_exhausted     账号池今日下载额度用尽
    api_error           接口返回的其他业务错误
"""

from __future__ import annotations

# 换一个镜像域名重试有意义的类别
DOMAIN_FAILOVER_CATEGORIES = frozenset(
    {
        "network_error",
        "tls_error",
        "diamwall_blocked",
        "cloudflare_blocked",
        "domain_invalid",
        "rate_limited",
    }
)


class ZlibError(Exception):
    """Z-Library 访问统一异常。

    message 面向用户（可直接回显）；detail 只进日志与诊断，可能含响应片段。
    """

    def __init__(self, category: str, message: str, detail: str = ""):
        super().__init__(message)
        self.category = category
        self.message = message
        self.detail = detail

    def __str__(self) -> str:
        return f"[{self.category}] {self.message}"
