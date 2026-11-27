"""zlib_transport：响应分类、同源判定、Cookie 合并、受限读取、request_json 与 guarded_get。

安全不变量（本文件是它们的唯一覆盖点）：
- 跳转只允许回到同一源，带账号 cookies 的请求不会被送去其他主机；
- 响应体读取有硬上限，读取方式是循环读满而非单次 read(n)；
- 直链的每一跳都做 URL 校验，直连时再核验真实对端 IP，配代理时跳过对端核验；
- 不关闭 TLS 校验。
"""

from __future__ import annotations

import asyncio

import aiohttp
import pytest
from stubs import FakeResponse, FakeSession

from astrbot_plugin_zlibrary_assistant import zlib_security, zlib_transport
from astrbot_plugin_zlibrary_assistant.zlib_errors import ZlibError
from astrbot_plugin_zlibrary_assistant.zlib_transport import (
    MAX_API_REDIRECT_HOPS,
    MAX_GUARDED_REDIRECT_HOPS,
    ZlibTransport,
    classify_body,
    is_same_origin,
    merge_set_cookies,
    normalize_socks_proxy_url,
    read_limited,
)

BASE = "https://z-lib.example"


# ---------- 响应分类 ----------


class TestClassifyBody:
    """一次 E-API 响应归类为 JSON 或基础设施错误。

    is_safe_public_url / is_private_ip 的逐条黑白名单见 test_zlib_security.py，
    这里只验分类边界与优先级。
    """

    def test_json_dict_returned_and_mojibake_repaired(self):
        payload, err = classify_body(
            200, "", '{"author": "%s"}' % "Frédéric".encode().decode("latin-1")
        )
        assert err is None
        assert payload["author"] == "Frédéric"

    @pytest.mark.parametrize(
        ("status", "server", "text", "category"),
        [
            # 业务错误是合法 JSON，必须原样交给上层按字段判断，不能当基础设施错误
            (401, "", '{"success": 0, "error": "Please login"}', None),
            (200, "", '{"success": 0, "error": "Incorrect email or password"}', None),
            # 有限流文案或 429 即限流，优先于 JSON 解析
            (200, "", "Too many requests from your IP", "rate_limited"),
            (403, "", "blocked #ipd3", "rate_limited"),
            (429, "", '{"success": 0}', "rate_limited"),
            # DiamWall：Server 头或页面特征都算
            (513, "DiamWall", "<html>blocked</html>", "diamwall_blocked"),
            (403, "nginx", "<html>Verifying your browser</html>", "diamwall_blocked"),
            # Cloudflare：文案优先，其次才是 cloudflare + 403/503 + html 的组合判据
            (200, "nginx", "<html>Checking your browser</html>", "cloudflare_blocked"),
            (
                403,
                "nginx",
                '<html><div id="cf-challenge"></div></html>',
                "cloudflare_blocked",
            ),
            (
                403,
                "cloudflare",
                "<html><title>Just a moment...</title></html>",
                "cloudflare_blocked",
            ),
            (503, "cloudflare", "<html>error</html>", "cloudflare_blocked"),
            # 业务错误以 "Err #N" 开头，不能加宽成限流
            (200, "", "Err #5 file not found", "domain_invalid"),
            (502, "nginx", "<html>hello</html>", "domain_invalid"),
            (200, "", "[1, 2, 3]", "domain_invalid"),
        ],
    )
    def test_categories(self, status, server, text, category):
        payload, err = classify_body(status, server, text)
        if category is None:
            assert err is None
            assert payload["success"] == 0
        else:
            assert (payload, err.category) == (None, category)

    def test_domain_invalid_reports_status_and_bounds_detail(self):
        _, err = classify_body(502, "", "x" * 1000)
        assert str(502) in err.message
        assert len(err.detail) == 200


# ---------- 纯函数 ----------


