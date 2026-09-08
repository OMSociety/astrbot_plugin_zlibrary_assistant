"""zlib_client 内部逻辑测试：限流分类、缓存淘汰/浅拷贝、错误分类、额度文案。

网络环节（_request_json 的 HTTP 会话）用桩替代，不触网。
"""

import asyncio

import aiohttp
import pytest
from astrbot_plugin_zlibrary_assistant.zlib_client import (
    Account,
    ZlibClient,
    ZlibError,
    _is_cf_challenge,
    _is_rate_limited,
    _normalize_base_url,
    _normalize_socks_proxy_url,
)


def _make_client() -> ZlibClient:
    client = ZlibClient(accounts=[], domain="z-library.sk")
    client.book_cache.clear()
    return client


class TestBaseUrlAndProxy:
    def test_onion_defaults_to_http(self):
        host = "a" * 56 + ".onion"
        assert _normalize_base_url(host) == f"http://{host}"

    def test_clearnet_defaults_to_https(self):
        assert _normalize_base_url("z-library.sk/") == "https://z-library.sk"

    def test_onion_requires_socks(self):
        host = "a" * 56 + ".onion"
        with pytest.raises(ValueError, match="SOCKS"):
            ZlibClient(accounts=[], domain=host, proxy="http://127.0.0.1:7890")

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("SOCKS5H://tor:9050", "socks5://tor:9050"),
            ("Socks5://tor:9050", "socks5://tor:9050"),
            ("SOCKS4A://tor:9050", "socks4://tor:9050"),
        ],
    )
    def test_socks_scheme_normalization(self, raw, expected):
        assert _normalize_socks_proxy_url(raw) == expected


class _CtxResp:
    """_request_json 用法：async with session.request(...) as resp: await resp.text()"""

    def __init__(self, status=200, text="{}"):
        self.status = status
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def text(self, encoding=None, errors=None):
        return self._text


class _CtxSession:
    def __init__(self, responses):
        self._responses = responses  # list of _CtxResp（按调用次序出队）

    def request(self, method, url, data=None, **kwargs):
        # aiohttp 的 session.request 返回异步上下文管理器（不是协程）
        return self._responses.pop(0)


def _patch_session(monkeypatch, client: ZlibClient, session: _CtxSession):
    async def fake_get_session():
        return session

    monkeypatch.setattr(client, "_get_session", fake_get_session)


class TestRateLimitClassification:
    """限流判定边界：宽泛的 'Err #N' 是业务错误，不是限流"""

    @pytest.mark.parametrize(
        "text",
        [
            "Too many requests, please slow down",
            "Err #ipd3: your IP has been limited",
        ],
    )
    def test_rate_limited(self, text):
        assert _is_rate_limited(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "Err #102: file not found",
            "Err #205: invalid parameter",
            "",
            "normal response",
        ],
    )
    def test_not_rate_limited(self, text):
        assert _is_rate_limited(text) is False

    @pytest.mark.parametrize(
        "text",
        [
            "Checking your browser before accessing",
            "<html>cf-challenge</html>",
        ],
    )
    def test_cf_challenge(self, text):
        assert _is_cf_challenge(text) is True


class TestTrimBookCache:
    """缓存上限：dict 保序淘汰最旧条目"""

    def test_trim_evicts_oldest(self):
        client = _make_client()
        for i in range(502):
            client.book_cache[i] = {"id": i}
        client._trim_book_cache()
        assert len(client.book_cache) == 500
        assert 0 not in client.book_cache
        assert 1 not in client.book_cache
        assert 501 in client.book_cache

    def test_trim_noop_under_limit(self):
        client = _make_client()
        client.book_cache[1] = {"id": 1}
        client._trim_book_cache()
        assert 1 in client.book_cache


class TestSearchCacheShallowCopy:
    """搜索结果必须存浅拷贝：调用方就地写入 base64 封面不得污染缓存（A2-1 回归）"""

    def test_cover_write_does_not_pollute_cache(self, monkeypatch):
        client = _make_client()
        monkeypatch.setattr(client, "_save_book_cache", lambda: None)
        resp = _CtxResp(
            200,
            '{"success": true, "books": [{"id": 1, "hash": "abc", "title": "t"}]}',
        )
        _patch_session(monkeypatch, client, _CtxSession([resp]))

        books = asyncio.run(client.search("python"))
        books[0]["cover"] = "x" * 100_000  # 调用方 _attach_covers 的行为

        assert client.book_cache[1]["title"] == "t"
        assert "cover" not in client.book_cache[1]

    def test_cache_persisted_entries_survive(self, tmp_path, monkeypatch):
        """缓存写入磁盘后重启可读（id 转字符串存储）"""
        from astrbot_plugin_zlibrary_assistant import zlib_client as zc

        monkeypatch.setattr(zc, "get_astrbot_data_path", lambda: str(tmp_path))
        client = _make_client()
        client.book_cache[1] = {"id": 1, "title": "t"}
        client._save_book_cache()

        client2 = zc.ZlibClient(accounts=[], domain="z-library.sk")
        assert client2.book_cache[1]["title"] == "t"


