"""zlib_security：IP 黑名单与 URL 公网校验（含 DNS 解析结果校验、onion 开关）。

传输层的逐跳重定向校验与对端核验见 test_transport.py。
"""

import pytest

from astrbot_plugin_zlibrary_assistant import zlib_security
from astrbot_plugin_zlibrary_assistant.zlib_security import (
    is_private_ip,
    is_safe_public_url,
)


class TestIsPrivateIp:
    """IP 黑名单边界"""

    @pytest.mark.parametrize(
        "ip",
        [
            "192.168.1.1",  # 内网
            "10.0.0.1",  # 内网
            "172.16.0.1",  # 内网
            "127.0.0.1",  # 环回
            "169.254.169.254",  # 链路本地 / 云元数据
            "224.0.0.1",  # 多播
            "240.0.0.1",  # 保留
            "0.0.0.0",  # 未指定
            "::1",  # IPv6 环回
            "fe80::1",  # IPv6 链路本地
            "not-an-ip",  # 非法 IP 保守拒绝
        ],
    )
    def test_blocked(self, ip):
        assert is_private_ip(ip) is True

    @pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2606:4700::1111"])
    def test_public_allowed(self, ip):
        assert is_private_ip(ip) is False


class TestIsSafePublicUrl:
    """URL 校验：协议 + 主机名黑名单 + IP 字面量 + DNS 二次校验"""

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "ftp://example.com/a.jpg",
            "file:///etc/passwd",
            "http://localhost/a.jpg",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://myhost.internal/a.jpg",
            "http://127.0.0.1/a.jpg",
            "http://[::1]/a.jpg",
            "http://169.254.169.254/latest/meta-data/",
        ],
    )
    def test_rejected(self, url):
        ok, reason = is_safe_public_url(url, dns_check=False)
        assert ok is False
        assert reason

    def test_public_ip_literal_allowed(self):
        ok, reason = is_safe_public_url("https://8.8.8.8/a.jpg", dns_check=False)
        assert ok is True
        assert reason == ""

    def test_dns_resolves_public(self, monkeypatch):
        monkeypatch.setattr(
            zlib_security, "_resolve_host_ips", lambda host: ["93.184.216.34"]
        )
        ok, _ = is_safe_public_url("https://covers.example.com/a.jpg")
        assert ok is True

    def test_dns_resolves_private_rejected(self, monkeypatch):
        monkeypatch.setattr(
            zlib_security, "_resolve_host_ips", lambda host: ["192.168.0.1"]
        )
        ok, reason = is_safe_public_url("https://covers.example.com/a.jpg")
        assert ok is False
        assert "内网" in reason

    def test_dns_failure_conservative_reject(self, monkeypatch):
        monkeypatch.setattr(zlib_security, "_resolve_host_ips", lambda host: None)
        ok, reason = is_safe_public_url("https://covers.example.com/a.jpg")
        assert ok is False
        assert "保守拒绝" in reason

    def test_dns_check_disabled_skips_resolve(self, monkeypatch):
        def _boom(host):
            raise AssertionError("dns_check=False 不应触发解析")

        monkeypatch.setattr(zlib_security, "_resolve_host_ips", _boom)
        ok, _ = is_safe_public_url("https://covers.example.com/a.jpg", dns_check=False)
        assert ok is True

    def test_v3_onion_requires_explicit_enable(self):
        url = "http://" + "a" * 56 + ".onion/book"
        assert is_safe_public_url(url, dns_check=False)[0] is False
        assert is_safe_public_url(url, dns_check=False, allow_onion=True)[0] is True

    def test_invalid_onion_rejected_even_when_enabled(self):
        ok, reason = is_safe_public_url(
            "http://short.onion/book", dns_check=False, allow_onion=True
        )
        assert ok is False
        assert "v3" in reason