class TestIsSameOrigin:
    @pytest.mark.parametrize(
        ("target", "base", "same"),
        [
            ("https://z-lib.example/path", BASE, True),
            ("https://Z-LIB.EXAMPLE/path", BASE, True),
            ("https://z-lib.example:443/path", BASE, True),
            ("http://a.example:80/x", "http://a.example", True),
            # 跨主机 / 明文降级 / 换端口 / 后缀伪装 / 子域 / 非 http(s) 全部不同源
            ("https://evil.example/path", BASE, False),
            ("http://z-lib.example/path", BASE, False),
            ("https://z-lib.example:8443/path", BASE, False),
            ("https://z-lib.example.evil.example/path", BASE, False),
            ("https://sub.z-lib.example/path", BASE, False),
            ("ftp://z-lib.example/path", BASE, False),
            ("//evil.example/path", BASE, False),
        ],
    )
    def test_origin_matrix(self, target, base, same):
        assert is_same_origin(target, base) is same

    def test_onion_host_matches_exactly(self):
        onion = "http://" + "a" * 56 + ".onion"
        assert is_same_origin(onion + "/x", onion) is True
        assert is_same_origin("http://" + "b" * 56 + ".onion/x", onion) is False


class TestMergeSetCookies:
    def test_only_first_pair_is_cookie_and_input_untouched(self):
        original = {"x": "1"}
        merged = merge_set_cookies(original, ["a=b; Priority=High; Path=/; Secure"])
        assert merged == {"x": "1", "a": "b"}
        assert original == {"x": "1"}

    def test_overrides_same_name_across_multiple_headers(self):
        merged = merge_set_cookies({"a": "old"}, ["a=new; Path=/", "b=2; HttpOnly"])
        assert merged == {"a": "new", "b": "2"}

    def test_quoted_value_unquoted(self):
        assert merge_set_cookies({}, ['a="b c"']) == {"a": "b c"}

    @pytest.mark.parametrize(
        "header", ["", "novalue", "=v", "bad name=v", "a=", "a=;Path=/"]
    )
    def test_malformed_or_empty_ignored(self, header):
        assert merge_set_cookies({}, [header]) == {}


class TestNormalizeSocksProxyUrl:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("socks5h://127.0.0.1:9050", "socks5://127.0.0.1:9050"),
            ("SOCKS5://127.0.0.1:9050", "socks5://127.0.0.1:9050"),
            ("socks4a://h:1080", "socks4://h:1080"),
            ("socks5://u:p@h:1080", "socks5://u:p@h:1080"),
        ],
    )
    def test_normalize(self, raw, expected):
        assert normalize_socks_proxy_url(raw) == expected


class TestReadLimited:
    """read_limited 必须循环读取：StreamReader.read(n) 每次只返回当前已到的一块。"""

    @pytest.mark.asyncio
    async def test_reads_across_many_chunks_until_complete(self):
        body = bytes(range(256)) * 40  # 10240 字节，100 字节一块
        resp = FakeResponse(body=body, chunk_size=100)
        assert await read_limited(resp, 1_000_000) == body
        assert resp.content.read_calls == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        (("body", "limit", "expected")),
        [
            (b"x" * 5000, 1000, 1001),
            (b"x" * 1000, 1000, 1000),
            (b"", 100, 0),
        ],
    )
    async def test_cap_boundaries(self, body, limit, expected):
        data = await read_limited(FakeResponse(body=body, chunk_size=64), limit)
        assert len(data) == expected

    @pytest.mark.asyncio
    async def test_stops_reading_after_limit_exceeded(self):
        consumed = []

        class _Content:
            async def iter_chunked(self, n):
                for i in range(1000):
                    consumed.append(i)
                    yield b"x" * 100

        class _Resp:
            content = _Content()

        data = await read_limited(_Resp(), 250)
        assert len(data) == 251
        assert len(consumed) == 3


# ---------- 构造与会话 ----------


