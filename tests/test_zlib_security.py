"""SSRF 纵深防护测试：IP 黑名单、DNS 二次校验、重定向逐跳校验。

背景：封面 URL 与下载直链来自 Z-Library API 响应（第三方内容），
此前直接请求且默认跟随重定向，公网 302 可把请求带去内网（SSRF 放大）。
现统一经 _guarded_get：不自动跟随重定向，每跳重新校验目标为公网地址。
"""

import asyncio

import aiohttp
import pytest

import astrbot_plugin_zlibrary_assistant.zlib_security as zlib_security
from astrbot_plugin_zlibrary_assistant.zlib_client import ZlibError, ZlibClient
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


class _FakeContent:
    def __init__(self, body: bytes):
        self._body = body

    async def read(self, n=-1):
        return self._body


class _FakeResponse:
    def __init__(self, status=200, headers=None, body=b""):
        self.status = status
        self.headers = headers or {}
        self.content = _FakeContent(body)
        self.released = False

    async def release(self):
        self.released = True

    async def read(self):
        return self.content._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.released = True
        return False


class _FakeSession:
    """按 URL 路由 canned 响应；记录请求顺序供跳数断言。"""

    def __init__(self, routes: dict):
        self._routes = routes
        self.requested: list[str] = []

    async def get(self, url, **kwargs):
        assert kwargs.get("allow_redirects") is False
        self.requested.append(url)
        return self._routes[url]


def _make_client(monkeypatch, session: _FakeSession) -> ZlibClient:
    client = ZlibClient(accounts=[], domain="z-library.sk")

    async def fake_get_session():
        return session

    monkeypatch.setattr(client, "_get_session", fake_get_session)
    return client


class TestGuardedGet:
    """重定向逐跳校验"""

    @pytest.fixture(autouse=True)
    def _no_dns(self, monkeypatch):
        # 测试内所有域名都预置解析结果，避免真实 DNS 请求
        monkeypatch.setattr(
            zlib_security, "_resolve_host_ips", lambda host: ["93.184.216.34"]
        )

    def test_direct_200(self, monkeypatch):
        routes = {"https://8.8.8.8/f.jpg": _FakeResponse(200, body=b"img")}
        client = _make_client(monkeypatch, _FakeSession(routes))
        resp, err = asyncio.run(
            client._guarded_get("https://8.8.8.8/f.jpg", timeout_total=8)
        )
        assert err == ""
        assert resp.status == 200
        assert resp.released is False  # 交给调用方关闭

    def test_follows_redirect_to_public(self, monkeypatch):
        routes = {
            "https://8.8.8.8/f.jpg": _FakeResponse(
                302, headers={"Location": "https://8.8.4.4/real.jpg"}
            ),
            "https://8.8.4.4/real.jpg": _FakeResponse(200, body=b"img"),
        }
        session = _FakeSession(routes)
        client = _make_client(monkeypatch, session)
        resp, err = asyncio.run(
            client._guarded_get("https://8.8.8.8/f.jpg", timeout_total=8)
        )
        assert err == ""
        assert resp.status == 200
        assert session.requested == [
            "https://8.8.8.8/f.jpg",
            "https://8.8.4.4/real.jpg",
        ]

    def test_rejects_redirect_to_private_ip(self, monkeypatch):
        routes = {
            "https://8.8.8.8/f.jpg": _FakeResponse(
                302, headers={"Location": "http://127.0.0.1/admin"}
            ),
        }
        client = _make_client(monkeypatch, _FakeSession(routes))
        resp, err = asyncio.run(
            client._guarded_get("https://8.8.8.8/f.jpg", timeout_total=8)
        )
        assert resp is None
        assert "安全校验" in err
        assert "内网" in err

    def test_rejects_relative_redirect_to_private(self, monkeypatch):
        routes = {
            "https://8.8.8.8/f.jpg": _FakeResponse(
                302, headers={"Location": "http://10.0.0.5/x"}
            ),
        }
        client = _make_client(monkeypatch, _FakeSession(routes))
        resp, err = asyncio.run(
            client._guarded_get("https://8.8.8.8/f.jpg", timeout_total=8)
        )
        assert resp is None
        assert "安全校验" in err

    def test_exceeds_max_hops(self, monkeypatch):
        # 4 台公网主机互相重定向，超过 3 跳上限
        routes = {
            f"https://8.8.8.{i}/jump": _FakeResponse(
                302, headers={"Location": f"https://8.8.8.{i + 1}/jump"}
            )
            for i in range(4)
        }
        session = _FakeSession(routes)
        client = _make_client(monkeypatch, session)
        resp, err = asyncio.run(
            client._guarded_get("https://8.8.8.0/jump", timeout_total=8)
        )
        assert resp is None
        assert "重定向超过 3 跳" in err
        assert len(session.requested) == 4  # 1 次初始请求 + 3 跳

    def test_redirect_missing_location(self, monkeypatch):
        routes = {"https://8.8.8.8/f.jpg": _FakeResponse(302)}
        client = _make_client(monkeypatch, _FakeSession(routes))
        resp, err = asyncio.run(
            client._guarded_get("https://8.8.8.8/f.jpg", timeout_total=8)
        )
        assert resp is None
        assert "Location" in err

    def test_initial_url_rejected(self, monkeypatch):
        client = _make_client(monkeypatch, _FakeSession({}))
        resp, err = asyncio.run(
            client._guarded_get("http://127.0.0.1/x", timeout_total=8)
        )
        assert resp is None
        assert "安全校验" in err

    def test_client_error_returned_as_error(self, monkeypatch):
        class _FailingSession:
            async def get(self, url, **kwargs):
                raise aiohttp.ClientConnectionError("boom")

        client = _make_client(monkeypatch, _FailingSession())
        resp, err = asyncio.run(
            client._guarded_get("https://8.8.8.8/f.jpg", timeout_total=8)
        )
        assert resp is None
        assert "boom" in err


class TestClientEntryGuards:
    """封面与下载入口的 SSRF 拒绝"""

    def test_fetch_cover_rejects_private_url(self, monkeypatch):
        client = _make_client(monkeypatch, _FakeSession({}))
        uri, err = asyncio.run(client.fetch_cover_base64("http://127.0.0.1/cover.jpg"))
        assert uri == ""
        assert "安全校验" in err

    def test_fetch_cover_rejects_non_http(self):
        client = ZlibClient(accounts=[], domain="z-library.sk")
        uri, err = asyncio.run(client.fetch_cover_base64("ftp://example.com/cover.jpg"))
        assert uri == ""
        assert err == "无效 URL"

    def test_download_rejects_private_direct_link(self, monkeypatch):
        from astrbot_plugin_zlibrary_assistant.zlib_client import Account

        client = _make_client(monkeypatch, _FakeSession({}))
        monkeypatch.setattr(
            client, "pick_download_account", lambda: Account(name="a", logged_in=True)
        )

        async def fake_download_link(account, book):
            return "http://127.0.0.1/book.pdf", "book.pdf", "pdf"

        monkeypatch.setattr(client, "get_download_link", fake_download_link)
        with pytest.raises(ZlibError) as exc_info:
            asyncio.run(client.download({"id": 1, "hash": "abc"}))
        assert exc_info.value.category == "network_error"
        assert "安全校验" in exc_info.value.detail
