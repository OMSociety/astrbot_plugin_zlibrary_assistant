"""zlib_transport：响应分类、同源判定、Cookie 合并、受限读取、request_json 与 guarded_get。

安全不变量：
- 跳转只允许回到同一源，带账号 cookies 的请求不会被送去其他主机；
- 响应体读取有硬上限，读取方式是循环读满而非单次 read(n)；
- 封面与下载直链的每一跳都做 SSRF 校验，直连时再核验真实对端 IP，配代理时跳过对端核验；
- 不关闭 TLS 校验。
"""

from __future__ import annotations

import asyncio

import aiohttp
import pytest
from aiohttp import web
from aiohttp_socks import ProxyConnector
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


# ---------- classify_body ----------


class TestClassifyBody:
    """一次 E-API 响应归类为 JSON 或基础设施错误。"""

    def test_success_json_returned(self):
        payload, err = classify_body(200, "", '{"success": 1, "books": []}')
        assert err is None
        assert payload == {"success": 1, "books": []}

    def test_success_json_mojibake_repaired(self):
        double_encoded = "Frédéric".encode().decode("latin-1")
        payload, err = classify_body(200, "", '{"author": "%s"}' % double_encoded)
        assert err is None
        assert payload["author"] == "Frédéric"

    @pytest.mark.parametrize("status", [400, 401, 403, 404])
    def test_business_error_json_passes_through_regardless_of_status(self, status):
        """业务错误（如未登录）也是合法 JSON，原样交给上层按字段判断。"""
        payload, err = classify_body(
            status, "", '{"success": 0, "error": "Please login"}'
        )
        assert err is None
        assert payload["error"] == "Please login"

    def test_incorrect_password_json_passes_through(self):
        payload, err = classify_body(
            200, "", '{"success": 0, "error": "Incorrect email or password"}'
        )
        assert err is None
        assert payload["success"] == 0

    @pytest.mark.parametrize("status", [513, 403])
    def test_diamwall_by_server_header(self, status):
        payload, err = classify_body(status, "DiamWall", "<html>blocked</html>")
        assert payload is None
        assert err.category == "diamwall_blocked"

    @pytest.mark.parametrize(
        "body",
        [
            "<html><body>DiamWall protection</body></html>",
            "<html><title>Verifying your browser</title></html>",
        ],
    )
    def test_diamwall_by_body(self, body):
        _, err = classify_body(403, "nginx", body)
        assert err.category == "diamwall_blocked"

    @pytest.mark.parametrize(
        ("status", "server", "body"),
        [
            (403, "cloudflare", "<html><title>Just a moment...</title></html>"),
            (200, "nginx", "<html>Checking your browser before accessing</html>"),
            (403, "nginx", '<html><div id="cf-challenge"></div></html>'),
            (503, "cloudflare", "<html>error</html>"),
        ],
    )
    def test_cloudflare_challenge(self, status, server, body):
        _, err = classify_body(status, server, body)
        assert err.category == "cloudflare_blocked"

    @pytest.mark.parametrize(
        ("status", "text"),
        [
            (429, "slow down"),
            (200, "Too many requests from your IP"),
            (403, "blocked #ipd3"),
            (429, '{"success": 0}'),
        ],
    )
    def test_rate_limited(self, status, text):
        """限流判定先于 JSON 解析：429 即使带 JSON 体也按限流处理。"""
        payload, err = classify_body(status, "", text)
        assert payload is None
        assert err.category == "rate_limited"

    def test_business_err_number_is_not_rate_limited(self):
        """E-API 的业务错误以 "Err #N" 开头，不能误判成限流。"""
        _, err = classify_body(200, "", "Err #5 file not found")
        assert err.category == "domain_invalid"

    @pytest.mark.parametrize("status", [200, 404, 502])
    def test_html_instead_of_json_is_domain_invalid(self, status):
        _, err = classify_body(status, "nginx", "<html><body>hello</body></html>")
        assert err.category == "domain_invalid"
        assert str(status) in err.message

    def test_json_non_object_is_domain_invalid(self):
        _, err = classify_body(200, "", "[1, 2, 3]")
        assert err.category == "domain_invalid"

    def test_detail_truncated(self):
        _, err = classify_body(502, "", "x" * 1000)
        assert len(err.detail) == 200