class TestTransportSetup:
    @pytest.mark.parametrize(
        ("proxy", "is_socks", "kwargs"),
        [
            ("", False, {}),
            ("http://p:1", False, {"proxy": "http://p:1"}),
            ("socks5h://p:1", True, {}),
            ("SOCKS4://p:1", True, {}),
        ],
    )
    def test_proxy_assembly(self, proxy, is_socks, kwargs):
        transport = ZlibTransport(proxy)
        assert transport.proxy == (proxy or None)
        assert transport.socks is is_socks
        assert transport.proxy_kwargs() == kwargs

    @pytest.mark.asyncio
    async def test_direct_session_gets_peer_capture_and_is_reused(self):
        transport = ZlibTransport()
        try:
            session = await transport.get_session()
            assert session is await transport.get_session()
            assert session.connector._zl_peer_capture is True
        finally:
            await transport.close()

    @pytest.mark.asyncio
    async def test_socks_session_uses_proxy_connector_without_peer_capture(self):
        from aiohttp_socks import ProxyConnector

        transport = ZlibTransport("socks5h://127.0.0.1:9050")
        try:
            session = await transport.get_session()
            assert isinstance(session.connector, ProxyConnector)
            assert not getattr(session.connector, "_zl_peer_capture", False)
        finally:
            await transport.close()

    @pytest.mark.asyncio
    async def test_session_recreated_after_close(self):
        transport = ZlibTransport()
        first = await transport.get_session()
        await transport.close()
        second = await transport.get_session()
        try:
            assert first is not second
        finally:
            await transport.close()

    @pytest.mark.asyncio
    async def test_peer_recorded_at_connect_time_survives_released_connection(self):
        """对端记录靠包裹 connector 的私有建连方法实现：aiohttp 换实现就会静默失效，
        使 guarded_get 的对端核验退化成"永远取不到"而无人察觉。只连本机回环，不出网。
        """
        from aiohttp import web

        async def handler(_request):
            return web.Response(body=b"ok")

        app = web.Application()
        app.router.add_get("/", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        transport = ZlibTransport()
        url = f"http://127.0.0.1:{port}/"
        try:
            session = await transport.get_session()
            async with session.get(url) as resp:
                await resp.read()
            assert transport._peers["127.0.0.1"] == "127.0.0.1"
            assert transport.peer_for_get(resp, url) == "127.0.0.1"
        finally:
            await transport.close()
            await runner.cleanup()


# ---------- request_json ----------


class _TlsFailure(aiohttp.ClientSSLError):
    """绕过 aiohttp 需要 connection_key 的构造器，只保留异常类型。"""

    def __init__(self):
        Exception.__init__(self, "tls failure")

    def __str__(self):
        return "tls failure"


class _ConnectFailure(aiohttp.ClientConnectorError):
    def __init__(self):
        Exception.__init__(self, "connect failure")

    def __str__(self):
        return "connect failure"


def _api_transport(session: FakeSession, proxy: str = "") -> ZlibTransport:
    transport = ZlibTransport(proxy)
    transport._session = session
    return transport


def _json(payload: str = '{"success": 1}', status: int = 200, headers=None):
    return FakeResponse(status, headers, payload)


class TestRequestJson:
    @pytest.mark.asyncio
    async def test_success_returns_status_and_utf8_payload(self):
        session = FakeSession({f"{BASE}/a": _json('{"title": "资本论"}')})
        status, payload = await _api_transport(session).request_json(BASE, "GET", "/a")
        assert (status, payload["title"]) == (200, "资本论")

    @pytest.mark.asyncio
    async def test_request_shape_keeps_tls_verification(self):
        session = FakeSession({f"{BASE}/a": _json()})
        await _api_transport(session).request_json(
            BASE, "POST", "/a", data={"k": "v"}, cookies={"c": "1"}
        )
        call = session.calls[0]
        assert call["allow_redirects"] is False
        assert call["headers"]["source"] == "android"
        assert call["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
        assert (call["data"], call["cookies"]) == ({"k": "v"}, {"c": "1"})
        # TLS 校验保持 aiohttp 默认：不传任何 ssl 相关覆盖
        assert not {"ssl", "verify_ssl", "fingerprint"} & call.keys()

    @pytest.mark.asyncio
    async def test_get_without_body_has_no_form_content_type(self):
        session = FakeSession({f"{BASE}/a": _json()})
        await _api_transport(session).request_json(BASE, "GET", "/a")
        assert "Content-Type" not in session.calls[0]["headers"]

    @pytest.mark.asyncio
    async def test_same_origin_redirect_resends_cookie_without_mutating_input(
        self,
    ):
        """防护握手：自指 307 种下 cookie，带着 cookie 与原请求体重发才放行。"""
        cookies = {"remix_userid": "9"}
        session = FakeSession(
            {
                f"{BASE}/a": [
                    _json(
                        "",
                        307,
                        [
                            ("Location", f"{BASE}/a"),
                            ("Set-Cookie", "guard=tok; Path=/; Priority=High"),
                        ],
                    ),
                    _json(),
                ]
            }
        )
        status, _ = await _api_transport(session).request_json(
            BASE, "POST", "/a", data={"k": "v"}, cookies=cookies
        )
        assert status == 200
        assert cookies == {"remix_userid": "9"}
        retry = session.calls[1]
        assert retry["cookies"] == {"remix_userid": "9", "guard": "tok"}
        assert retry["data"] == {"k": "v"}
        assert retry["allow_redirects"] is False

    @pytest.mark.asyncio
    async def test_redirect_cookies_collected_without_initial_cookies(self):
        session = FakeSession(
            {
                f"{BASE}/a": [
                    _json("", 302, {"Location": "/a", "Set-Cookie": "g=1"}),
                    _json(),
                ]
            }
        )
        await _api_transport(session).request_json(BASE, "GET", "/a")
        assert session.urls == [f"{BASE}/a", f"{BASE}/a"]
        assert session.calls[1]["cookies"] == {"g": "1"}

    @pytest.mark.asyncio
    async def test_relative_redirect_resolved_against_current_url(self):
        session = FakeSession(
            {
                f"{BASE}/a": _json("", 302, {"Location": "/b"}),
                f"{BASE}/b": _json(),
            }
        )
        status, _ = await _api_transport(session).request_json(BASE, "GET", "/a")
        assert status == 200
        assert session.urls == [f"{BASE}/a", f"{BASE}/b"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "location",
        [
            "https://evil.example/steal",
            "http://z-lib.example/downgrade",
            "https://z-lib.example:8443/other-port",
            "https://z-lib.example.evil.example/",
        ],
    )
    async def test_cross_origin_redirect_rejected_without_second_request(
        self, location
    ):
        """跨主机/降级/换端口的跳转一律拒绝，且账号 cookies 不会被发出去。"""
        session = FakeSession(
            {f"{BASE}/a": _json("", 302, {"Location": location, "Set-Cookie": "g=1"})}
        )
        with pytest.raises(ZlibError) as exc:
            await _api_transport(session).request_json(
                BASE, "GET", "/a", cookies={"remix_userkey": "SECRET"}
            )
        assert exc.value.category == "domain_invalid"
        assert len(session.calls) == 1
        assert "SECRET" not in str(exc.value) + exc.value.detail

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("server", "category"),
        [("DiamWall", "diamwall_blocked"), ("nginx", "domain_invalid")],
    )
    async def test_redirect_hop_limit(self, server, category):
        """跳数上限：DiamWall 永久自指按防护页报，其它按域名无效报。"""
        loop = _json("", 307, {"Location": f"{BASE}/a", "Server": server})
        session = FakeSession({f"{BASE}/a": loop})
        with pytest.raises(ZlibError) as exc:
            await _api_transport(session).request_json(BASE, "GET", "/a")
        assert exc.value.category == category
        assert len(session.calls) == MAX_API_REDIRECT_HOPS + 1

    @pytest.mark.asyncio
    async def test_redirect_without_location_rejected(self):
        session = FakeSession({f"{BASE}/a": _json("", 302)})
        with pytest.raises(ZlibError) as exc:
            await _api_transport(session).request_json(BASE, "GET", "/a")
        assert exc.value.category == "domain_invalid"
        assert "Location" in exc.value.message

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("extra", "accepted"), [(0, True), (1, False)])
    async def test_body_cap_boundary(self, monkeypatch, extra, accepted):
        body = '{"success": 1}'
        monkeypatch.setattr(zlib_transport, "MAX_API_BYTES", len(body))
        session = FakeSession(
            {f"{BASE}/a": FakeResponse(200, body=body + " " * extra, chunk_size=4)}
        )
        if accepted:
            assert (await _api_transport(session).request_json(BASE, "GET", "/a"))[
                0
            ] == 200
        else:
            with pytest.raises(ZlibError) as exc:
                await _api_transport(session).request_json(BASE, "GET", "/a")
            assert exc.value.category == "domain_invalid"

    @pytest.mark.asyncio
    async def test_business_error_json_returned_not_raised(self):
        session = FakeSession(
            {f"{BASE}/a": _json('{"success": 0, "error": "Please login"}', status=403)}
        )
        status, payload = await _api_transport(session).request_json(BASE, "GET", "/a")
        assert status == 403
        assert payload["error"] == "Please login"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("exc", "category"),
        [
            (_TlsFailure(), "tls_error"),
            (_ConnectFailure(), "network_error"),
            (asyncio.TimeoutError(), "network_error"),
            (aiohttp.ServerTimeoutError("t"), "network_error"),
            (aiohttp.ClientPayloadError("p"), "network_error"),
        ],
    )
    async def test_transport_exceptions_classified(self, exc, category):
        session = FakeSession({f"{BASE}/a": exc})
        with pytest.raises(ZlibError) as info:
            await _api_transport(session).request_json(BASE, "GET", "/a")
        assert info.value.category == category

    @pytest.mark.asyncio
    async def test_proxy_and_params_forwarded_per_request(self):
        session = FakeSession({f"{BASE}/a": _json()})
        await _api_transport(session, "http://127.0.0.1:7890").request_json(
            BASE, "GET", "/a", params={"q": "1"}
        )
        call = session.calls[0]
        assert call["proxy"] == "http://127.0.0.1:7890"
        assert call["params"] == {"q": "1"}


