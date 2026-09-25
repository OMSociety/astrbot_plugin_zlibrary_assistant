"""SSRF 纵深防护测试：IP 黑名单、DNS 二次校验、重定向逐跳校验。

背景：封面 URL 与下载直链来自 Z-Library API 响应（第三方内容），
此前直接请求且默认跟随重定向，公网 302 可把请求带去内网（SSRF 放大）。
现统一经 _guarded_get：不自动跟随重定向，每跳重新校验目标为公网地址。
"""

import asyncio

import aiohttp
import pytest
from astrbot_plugin_zlibrary_assistant import zlib_security
from astrbot_plugin_zlibrary_assistant.zlib_client import ZlibClient, ZlibError
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


class _FakeContent:
    def __init__(self, body: bytes):
        self._body = body

    async def read(self, n=-1):
        return self._body


class _FakeTransport:
    def __init__(self, peer):
        self._peer = peer

    def get_extra_info(self, name):
        return self._peer if name == "peername" else None


class _FakeConnection:
    def __init__(self, peer):
        self.transport = _FakeTransport(peer)


class _FakeResponse:
    def __init__(self, status=200, headers=None, body=b"", peer=("93.184.216.34", 443)):
        self.status = status
        self.headers = headers or {}
        self.content = _FakeContent(body)
        self.connection = _FakeConnection(peer)
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

    def test_rejects_private_peer(self, monkeypatch):
        """DNS 校验与实际连接分别解析：真实对端 IP 是内网即拒绝（TOCTOU/rebinding）"""
        routes = {
            "https://8.8.8.8/f.jpg": _FakeResponse(
                200, body=b"img", peer=("192.168.1.10", 443)
            )
        }
        client = _make_client(monkeypatch, _FakeSession(routes))
        resp, err = asyncio.run(
            client._guarded_get("https://8.8.8.8/f.jpg", timeout_total=8)
        )
        assert resp is None
        assert "对端" in err

    def test_released_connection_is_not_rejected(self, monkeypatch):
        """取不到对端时不得拒绝。

        aiohttp 在响应体读完后就释放连接（ClientResponse.connection 变 None），
        小响应（单 TCP 段）几乎必然命中；此时由上一层 URL 校验承担，判定退化为
        "拿到且是私网才拒绝"，否则直连部署的正常响应会被整体误杀。
        """
        resp_obj = _FakeResponse(200, body=b"img")
        resp_obj.connection = None
        client = _make_client(monkeypatch, _FakeSession({"https://8.8.8.8/f.jpg": resp_obj}))
        resp, err = asyncio.run(
            client._guarded_get("https://8.8.8.8/f.jpg", timeout_total=8)
        )
        assert err == ""
        assert resp is not None
        assert resp.status == 200

    def test_released_connection_uses_recorded_peer(self, monkeypatch):
        """连接已释放但有建连期记录：记录下来的是私网就必须拒绝"""
        resp_obj = _FakeResponse(200, body=b"img")
        resp_obj.connection = None
        client = _make_client(monkeypatch, _FakeSession({"https://8.8.8.8/f.jpg": resp_obj}))
        client._peers["8.8.8.8"] = "10.0.0.7"
        resp, err = asyncio.run(
            client._guarded_get("https://8.8.8.8/f.jpg", timeout_total=8)
        )
        assert resp is None
        assert "对端" in err

    def test_recorded_public_peer_passes(self, monkeypatch):
        resp_obj = _FakeResponse(200, body=b"img")
        resp_obj.connection = None
        client = _make_client(monkeypatch, _FakeSession({"https://8.8.8.8/f.jpg": resp_obj}))
        client._peers["8.8.8.8"] = "93.184.216.34"
        resp, err = asyncio.run(
            client._guarded_get("https://8.8.8.8/f.jpg", timeout_total=8)
        )
        assert err == ""
        assert resp.status == 200

    def test_loopback_peer_releases_response(self, monkeypatch):
        resp_obj = _FakeResponse(200, body=b"img", peer=("127.0.0.1", 80))
        client = _make_client(monkeypatch, _FakeSession({"https://8.8.8.8/f.jpg": resp_obj}))
        asyncio.run(client._guarded_get("https://8.8.8.8/f.jpg", timeout_total=8))
        assert resp_obj.released is True

    def test_peer_check_skipped_for_http_proxy(self, monkeypatch):
        """配了代理时 TCP 对端必然是代理本身，按内网拒绝会误杀所有正常请求"""
        routes = {
            "https://8.8.8.8/f.jpg": _FakeResponse(
                200, body=b"img", peer=("127.0.0.1", 7897)
            )
        }
        session = _FakeSession(routes)
        client = ZlibClient(
            accounts=[], domain="z-library.sk", proxy="http://127.0.0.1:7897"
        )

        async def fake_get_session():
            return session

        monkeypatch.setattr(client, "_get_session", fake_get_session)
        resp, err = asyncio.run(
            client._guarded_get("https://8.8.8.8/f.jpg", timeout_total=8)
        )
        assert err == ""
        assert resp.status == 200

    def test_peer_check_skipped_for_socks_onion(self, monkeypatch):
        """Tor 场景对端是本地 SOCKS 代理、目标由 onion 出口建立，无从核验"""
        onion_host = "a" * 56 + ".onion"
        onion_url = f"http://{onion_host}/cover.jpg"
        resp_obj = _FakeResponse(200, body=b"img", peer=("127.0.0.1", 9050))
        session = _FakeSession({onion_url: resp_obj})
        client = ZlibClient(
            accounts=[], domain=onion_host, proxy="socks5://127.0.0.1:9050"
        )

        async def fake_get_session():
            return session

        monkeypatch.setattr(client, "_get_session", fake_get_session)
        resp, err = asyncio.run(client._guarded_get(onion_url, timeout_total=8))
        assert err == ""
        assert resp.status == 200

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
        # 多台公网主机互相重定向，超过守链跳数上限
        from astrbot_plugin_zlibrary_assistant.zlib_client import (
            _MAX_GUARDED_REDIRECT_HOPS,
        )

        routes = {
            f"https://8.8.8.{i}/jump": _FakeResponse(
                302, headers={"Location": f"https://8.8.8.{i + 1}/jump"}
            )
            for i in range(_MAX_GUARDED_REDIRECT_HOPS + 1)
        }
        session = _FakeSession(routes)
        client = _make_client(monkeypatch, session)
        resp, err = asyncio.run(
            client._guarded_get("https://8.8.8.0/jump", timeout_total=8)
        )
        assert resp is None
        assert f"重定向超过 {_MAX_GUARDED_REDIRECT_HOPS} 跳" in err
        # 1 次初始请求 + _MAX_GUARDED_REDIRECT_HOPS 跳
        assert len(session.requested) == _MAX_GUARDED_REDIRECT_HOPS + 1

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