# ---------- 纯函数 ----------


class TestIsSameOrigin:
    @pytest.mark.parametrize(
        "target",
        [
            "https://z-lib.example/path",
            "https://Z-LIB.EXAMPLE/path",
            "https://z-lib.example:443/path",
        ],
    )
    def test_same_origin(self, target):
        assert is_same_origin(target, BASE) is True

    @pytest.mark.parametrize(
        "target",
        [
            "https://evil.example/path",
            "http://z-lib.example/path",
            "https://z-lib.example:8443/path",
            "https://z-lib.example.evil.example/path",
            "https://sub.z-lib.example/path",
            "ftp://z-lib.example/path",
            "//evil.example/path",
        ],
    )
    def test_not_same_origin(self, target):
        assert is_same_origin(target, BASE) is False

    def test_explicit_default_port_equals_omitted(self):
        assert is_same_origin("http://a.example:80/x", "http://a.example") is True

    def test_onion_host_matches_exactly(self):
        onion = "http://" + "a" * 56 + ".onion"
        assert is_same_origin(onion + "/x", onion) is True
        assert is_same_origin("http://" + "b" * 56 + ".onion/x", onion) is False


class TestMergeSetCookies:
    def test_only_first_pair_is_cookie(self):
        merged = merge_set_cookies({}, ["a=b; Priority=High; Path=/; Secure"])
        assert merged == {"a": "b"}

    def test_keeps_existing_and_overrides_same_name(self):
        merged = merge_set_cookies({"x": "1", "a": "old"}, ["a=new; Path=/"])
        assert merged == {"x": "1", "a": "new"}

    def test_does_not_mutate_input(self):
        original = {"x": "1"}
        merge_set_cookies(original, ["a=b"])
        assert original == {"x": "1"}

    def test_quoted_value_unquoted(self):
        assert merge_set_cookies({}, ['a="b c"']) == {"a": "b c"}

    @pytest.mark.parametrize(
        "header", ["", "novalue", "=v", "bad name=v", "a=", "a=;Path=/"]
    )
    def test_malformed_or_empty_ignored(self, header):
        assert merge_set_cookies({}, [header]) == {}

    def test_multiple_headers(self):
        merged = merge_set_cookies({}, ["a=1; Path=/", "b=2; HttpOnly"])
        assert merged == {"a": "1", "b": "2"}


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
        body = bytes(range(256)) * 40  # 10240 字节，分 100 字节一块
        resp = FakeResponse(body=body, chunk_size=100)
        assert await read_limited(resp, 1_000_000) == body
        assert resp.content.read_calls == 0

    @pytest.mark.asyncio
    async def test_over_limit_returns_limit_plus_one(self):
        resp = FakeResponse(body=b"x" * 5000, chunk_size=100)
        data = await read_limited(resp, 1000)
        assert len(data) == 1001

    @pytest.mark.asyncio
    async def test_exactly_at_limit_not_flagged(self):
        resp = FakeResponse(body=b"x" * 1000, chunk_size=64)
        assert len(await read_limited(resp, 1000)) == 1000

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

    @pytest.mark.asyncio
    async def test_empty_body(self):
        assert await read_limited(FakeResponse(body=b""), 100) == b""


# ---------- 构造与会话 ----------


class TestTransportSetup:
    def test_proxy_flags(self):
        assert ZlibTransport("").proxy is None
        assert ZlibTransport("").socks is False
        assert ZlibTransport("http://127.0.0.1:7890").socks is False
        assert ZlibTransport("socks5h://127.0.0.1:9050").socks is True
        assert ZlibTransport("SOCKS4://127.0.0.1:1080").socks is True

    def test_proxy_kwargs(self):
        assert ZlibTransport("").proxy_kwargs() == {}
        assert ZlibTransport("http://p:1").proxy_kwargs() == {"proxy": "http://p:1"}
        assert ZlibTransport("socks5://p:1").proxy_kwargs() == {}

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
        """响应体读完后连接即被释放，建连期记录是唯一可用的对端来源（仅回环本机，不出网）。"""

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