# ---------- guarded_get ----------


PUBLIC = "https://8.8.8.8/f.jpg"
ONION = "http://" + "a" * 56 + ".onion/cover.jpg"


@pytest.fixture(autouse=False)
def no_dns(monkeypatch):
    """域名一律解析到固定公网地址，避免真实 DNS 请求。"""
    monkeypatch.setattr(
        zlib_security, "_resolve_host_ips", lambda host: ["93.184.216.34"]
    )


def _guard_transport(
    routes: dict, proxy: str = ""
) -> tuple[ZlibTransport, FakeSession]:
    session = FakeSession(routes)
    transport = ZlibTransport(proxy)
    transport._session = session
    return transport, session


class TestGuardedGet:
    pytestmark = pytest.mark.usefixtures("no_dns")

    @pytest.mark.asyncio
    async def test_direct_200_handed_to_caller_unreleased(self):
        transport, session = _guard_transport({PUBLIC: FakeResponse(200, body=b"img")})
        resp, err = await transport.guarded_get(
            PUBLIC, headers={"Referer": "r"}, cookies={"c": "1"}, timeout_total=8
        )
        assert err == ""
        assert resp.status == 200
        assert resp.released is False
        call = session.calls[0]
        assert call["allow_redirects"] is False
        assert call["headers"] == {"Referer": "r"}
        assert call["cookies"] == {"c": "1"}
        assert call["timeout"].total == 8
        assert not {"ssl", "verify_ssl"} & call.keys()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("peer", [("192.168.1.10", 443), ("127.0.0.1", 80)])
    async def test_private_or_loopback_peer_rejected_and_released(self, peer):
        """URL 校验与实际建连分别解析（TOCTOU / rebinding）：真实对端是内网即拒绝。"""
        bad = FakeResponse(200, body=b"img", peer=peer)
        transport, _ = _guard_transport({PUBLIC: bad})
        resp, err = await transport.guarded_get(PUBLIC, timeout_total=8)
        assert resp is None
        assert "对端" in err
        assert bad.released is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("peer", "record", "allowed"),
        [
            (None, None, True),  # 响应体读完连接已释放：取不到对端是常态，不得据此拒绝
            (None, "93.184.216.34", True),  # 退回建连期记录的公网对端
            (None, "10.0.0.7", False),  # 退回建连期记录的私网对端
            (("93.184.216.34", 443), "10.0.0.7", True),  # 活连接优先于记录
        ],
        ids=["no_peer", "recorded_public", "recorded_private", "live_wins"],
    )
    async def test_peer_source_precedence(self, peer, record, allowed):
        transport, _ = _guard_transport({PUBLIC: FakeResponse(peer=peer)})
        if record:
            transport._peers["8.8.8.8"] = record
        resp, err = await transport.guarded_get(PUBLIC, timeout_total=8)
        if allowed:
            assert (err, resp.status) == ("", 200)
        else:
            assert resp is None
            assert "对端" in err

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("proxy", "url", "peer", "forwarded"),
        [
            # 配了代理时 TCP 对端必然是代理本身（通常是 127.0.0.1），
            # 按内网拒绝会误杀所有正常请求，故跳过对端核验
            ("http://127.0.0.1:7897", PUBLIC, ("127.0.0.1", 7897), True),
            # SOCKS 由 connector 接管，不能按请求传 proxy 参数
            ("socks5://127.0.0.1:9050", ONION, ("127.0.0.1", 9050), False),
        ],
        ids=["http_proxy", "socks_onion"],
    )
    async def test_peer_check_skipped_with_proxy(self, proxy, url, peer, forwarded):
        transport, session = _guard_transport(
            {url: FakeResponse(peer=peer)}, proxy=proxy
        )
        resp, err = await transport.guarded_get(url, timeout_total=8)
        assert (err, resp.status) == ("", 200)
        assert ("proxy" in session.calls[0]) is forwarded

    @pytest.mark.asyncio
    async def test_onion_url_rejected_without_socks_proxy(self):
        transport, session = _guard_transport({})
        resp, err = await transport.guarded_get(ONION, timeout_total=8)
        assert resp is None
        assert "安全校验" in err
        assert session.calls == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("relative", [False, True], ids=["absolute", "relative"])
    async def test_follows_public_redirect_and_releases_each_hop(self, relative):
        target = "https://8.8.8.8/other.jpg"
        first = FakeResponse(302, {"Location": "/other.jpg" if relative else target})
        transport, session = _guard_transport(
            {PUBLIC: first, target: FakeResponse(200, body=b"img")}
        )
        resp, err = await transport.guarded_get(PUBLIC, timeout_total=8)
        assert (err, resp.status) == ("", 200)
        assert session.urls == [PUBLIC, target]
        assert first.released is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "location",
        [
            "http://127.0.0.1/admin",
            "http://10.0.0.5/x",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/x",
            "http://localhost/x",
            "file:///etc/passwd",
            "ftp://example.com/x",
        ],
    )
    async def test_redirect_to_internal_target_rejected_before_request(self, location):
        transport, session = _guard_transport(
            {PUBLIC: FakeResponse(302, {"Location": location})}
        )
        resp, err = await transport.guarded_get(PUBLIC, timeout_total=8)
        assert resp is None
        assert "安全校验" in err
        assert session.urls == [PUBLIC]

    @pytest.mark.asyncio
    async def test_redirect_to_host_resolving_private_rejected(self, monkeypatch):
        """域名在重定向这一跳解析到内网（DNS rebinding）同样被拒。"""
        monkeypatch.setattr(
            zlib_security,
            "_resolve_host_ips",
            lambda host: ["10.1.1.1"] if host == "evil.example" else ["93.184.216.34"],
        )
        transport, session = _guard_transport(
            {PUBLIC: FakeResponse(302, {"Location": "https://evil.example/x"})}
        )
        resp, err = await transport.guarded_get(PUBLIC, timeout_total=8)
        assert resp is None
        assert "内网" in err
        assert session.urls == [PUBLIC]

    @pytest.mark.asyncio
    async def test_redirect_hop_limit(self):
        hosts = [f"8.8.8.{i}" for i in range(MAX_GUARDED_REDIRECT_HOPS + 2)]
        routes = {
            f"https://{h}/j": FakeResponse(302, {"Location": f"https://{nxt}/j"})
            for h, nxt in zip(hosts, hosts[1:], strict=False)
        }
        transport, session = _guard_transport(routes)
        resp, err = await transport.guarded_get(
            f"https://{hosts[0]}/j", timeout_total=8
        )
        assert resp is None
        assert f"重定向超过 {MAX_GUARDED_REDIRECT_HOPS} 跳" in err
        assert len(session.calls) == MAX_GUARDED_REDIRECT_HOPS + 1

    @pytest.mark.asyncio
    async def test_redirect_without_location(self):
        bare = FakeResponse(302)
        transport, _ = _guard_transport({PUBLIC: bare})
        resp, err = await transport.guarded_get(PUBLIC, timeout_total=8)
        assert resp is None
        assert "Location" in err
        assert bare.released is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/x",
            "http://localhost/x",
            "file:///etc/passwd",
            "",
            "ftp://8.8.8.8/x",
        ],
    )
    async def test_initial_url_rejected_without_request(self, url):
        transport, session = _guard_transport({})
        resp, err = await transport.guarded_get(url, timeout_total=8)
        assert resp is None
        assert "安全校验" in err
        assert session.calls == []

    @pytest.mark.asyncio
    async def test_dns_resolving_private_rejected(self, monkeypatch):
        monkeypatch.setattr(
            zlib_security, "_resolve_host_ips", lambda host: ["192.168.0.9"]
        )
        transport, session = _guard_transport({})
        resp, err = await transport.guarded_get(
            "https://cdn.example/a.jpg", timeout_total=8
        )
        assert resp is None
        assert "内网" in err
        assert session.calls == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("exc", "needle"),
        [
            (aiohttp.ClientConnectionError("boom"), "boom"),
            (asyncio.TimeoutError(), "网络异常"),
        ],
    )
    async def test_client_errors_returned_as_message(self, exc, needle):
        transport, _ = _guard_transport({PUBLIC: exc})
        resp, err = await transport.guarded_get(PUBLIC, timeout_total=8)
        assert resp is None
        assert needle in err