class TestRequestJsonClassification:
    """_request_json 错误分类"""

    def _client(self):
        return _make_client()

    def test_404_domain_invalid(self, monkeypatch):
        client = self._client()
        _patch_session(monkeypatch, client, _CtxSession([_CtxResp(404, "not found")]))
        with pytest.raises(ZlibError) as ei:
            asyncio.run(client._request_json("GET", "/eapi/info"))
        assert ei.value.category == "domain_invalid"
        assert "404" in ei.value.message

    def test_5xx_domain_invalid(self, monkeypatch):
        client = self._client()
        _patch_session(monkeypatch, client, _CtxSession([_CtxResp(502, "bad gw")]))
        with pytest.raises(ZlibError) as ei:
            asyncio.run(client._request_json("GET", "/eapi/info"))
        assert ei.value.category == "domain_invalid"

    def test_cf_challenge_domain_invalid(self, monkeypatch):
        client = self._client()
        _patch_session(
            monkeypatch, client, _CtxSession([_CtxResp(403, "Checking your browser")])
        )
        with pytest.raises(ZlibError) as ei:
            asyncio.run(client._request_json("GET", "/eapi/info"))
        assert ei.value.category == "domain_invalid"

    def test_rate_limited(self, monkeypatch):
        client = self._client()
        _patch_session(
            monkeypatch, client, _CtxSession([_CtxResp(200, "Too many requests")])
        )
        with pytest.raises(ZlibError) as ei:
            asyncio.run(client._request_json("GET", "/eapi/info"))
        assert ei.value.category == "rate_limited"

    def test_business_error_not_rate_limited(self, monkeypatch):
        """'Err #102' 是业务错误：非 JSON → api_error，而非 rate_limited"""
        client = self._client()
        _patch_session(
            monkeypatch, client, _CtxSession([_CtxResp(200, "Err #102: no file")])
        )
        with pytest.raises(ZlibError) as ei:
            asyncio.run(client._request_json("GET", "/eapi/info"))
        assert ei.value.category == "api_error"

    def test_connection_error_network(self, monkeypatch):
        client = self._client()

        class _FailingSession:
            def request(self, method, url, data=None, **kwargs):
                class _FailingResp:
                    async def __aenter__(self):
                        raise aiohttp.ClientConnectionError("refused")

                    async def __aexit__(self, *args):
                        return False

                return _FailingResp()

        _patch_session(monkeypatch, client, _FailingSession())
        with pytest.raises(ZlibError) as ei:
            asyncio.run(client._request_json("GET", "/eapi/info"))
        assert ei.value.category == "network_error"

    def test_success_with_mojibake_fix(self, monkeypatch):
        """成功路径：mojibake 修复在 JSON 解析后递归应用"""
        client = self._client()
        # latin-1 双重编码的 "Frédéric"（UTF-8 字节被按 latin-1 解读）
        resp = _CtxResp(200, '{"success": true, "user": {"email": "FrÃ©dÃ©ric"}}')
        _patch_session(monkeypatch, client, _CtxSession([resp]))
        data = asyncio.run(client._request_json("GET", "/eapi/info"))
        assert data["user"]["email"] == "Frédéric"


class TestPickDownloadAccount:
    """额度挑选与文案"""

    def test_picks_account_with_most_left(self):
        client = _make_client()
        client.pool = [
            Account(name="a", logged_in=True, downloads_today=5, downloads_limit=10),
            Account(name="b", logged_in=True, downloads_today=1, downloads_limit=10),
        ]
        assert client.pick_download_account().name == "b"

    def test_quota_message_uses_max_limit(self):
        """额度文案取账号池实际上限，不硬编码 10（Premium 也可为 25）"""
        client = _make_client()
        client.pool = [
            Account(name="a", logged_in=True, downloads_today=10, downloads_limit=10),
            Account(name="b", logged_in=True, downloads_today=25, downloads_limit=25),
        ]
        with pytest.raises(ZlibError) as ei:
            client.pick_download_account()
        assert ei.value.category == "quota_exhausted"
        assert "25" in ei.value.message

    def test_not_logged_in_accounts_excluded(self):
        client = _make_client()
        client.pool = [Account(name="a", logged_in=False, downloads_limit=10)]
        with pytest.raises(ZlibError) as ei:
            client.pick_download_account()
        assert ei.value.category == "quota_exhausted"