def _api_transport(session: FakeSession) -> ZlibTransport:
    transport = ZlibTransport()
    transport._session = session
    return transport


def _json(payload: str = '{"success": 1}', status: int = 200, headers=None):
    return FakeResponse(status, headers, payload)


class TestRequestJson:
    @pytest.mark.asyncio
    async def test_success_returns_status_and_payload(self):
        session = FakeSession({f"{BASE}/eapi/x": _json('{"success": 1, "a": 2}')})
        status, payload = await _api_transport(session).request_json(
            BASE, "GET", "/eapi/x"
        )
        assert (status, payload) == (200, {"success": 1, "a": 2})

    @pytest.mark.asyncio
    async def test_request_shape_redirects_off_tls_on_source_header(self):
        session = FakeSession({f"{BASE}/eapi/x": _json()})
        await _api_transport(session).request_json(
            BASE, "POST", "/eapi/x", data={"k": "v"}, cookies={"c": "1"}
        )
        call = session.calls[0]
        assert call["allow_redirects"] is False
        assert call["headers"]["source"] == "android"
        assert call["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
        assert call["data"] == {"k": "v"}
        assert call["cookies"] == {"c": "1"}
        # TLS 校验保持 aiohttp 默认：不传任何 ssl 相关覆盖
        assert not {"ssl", "verify_ssl", "fingerprint"} & call.keys()

    @pytest.mark.asyncio
    async def test_get_without_body_has_no_form_content_type(self):
        session = FakeSession({f"{BASE}/eapi/x": _json()})
        await _api_transport(session).request_json(BASE, "GET", "/eapi/x")
        assert "Content-Type" not in session.calls[0]["headers"]

    @pytest.mark.asyncio
    async def test_cookies_are_copied_not_shared(self):
        cookies = {"remix_userid": "1"}
        session = FakeSession(
            {
                f"{BASE}/a": [
                    _json("", 307, {"Location": f"{BASE}/a", "Set-Cookie": "g=1"}),
                    _json(),
                ]
            }
        )
        await _api_transport(session).request_json(BASE, "GET", "/a", cookies=cookies)
        assert cookies == {"remix_userid": "1"}

    @pytest.mark.asyncio
    async def test_body_decoded_as_utf8_without_charset_header(self):
        session = FakeSession({f"{BASE}/a": _json('{"title": "资本论"}')})
        _, payload = await _api_transport(session).request_json(BASE, "GET", "/a")
        assert payload["title"] == "资本论"

    @pytest.mark.asyncio
    async def test_same_origin_redirect_resends_with_set_cookie_and_body(self):
        """防护握手：自指 307 种下 cookie，带着 cookie 与原请求体重发才放行。"""
        session = FakeSession(
            {
                f"{BASE}/eapi/x": [
                    _json(
                        "",
                        307,
                        [
                            ("Location", f"{BASE}/eapi/x"),
                            ("Set-Cookie", "guard=tok; Path=/; Priority=High"),
                        ],
                    ),
                    _json('{"success": 1}'),
                ]
            }
        )
        status, _ = await _api_transport(session).request_json(
            BASE, "POST", "/eapi/x", data={"k": "v"}, cookies={"remix_userid": "9"}
        )
        assert status == 200
        assert len(session.calls) == 2
        assert session.calls[1]["cookies"] == {"remix_userid": "9", "guard": "tok"}
        assert session.calls[1]["data"] == {"k": "v"}
        assert session.calls[1]["allow_redirects"] is False

    @pytest.mark.asyncio
    async def test_redirect_cookies_collected_even_without_initial_cookies(self):
        session = FakeSession(
            {
                f"{BASE}/a": [
                    _json("", 302, {"Location": "/a", "Set-Cookie": "g=1"}),
                    _json(),
                ]
            }
        )
        await _api_transport(session).request_json(BASE, "GET", "/a")
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
    async def test_too_many_redirects_rejected(self):
        session = FakeSession({f"{BASE}/a": _json("", 307, {"Location": f"{BASE}/a"})})
        with pytest.raises(ZlibError) as exc:
            await _api_transport(session).request_json(BASE, "GET", "/a")
        assert exc.value.category == "domain_invalid"
        assert len(session.calls) == MAX_API_REDIRECT_HOPS + 1

    @pytest.mark.asyncio
    async def test_redirect_loop_on_diamwall_page_reported_as_diamwall(self):
        loop = FakeResponse(307, {"Location": f"{BASE}/a", "Server": "DiamWall"}, "")
        session = FakeSession({f"{BASE}/a": loop})
        with pytest.raises(ZlibError) as exc:
            await _api_transport(session).request_json(BASE, "GET", "/a")
        assert exc.value.category == "diamwall_blocked"

    @pytest.mark.asyncio
    async def test_redirect_without_location_rejected(self):
        session = FakeSession({f"{BASE}/a": _json("", 302)})
        with pytest.raises(ZlibError) as exc:
            await _api_transport(session).request_json(BASE, "GET", "/a")
        assert exc.value.category == "domain_invalid"
        assert "Location" in exc.value.message

    @pytest.mark.asyncio
    async def test_oversized_body_rejected(self, monkeypatch):
        monkeypatch.setattr(zlib_transport, "MAX_API_BYTES", 100)
        session = FakeSession(
            {f"{BASE}/a": FakeResponse(200, None, b"x" * 500, chunk_size=10)}
        )
        with pytest.raises(ZlibError) as exc:
            await _api_transport(session).request_json(BASE, "GET", "/a")
        assert exc.value.category == "domain_invalid"

    @pytest.mark.asyncio
    async def test_body_exactly_at_cap_accepted(self, monkeypatch):
        body = '{"success": 1}'
        monkeypatch.setattr(zlib_transport, "MAX_API_BYTES", len(body))
        session = FakeSession({f"{BASE}/a": _json(body)})
        status, _ = await _api_transport(session).request_json(BASE, "GET", "/a")
        assert status == 200

    @pytest.mark.asyncio
    async def test_non_json_page_raises_classified_error(self):
        session = FakeSession(
            {
                f"{BASE}/a": FakeResponse(
                    513, {"Server": "DiamWall"}, "<html>blocked</html>"
                )
            }
        )
        with pytest.raises(ZlibError) as exc:
            await _api_transport(session).request_json(BASE, "GET", "/a")
        assert exc.value.category == "diamwall_blocked"

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
    async def test_http_proxy_passed_per_request(self):
        session = FakeSession({f"{BASE}/a": _json()})
        transport = ZlibTransport("http://127.0.0.1:7890")
        transport._session = session
        await transport.request_json(BASE, "GET", "/a")
        assert session.calls[0]["proxy"] == "http://127.0.0.1:7890"

    @pytest.mark.asyncio
    async def test_params_forwarded(self):
        session = FakeSession({f"{BASE}/a": _json()})
        await _api_transport(session).request_json(BASE, "GET", "/a", params={"q": "1"})
        assert session.calls[0]["params"] == {"q": "1"}


# ---------- guarded_get ----------


PUBLIC = "https://8.8.8.8/f.jpg"


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
    async def test_private_peer_rejected_and_released(self):
        """URL 校验与实际建连分别解析（TOCTOU / rebinding）：真实对端是内网即拒绝。"""
        bad = FakeResponse(200, body=b"img", peer=("192.168.1.10", 443))
        transport, _ = _guard_transport({PUBLIC: bad})
        resp, err = await transport.guarded_get(PUBLIC, timeout_total=8)
        assert resp is None
        assert "对端" in err
        assert bad.released is True

    @pytest.mark.asyncio
    async def test_loopback_peer_rejected(self):
        transport, _ = _guard_transport({PUBLIC: FakeResponse(peer=("127.0.0.1", 80))})
        resp, err = await transport.guarded_get(PUBLIC, timeout_total=8)
        assert resp is None
        assert "对端" in err

    @pytest.mark.asyncio
    async def test_released_connection_without_record_not_rejected(self):
        """响应体读完 aiohttp 就释放连接：取不到对端是常态，不得据此拒绝。"""
        transport, _ = _guard_transport({PUBLIC: FakeResponse(peer=None)})
        resp, err = await transport.guarded_get(PUBLIC, timeout_total=8)
        assert err == ""
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_released_connection_uses_recorded_private_peer(self):
        transport, _ = _guard_transport({PUBLIC: FakeResponse(peer=None)})
        transport._peers["8.8.8.8"] = "10.0.0.7"
        resp, err = await transport.guarded_get(PUBLIC, timeout_total=8)
        assert resp is None
        assert "对端" in err

    @pytest.mark.asyncio
    async def test_released_connection_with_recorded_public_peer_passes(self):
        transport, _ = _guard_transport({PUBLIC: FakeResponse(peer=None)})
        transport._peers["8.8.8.8"] = "93.184.216.34"
        resp, err = await transport.guarded_get(PUBLIC, timeout_total=8)
        assert err == ""
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_live_connection_takes_precedence_over_recorded_peer(self):
        transport, _ = _guard_transport(
            {PUBLIC: FakeResponse(peer=("93.184.216.34", 443))}
        )
        transport._peers["8.8.8.8"] = "10.0.0.7"
        resp, err = await transport.guarded_get(PUBLIC, timeout_total=8)
        assert err == ""
        assert resp is not None

    @pytest.mark.asyncio
    async def test_peer_check_skipped_with_http_proxy(self):
        """配了代理时 TCP 对端必然是代理本身，按内网拒绝会误杀所有正常请求。"""
        transport, session = _guard_transport(
            {PUBLIC: FakeResponse(peer=("127.0.0.1", 7897))},
            proxy="http://127.0.0.1:7897",
        )
        resp, err = await transport.guarded_get(PUBLIC, timeout_total=8)
        assert err == ""
        assert resp.status == 200
        assert session.calls[0]["proxy"] == "http://127.0.0.1:7897"

    @pytest.mark.asyncio
    async def test_peer_check_skipped_with_socks_onion(self):
        onion_url = f"http://{'a' * 56}.onion/cover.jpg"
        transport, session = _guard_transport(
            {onion_url: FakeResponse(peer=("127.0.0.1", 9050))},
            proxy="socks5://127.0.0.1:9050",
        )
        resp, err = await transport.guarded_get(onion_url, timeout_total=8)
        assert err == ""
        assert resp.status == 200
        assert "proxy" not in session.calls[0]

    @pytest.mark.asyncio
    async def test_onion_url_rejected_without_socks_proxy(self):
        onion_url = f"http://{'a' * 56}.onion/cover.jpg"
        transport, session = _guard_transport({})
        resp, err = await transport.guarded_get(onion_url, timeout_total=8)
        assert resp is None
        assert "安全校验" in err
        assert session.calls == []

    @pytest.mark.asyncio
    async def test_follows_public_redirect_and_reverifies_each_hop(self):
        first = FakeResponse(302, {"Location": "https://8.8.4.4/real.jpg"})
        transport, session = _guard_transport(
            {PUBLIC: first, "https://8.8.4.4/real.jpg": FakeResponse(200, body=b"img")}
        )
        resp, err = await transport.guarded_get(PUBLIC, timeout_total=8)
        assert err == ""
        assert resp.status == 200
        assert session.urls == [PUBLIC, "https://8.8.4.4/real.jpg"]
        assert first.released is True

    @pytest.mark.asyncio
    async def test_relative_redirect_resolved(self):
        transport, session = _guard_transport(
            {
                PUBLIC: FakeResponse(301, {"Location": "/other.jpg"}),
                "https://8.8.8.8/other.jpg": FakeResponse(200),
            }
        )
        resp, _ = await transport.guarded_get(PUBLIC, timeout_total=8)
        assert resp.status == 200
        assert session.urls[-1] == "https://8.8.8.8/other.jpg"

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
    async def test_client_error_returned_as_message(self):
        transport, _ = _guard_transport({PUBLIC: aiohttp.ClientConnectionError("boom")})
        resp, err = await transport.guarded_get(PUBLIC, timeout_total=8)
        assert resp is None
        assert "boom" in err

    @pytest.mark.asyncio
    async def test_timeout_returned_as_network_message(self):
        transport, _ = _guard_transport({PUBLIC: asyncio.TimeoutError()})
        resp, err = await transport.guarded_get(PUBLIC, timeout_total=8)
        assert resp is None
        assert "网络异常" in err
