"""zlib_client：登录、账号轮换、搜索、封面、下载与诊断。

E-API 层用 FakeApi 脚本化应答替换 ZlibTransport.request_json，文件/封面下载替换
ZlibTransport.guarded_get；不发起任何真实网络请求。
"""

from __future__ import annotations

import io
import types

import aiohttp
import pytest
from PIL import Image
from stubs import (
    FakeApi,
    FakeResponse,
    RecordingLogger,
    ZlibError,
    api_book,
    biz_error,
    download_link_response,
    make_account,
    make_book,
    ok,
    profile,
)

from astrbot_plugin_zlibrary_assistant import zlib_client, zlib_security
from astrbot_plugin_zlibrary_assistant.zlib_client import (
    EP_DOMAINS,
    EP_FILE,
    EP_LOGIN,
    EP_PROFILE,
    EP_SEARCH,
    LOGIN_RETRY_COOLDOWN,
    ZlibClient,
    _classify_business_error,
    mask_proxy,
)
from astrbot_plugin_zlibrary_assistant.zlib_domains import BUILTIN_MIRRORS

BUILTIN = [f"https://{h}" for h in BUILTIN_MIRRORS]
SECRET_KEY = "KEY-SECRET-123"
SECRET_PW = "PW-SECRET-456"
LOGIN_ERR = biz_error("Please login", 401)


class Clock:
    def __init__(self):
        self.now = 5000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    clk = Clock()
    monkeypatch.setattr(zlib_client, "time", types.SimpleNamespace(monotonic=clk))
    return clk


@pytest.fixture
def log(monkeypatch):
    rec = RecordingLogger()
    monkeypatch.setattr(zlib_client, "logger", rec)
    return rec


@pytest.fixture
def make_client(monkeypatch, clock, log):
    def factory(accounts=(), api: FakeApi | None = None, **kw):
        client = ZlibClient(list(accounts), **kw)
        client.api = api or FakeApi()
        monkeypatch.setattr(client.transport, "request_json", client.api.request_json)
        return client

    return factory


def remix(name="r", **over):
    fields = {"remix_userid": "1", "remix_userkey": SECRET_KEY}
    fields.update(over)
    return make_account(name, **fields)


def pwd(name="p", **over):
    fields = {"email": "u@example.com", "password": SECRET_PW}
    fields.update(over)
    return make_account(name, **fields)


def file_path(book):
    return EP_FILE.format(book_id=book.id, hash_id=book.hash)


def fake_fetch(
    client, monkeypatch, body=b"FILE", *, status=200, chunk_size=None, err=""
):
    """替换 guarded_get：返回给定响应（body 为 None 表示被安全校验拒绝），记录调用。"""
    calls = []

    async def guarded_get(url, *, headers=None, cookies=None, timeout_total):
        calls.append({"url": url, "timeout_total": timeout_total})
        if body is None:
            return None, err or "blocked"
        return FakeResponse(status, body=body, chunk_size=chunk_size), ""

    monkeypatch.setattr(client.transport, "guarded_get", guarded_get)
    return calls


# ---------- 业务错误分类 ----------


class TestClassifyBusinessError:
    @pytest.mark.parametrize(
        ("status", "resp", "category"),
        [
            (401, {"success": 0}, "auth_failed"),
            (400, {"error": "Please login"}, "auth_failed"),
            (403, {"error": "Please login to continue"}, "auth_failed"),
            (200, {"error": "Incorrect email or password"}, "auth_failed"),
            (200, {"error": "Unauthorized"}, "auth_failed"),
            (200, {"error": "Invalid credentials"}, "auth_failed"),
            (200, {"error": "Daily download limit reached"}, "quota_exhausted"),
            (200, {"error": "You exceeded your quota"}, "quota_exhausted"),
            (200, {"error": "File not found"}, "api_error"),
            (200, {}, "api_error"),
        ],
    )
    def test_categories(self, status, resp, category):
        assert _classify_business_error(status, resp, "x").category == category

    def test_api_error_message_and_detail_bounded(self):
        err = _classify_business_error(200, {"error": "e" * 500}, "搜索")
        assert err.message == "搜索失败：" + "e" * 80
        assert len(err.detail) <= 200

    def test_mask_proxy_drops_userinfo(self):
        assert mask_proxy("http://user:pass@127.0.0.1:7890") == "http://127.0.0.1:7890"
        assert mask_proxy("") == ""
        assert "pass" not in mask_proxy("socks5://u:pass@h:1080")


# ---------- 登录 ----------


class TestLogin:
    @pytest.mark.asyncio
    async def test_remix_login_is_get_profile_with_cookies(self, make_client):
        api = FakeApi().on(
            "GET", EP_PROFILE, profile(user_id="1", key=SECRET_KEY, today=3)
        )
        client = make_client([remix()], api)
        await client.ensure_login()
        (call,) = api.calls
        assert (call["method"], call["path"]) == ("GET", EP_PROFILE)
        assert call["cookies"] == {"remix_userid": "1", "remix_userkey": SECRET_KEY}
        acc = client.accounts[0]
        assert acc.logged_in
        assert (acc.downloads_today, acc.downloads_limit) == (3, 10)
        assert acc.last_error == ""

    @pytest.mark.asyncio
    async def test_password_login_is_post_login_with_form_and_no_cookies(
        self, make_client
    ):
        api = FakeApi().on("POST", EP_LOGIN, profile(user_id="42", key="NEWKEY"))
        client = make_client([pwd()], api)
        await client.ensure_login()
        (call,) = api.calls
        assert (call["method"], call["path"]) == ("POST", EP_LOGIN)
        assert call["data"] == {"email": "u@example.com", "password": SECRET_PW}
        assert call["cookies"] == {}
        acc = client.accounts[0]
        assert acc.logged_in
        assert (acc.remix_userid, acc.remix_userkey) == ("42", "NEWKEY")

    @pytest.mark.asyncio
    async def test_remix_preferred_over_password(self, make_client):
        api = FakeApi().on("GET", EP_PROFILE, profile())
        client = make_client([remix(email="a@b.c", password="x")], api)
        await client.ensure_login()
        assert api.paths() == [("GET", EP_PROFILE)]

    @pytest.mark.asyncio
    async def test_profile_applied_premium_and_default_limit(self, make_client):
        resp = ok(
            user={"id": 7, "remix_userkey": "k", "isPremium": 1, "downloads_today": 0}
        )
        client = make_client([remix()], FakeApi().on("GET", EP_PROFILE, resp))
        await client.ensure_login()
        acc = client.accounts[0]
        assert acc.is_premium is True
        assert acc.downloads_limit == 10
        assert acc.remix_userid == "7"

    @pytest.mark.asyncio
    async def test_account_without_credentials_fails_without_request(self, make_client):
        api = FakeApi()
        client = make_client([make_account("empty")], api)
        await client.ensure_login()
        assert api.calls == []
        acc = client.accounts[0]
        assert acc.logged_in is False
        assert "缺少凭据" in acc.last_error

    @pytest.mark.asyncio
    async def test_failed_login_marks_account_and_does_not_block_others(
        self, make_client
    ):
        api = FakeApi().on("GET", EP_PROFILE, LOGIN_ERR).on("POST", EP_LOGIN, profile())
        client = make_client([remix("bad"), pwd("good")], api)
        await client.ensure_login()
        bad, good = client.accounts
        assert bad.logged_in is False
        assert bad.last_error
        assert good.logged_in is True

    @pytest.mark.asyncio
    async def test_logged_in_accounts_skipped(self, make_client):
        api = FakeApi()
        client = make_client([remix(logged_in=True)], api)
        await client.ensure_login()
        assert api.calls == []

    @pytest.mark.asyncio
    async def test_force_relogs_logged_in_accounts(self, make_client):
        api = FakeApi().on("GET", EP_PROFILE, profile())
        client = make_client([remix(logged_in=True)], api)
        await client.ensure_login(force=True)
        assert len(api.calls) == 1


class TestLoginCooldown:
    @pytest.mark.asyncio
    async def test_failed_login_not_retried_within_cooldown(self, make_client, clock):
        api = FakeApi().on("GET", EP_PROFILE, LOGIN_ERR)
        client = make_client([remix()], api)
        await client.ensure_login()
        clock.now += LOGIN_RETRY_COOLDOWN - 1
        await client.ensure_login()
        assert len(api.calls) == 1

    @pytest.mark.asyncio
    async def test_retried_after_cooldown(self, make_client, clock):
        api = FakeApi().on("GET", EP_PROFILE, LOGIN_ERR, profile())
        client = make_client([remix()], api)
        await client.ensure_login()
        clock.now += LOGIN_RETRY_COOLDOWN + 1
        await client.ensure_login()
        assert len(api.calls) == 2
        assert client.accounts[0].logged_in is True

    @pytest.mark.asyncio
    async def test_force_ignores_cooldown(self, make_client):
        api = FakeApi().on("GET", EP_PROFILE, LOGIN_ERR)
        client = make_client([remix()], api)
        await client.ensure_login()
        await client.ensure_login(force=True)
        assert len(api.calls) == 2

    @pytest.mark.asyncio
    async def test_cooldown_is_per_account(self, make_client):
        api = FakeApi().on("GET", EP_PROFILE, LOGIN_ERR)
        client = make_client([remix("a"), remix("b")], api)
        await client.ensure_login()
        assert len(api.calls) == 2


# ---------- 域名池接入 ----------


class TestDomainRouting:
    """带账号凭据的请求只发往可信域名；学习到的域名只服务匿名请求。"""

    @pytest.fixture(autouse=True)
    def _no_dns(self, monkeypatch):
        monkeypatch.setattr(
            zlib_security, "_resolve_host_ips", lambda h: ["93.184.216.34"]
        )

    @staticmethod
    def _learned_only_ok(client):
        learned = "https://learned.example"
        assert client.domains.learn(["learned.example"]) == 1
        client.domains.max_attempts = 20

        def answer(call):
            if call["base_url"] == learned:
                return ok(books=[])
            return ZlibError("network_error", "down")

        return learned, answer

    @pytest.mark.asyncio
    async def test_anonymous_request_may_use_learned_domain(self, make_client):
        client = make_client()
        learned, answer = self._learned_only_ok(client)
        client.api.on("POST", EP_SEARCH, answer)
        result = await client.search("x")
        assert result.books == []
        assert client.api.calls[-1]["base_url"] == learned

    @pytest.mark.asyncio
    async def test_remix_login_never_sent_to_learned_domain(self, make_client):
        client = make_client([remix()])
        learned, answer = self._learned_only_ok(client)
        client.api.on("GET", EP_PROFILE, answer)
        await client.ensure_login()
        assert client.accounts[0].logged_in is False
        assert learned not in {c["base_url"] for c in client.api.calls}
        assert {c["base_url"] for c in client.api.calls} == set(BUILTIN)

    @pytest.mark.asyncio
    async def test_password_login_never_sent_to_learned_domain(self, make_client):
        client = make_client([pwd()])
        learned, answer = self._learned_only_ok(client)
        client.api.on("POST", EP_LOGIN, answer)
        await client.ensure_login()
        assert learned not in {c["base_url"] for c in client.api.calls}

    @pytest.mark.asyncio
    async def test_download_link_request_never_sent_to_learned_domain(
        self, make_client
    ):
        book = make_book(1)
        client = make_client([remix(logged_in=True)])
        learned, answer = self._learned_only_ok(client)
        client.api.on("GET", file_path(book), answer)
        with pytest.raises(ZlibError) as exc:
            await client.get_download_link(client.accounts[0], book)
        assert exc.value.category == "all_domains_failed"
        assert learned not in {c["base_url"] for c in client.api.calls}

    @pytest.mark.asyncio
    async def test_discover_domains_learns_valid_and_ignores_junk(self, make_client):
        resp = ok(
            domains=[
                {"domain": "fresh.example"},
                {"domain": "127.0.0.1"},
                "junk",
                {"x": 1},
            ]
        )
        client = make_client(api=FakeApi().on("GET", EP_DOMAINS, resp))
        assert await client.discover_domains() == 1

    @pytest.mark.asyncio
    async def test_discover_domains_failure_returns_zero(self, make_client):
        api = FakeApi().on("GET", EP_DOMAINS, ZlibError("domain_invalid", "x"))
        assert await make_client(api=api).discover_domains() == 0

    def test_user_domains_and_socks_flag_passed_to_pool(self, make_client):
        client = make_client(domains=["https://mine.example"])
        assert client.domains.primary == "https://mine.example"
        onion = "a" * 56 + ".onion"
        with pytest.raises(ValueError):
            make_client(domains=[onion])
        client = make_client(domains=[onion], proxy="socks5h://127.0.0.1:9050")
        assert client.domains.primary.endswith(".onion")


# ---------- 搜索 ----------


class TestSearch:
    @pytest.mark.asyncio
    async def test_request_payload_and_result(self, make_client):
        resp = ok(
            books=[api_book(1), api_book(2)],
            pagination={"current": 2, "total_pages": 5, "total_items": 99},
        )
        api = FakeApi().on("POST", EP_SEARCH, resp)
        client = make_client(api=api)
        result = await client.search(
            "  资本论  ", limit=7, page=2, language="中文", extension=".PDF"
        )
        (call,) = api.calls
        assert call["data"] == {
            "message": "资本论",
            "limit": 7,
            "page": 2,
            "languages[]": "chinese",
            "extensions[]": "pdf",
        }
        assert call["cookies"] == {}
        assert (result.query, result.page, result.total_pages, result.total_items) == (
            "资本论",
            2,
            5,
            99,
        )
        assert [b.id for b in result.books] == [1, 2]
        assert (result.language, result.extension) == ("chinese", "pdf")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("given", "sent"), [(0, 1), (-5, 1), (10, 10), (500, 50)])
    async def test_limit_clamped(self, make_client, given, sent):
        api = FakeApi().on("POST", EP_SEARCH, ok(books=[]))
        await make_client(api=api).search("x", limit=given)
        assert api.calls[0]["data"]["limit"] == sent

    @pytest.mark.asyncio
    async def test_page_floor_and_optional_filters_omitted(self, make_client):
        api = FakeApi().on("POST", EP_SEARCH, ok(books=[]))
        await make_client(api=api).search("x", page=0)
        data = api.calls[0]["data"]
        assert data["page"] == 1
        assert "languages[]" not in data
        assert "extensions[]" not in data

    @pytest.mark.asyncio
    @pytest.mark.parametrize("query", ["", "   "])
    async def test_empty_query_rejected_without_request(self, make_client, query):
        api = FakeApi()
        with pytest.raises(ZlibError) as exc:
            await make_client(api=api).search(query)
        assert exc.value.category == "api_error"
        assert api.calls == []

    @pytest.mark.asyncio
    async def test_results_cached_by_id_and_invalid_entries_dropped(self, make_client):
        resp = ok(books=[api_book(5), {"id": 6}, "x", api_book(7)])
        client = make_client(api=FakeApi().on("POST", EP_SEARCH, resp))
        result = await client.search("x")
        assert [b.id for b in result.books] == [5, 7]
        assert client.book_cache.get(5).hash == "h5"
        assert client.book_cache.get(6) is None

    @pytest.mark.asyncio
    async def test_pagination_defaults(self, make_client):
        api = FakeApi().on("POST", EP_SEARCH, ok(books=[api_book(1)]))
        result = await make_client(api=api).search("x", page=3)
        assert (result.page, result.total_pages, result.total_items) == (3, 1, 1)

    @pytest.mark.asyncio
    async def test_cache_flushed_to_disk(self, make_client, tmp_path):
        api = FakeApi().on("POST", EP_SEARCH, ok(books=[api_book(5)]))
        path = tmp_path / "cache.json"
        await make_client(api=api, cache_path=path).search("x")
        assert path.exists()

    @pytest.mark.asyncio
    async def test_non_auth_errors_not_retried(self, make_client):
        api = FakeApi().on("POST", EP_SEARCH, biz_error("boom"))
        client = make_client([remix(logged_in=True)], api)
        with pytest.raises(ZlibError) as exc:
            await client.search("x")
        assert exc.value.category == "api_error"
        assert len(api.calls) == 1


class TestSearchAuthRetry:
    @pytest.mark.asyncio
    async def test_retries_with_logged_in_account_cookies(self, make_client):
        api = FakeApi().on(
            "POST", EP_SEARCH, biz_error("Please login", 400), ok(books=[api_book(1)])
        )
        client = make_client([remix(logged_in=True)], api)
        result = await client.search("x")
        assert [b.id for b in result.books] == [1]
        first, second = api.calls
        assert first["cookies"] == {}
        assert second["cookies"] == {"remix_userid": "1", "remix_userkey": SECRET_KEY}

    @pytest.mark.asyncio
    async def test_logs_in_first_when_needed(self, make_client):
        api = (
            FakeApi()
            .on("POST", EP_SEARCH, biz_error("Please login", 403), ok(books=[]))
            .on("GET", EP_PROFILE, profile(key=SECRET_KEY))
        )
        client = make_client([remix()], api)
        await client.search("x")
        assert api.paths() == [
            ("POST", EP_SEARCH),
            ("GET", EP_PROFILE),
            ("POST", EP_SEARCH),
        ]

    @pytest.mark.asyncio
    async def test_no_account_raises_auth_failed(self, make_client):
        api = FakeApi().on("POST", EP_SEARCH, biz_error("Please login", 403))
        with pytest.raises(ZlibError) as exc:
            await make_client(api=api).search("x")
        assert exc.value.category == "auth_failed"
        assert len(api.calls) == 1

    @pytest.mark.asyncio
    async def test_account_login_failure_raises_auth_failed(self, make_client):
        api = (
            FakeApi()
            .on("POST", EP_SEARCH, biz_error("Please login", 403))
            .on("GET", EP_PROFILE, LOGIN_ERR)
        )
        with pytest.raises(ZlibError) as exc:
            await make_client([remix()], api).search("x")
        assert exc.value.category == "auth_failed"

    @pytest.mark.asyncio
    async def test_retry_failure_propagates_and_is_not_repeated(self, make_client):
        api = FakeApi().on("POST", EP_SEARCH, biz_error("Please login", 403))
        client = make_client([remix(logged_in=True)], api)
        with pytest.raises(ZlibError) as exc:
            await client.search("x")
        assert exc.value.category == "auth_failed"
        assert len(api.calls) == 2


# ---------- 下载 ----------


class TestGetDownloadLink:
    async def _link(self, make_client, resp, book=None):
        book = book or make_book(9, extension="epub")
        api = FakeApi().on("GET", file_path(book), resp)
        client = make_client([remix(logged_in=True)], api)
        return await client.get_download_link(client.accounts[0], book), api

    @pytest.mark.asyncio
    async def test_link_and_filename(self, make_client):
        resp = download_link_response("https://8.8.8.8/f", "pdf", "My Book (z-lib)")
        (link, name), api = await self._link(make_client, resp)
        assert (link, name) == ("https://8.8.8.8/f", "My Book.pdf")
        assert api.calls[0]["cookies"]["remix_userid"] == "1"

    @pytest.mark.asyncio
    async def test_extension_not_duplicated(self, make_client):
        resp = download_link_response(description="a.pdf (x)", extension="pdf")
        (_, name), _ = await self._link(make_client, resp)
        assert name == "a.pdf"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("extension", "suffix"),
        [
            ("pdf/../../evil", ".pdfevil"),
            ("p.d.f", ".pdf"),
            ("", ".epub"),
            ("../..", ".epub"),
            ("a" * 20, "." + "a" * 8),
        ],
    )
    async def test_extension_whitelisted(self, make_client, extension, suffix):
        resp = download_link_response(extension=extension, description="Name (x)")
        (_, name), _ = await self._link(make_client, resp)
        assert name == "Name" + suffix

    @pytest.mark.asyncio
    async def test_bin_when_no_extension_anywhere(self, make_client):
        resp = download_link_response(extension="", description="N (x)")
        (_, name), _ = await self._link(make_client, resp, make_book(9, extension=""))
        assert name == "N.bin"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "description",
        [
            "../../etc/passwd (x)",
            "..\\..\\win.ini (x)",
            "a/b\\c:d*e?f (x)",
            "\x00\x1fbad (x)",
        ],
    )
    async def test_description_cannot_inject_path(self, make_client, description):
        resp = download_link_response(description=description, extension="pdf")
        (_, name), _ = await self._link(make_client, resp)
        assert not set(name) & set('/\\:*?"<>|\x00\x1f')
        assert not name.startswith(".")

    @pytest.mark.asyncio
    async def test_empty_description_falls_back_to_book_id(self, make_client):
        resp = download_link_response(description="", extension="pdf")
        (_, name), _ = await self._link(make_client, resp)
        assert name == "book_9.pdf"

    @pytest.mark.asyncio
    async def test_missing_link_is_api_error(self, make_client):
        book = make_book(9)
        api = FakeApi().on("GET", file_path(book), ok(file={}))
        client = make_client([remix(logged_in=True)], api)
        with pytest.raises(ZlibError) as exc:
            await client.get_download_link(client.accounts[0], book)
        assert exc.value.category == "api_error"


class TestFetchFile:
    URL = "https://8.8.8.8/f"

    @pytest.mark.asyncio
    async def test_returns_full_body_across_chunks(self, make_client, monkeypatch):
        client = make_client()
        body = bytes(range(256)) * 100
        calls = fake_fetch(client, monkeypatch, body, chunk_size=777)
        assert await client._fetch_file(self.URL) == body
        assert calls[0]["timeout_total"] == client.timeout * 4

    @pytest.mark.asyncio
    async def test_exactly_at_cap_accepted(self, make_client, monkeypatch):
        client = make_client()
        client.max_download_bytes = 1000
        fake_fetch(client, monkeypatch, b"x" * 1000, chunk_size=64)
        assert len(await client._fetch_file(self.URL)) == 1000

    @pytest.mark.asyncio
    async def test_over_cap_rejected_even_when_split_into_many_chunks(
        self, make_client, monkeypatch
    ):
        client = make_client()
        client.max_download_bytes = 1000
        fake_fetch(client, monkeypatch, b"x" * 5000, chunk_size=64)
        with pytest.raises(ZlibError) as exc:
            await client._fetch_file(self.URL)
        assert exc.value.category == "api_error"
        assert "上限" in exc.value.message

    def test_cap_derived_from_config_mb(self, make_client):
        assert make_client(max_download_mb=3).max_download_bytes == 3 * 1024 * 1024
        assert make_client(max_download_mb=0).max_download_bytes == 1024 * 1024

    @pytest.mark.asyncio
    async def test_empty_body_rejected(self, make_client, monkeypatch):
        client = make_client()
        fake_fetch(client, monkeypatch, b"")
        with pytest.raises(ZlibError, match="为空"):
            await client._fetch_file(self.URL)

    @pytest.mark.asyncio
    async def test_http_error_status(self, make_client, monkeypatch):
        client = make_client()
        fake_fetch(client, monkeypatch, b"nope", status=503)
        with pytest.raises(ZlibError, match="HTTP 503"):
            await client._fetch_file(self.URL)

    @pytest.mark.asyncio
    async def test_guard_rejection_becomes_network_error_with_reason(
        self, make_client, monkeypatch
    ):
        client = make_client()
        fake_fetch(client, monkeypatch, None, err="URL 未通过安全校验（内网）")
        with pytest.raises(ZlibError) as exc:
            await client._fetch_file("http://10.0.0.1/f")
        assert exc.value.category == "network_error"
        assert "安全校验" in exc.value.detail

    @pytest.mark.asyncio
    async def test_aiohttp_error_during_read_is_network_error(
        self, make_client, monkeypatch
    ):
        client = make_client()

        class Broken(FakeResponse):
            async def __aenter__(self):
                raise aiohttp.ClientPayloadError("cut")

        async def guarded_get(url, **kw):
            return Broken(200, body=b"x"), ""

        monkeypatch.setattr(client.transport, "guarded_get", guarded_get)
        with pytest.raises(ZlibError) as exc:
            await client._fetch_file(self.URL)
        assert exc.value.category == "network_error"


class TestDownload:
    @staticmethod
    def _wire(client, monkeypatch, book):
        fake_fetch(client, monkeypatch, b"CONTENT")
        client.api.on("GET", file_path(book), download_link_response())

    @pytest.mark.asyncio
    async def test_success_returns_account_filename_content(
        self, make_client, monkeypatch
    ):
        book = make_book(1)
        client = make_client([remix(logged_in=True)])
        self._wire(client, monkeypatch, book)
        client.api.on("GET", EP_PROFILE, profile(today=1))
        account, filename, content = await client.download(book)
        assert (account.name, filename, content) == ("r", "My Book.pdf", b"CONTENT")

    @pytest.mark.asyncio
    async def test_quota_refreshed_from_server_after_download(
        self, make_client, monkeypatch
    ):
        book = make_book(1)
        client = make_client([remix(logged_in=True)])
        self._wire(client, monkeypatch, book)
        client.api.on("GET", EP_PROFILE, profile(today=7, limit=10))
        account, _, _ = await client.download(book)
        assert (account.downloads_today, account.downloads_limit) == (7, 10)
        assert client.api.paths()[-1] == ("GET", EP_PROFILE)

    @pytest.mark.asyncio
    async def test_local_count_kept_when_refresh_fails(self, make_client, monkeypatch):
        book = make_book(1)
        client = make_client([remix(logged_in=True, downloads_today=2)])
        self._wire(client, monkeypatch, book)
        client.api.on("GET", EP_PROFILE, ZlibError("domain_invalid", "x"))
        account, _, _ = await client.download(book)
        assert account.downloads_today == 3

    @pytest.mark.asyncio
    async def test_prefers_account_with_most_remaining_quota(
        self, make_client, monkeypatch
    ):
        book = make_book(1)
        client = make_client(
            [
                remix("low", remix_userid="1", logged_in=True, downloads_today=8),
                remix("high", remix_userid="2", logged_in=True, downloads_today=1),
            ]
        )
        self._wire(client, monkeypatch, book)
        client.api.on("GET", EP_PROFILE, profile(user_id="2", today=2))
        account, _, _ = await client.download(book)
        assert account.name == "high"
        link_call = next(c for c in client.api.calls if c["path"] == file_path(book))
        assert link_call["cookies"]["remix_userid"] == "2"

    @pytest.mark.asyncio
    async def test_accounts_without_quota_skipped(self, make_client, monkeypatch):
        book = make_book(1)
        client = make_client(
            [
                remix("full", logged_in=True, downloads_today=10),
                remix("ok", remix_userid="2", logged_in=True),
            ]
        )
        self._wire(client, monkeypatch, book)
        client.api.on("GET", EP_PROFILE, profile(user_id="2"))
        account, _, _ = await client.download(book)
        assert account.name == "ok"

    @pytest.mark.asyncio
    async def test_auth_failed_switches_to_next_account_and_marks_logged_out(
        self, make_client, monkeypatch
    ):
        book = make_book(1)
        client = make_client(
            [
                remix("expired", remix_userid="1", logged_in=True, downloads_today=0),
                remix("fine", remix_userid="2", logged_in=True, downloads_today=5),
            ]
        )
        fake_fetch(client, monkeypatch)

        def answer(call):
            if call["cookies"]["remix_userid"] == "1":
                return LOGIN_ERR
            return download_link_response()

        client.api.on("GET", file_path(book), answer)
        client.api.on("GET", EP_PROFILE, profile(user_id="2", today=6))
        account, _, _ = await client.download(book)
        expired = client.accounts[0]
        assert account.name == "fine"
        assert expired.logged_in is False
        assert expired.last_error

    @pytest.mark.asyncio
    async def test_quota_exhausted_switches_and_zeroes_local_quota(
        self, make_client, monkeypatch
    ):
        book = make_book(1)
        client = make_client(
            [
                remix("drained", remix_userid="1", logged_in=True),
                remix("fresh", remix_userid="2", logged_in=True, downloads_today=5),
            ]
        )
        fake_fetch(client, monkeypatch)

        def answer(call):
            if call["cookies"]["remix_userid"] == "1":
                return biz_error("Daily download limit reached")
            return download_link_response()

        client.api.on("GET", file_path(book), answer)
        client.api.on("GET", EP_PROFILE, profile(user_id="2", today=6))
        account, _, _ = await client.download(book)
        assert account.name == "fresh"
        assert client.accounts[0].downloads_left == 0

    @pytest.mark.asyncio
    async def test_each_account_tried_at_most_once(self, make_client):
        book = make_book(1)
        client = make_client(
            [remix("a", logged_in=True), remix("b", remix_userid="2", logged_in=True)]
        )
        client.api.on("GET", file_path(book), biz_error("Daily download limit reached"))
        with pytest.raises(ZlibError) as exc:
            await client.download(book)
        assert exc.value.category == "quota_exhausted"
        assert len([c for c in client.api.calls if c["path"] == file_path(book)]) == 2

    @pytest.mark.asyncio
    async def test_all_logins_expired_reports_auth_failed(self, make_client):
        book = make_book(1)
        client = make_client([remix(logged_in=True)])
        client.api.on("GET", file_path(book), LOGIN_ERR)
        with pytest.raises(ZlibError) as exc:
            await client.download(book)
        assert exc.value.category == "auth_failed"

    @pytest.mark.asyncio
    async def test_no_accounts_configured(self, make_client):
        client = make_client([])
        with pytest.raises(ZlibError) as exc:
            await client.download(make_book(1))
        assert exc.value.category == "auth_failed"
        assert client.api.calls == []

    @pytest.mark.asyncio
    async def test_no_account_can_log_in(self, make_client):
        client = make_client([remix()])
        client.api.on("GET", EP_PROFILE, LOGIN_ERR)
        with pytest.raises(ZlibError) as exc:
            await client.download(make_book(1))
        assert exc.value.category == "auth_failed"

    @pytest.mark.asyncio
    async def test_all_accounts_over_quota(self, make_client):
        client = make_client([remix(logged_in=True, downloads_today=10)])
        with pytest.raises(ZlibError) as exc:
            await client.download(make_book(1))
        assert exc.value.category == "quota_exhausted"
        assert client.api.calls == []

    @pytest.mark.asyncio
    async def test_other_errors_are_raised_not_rotated(self, make_client):
        book = make_book(1)
        client = make_client(
            [remix("a", logged_in=True), remix("b", remix_userid="2", logged_in=True)]
        )
        client.api.on("GET", file_path(book), biz_error("File not found"))
        with pytest.raises(ZlibError) as exc:
            await client.download(book)
        assert exc.value.category == "api_error"
        assert len(client.api.calls) == 1

    @pytest.mark.asyncio
    async def test_oversized_file_not_counted_against_quota(
        self, make_client, monkeypatch
    ):
        book = make_book(1)
        client = make_client([remix(logged_in=True)])
        client.max_download_bytes = 10
        fake_fetch(client, monkeypatch, b"x" * 100, chunk_size=7)
        client.api.on("GET", file_path(book), download_link_response())
        with pytest.raises(ZlibError, match="上限"):
            await client.download(book)
        assert client.accounts[0].downloads_today == 0

    @pytest.mark.asyncio
    async def test_download_by_id(self, make_client, monkeypatch):
        book = make_book(11)
        client = make_client([remix(logged_in=True)])
        client.book_cache.put_many([book])
        self._wire(client, monkeypatch, book)
        client.api.on("GET", EP_PROFILE, profile())
        _, filename, _ = await client.download_by_id(11)
        assert filename == "My Book.pdf"

    @pytest.mark.asyncio
    async def test_download_by_id_unknown(self, make_client):
        with pytest.raises(ZlibError, match="id=99"):
            await make_client().download_by_id(99)


# ---------- 封面 ----------


def _png_bytes(w=60, h=90):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "blue").save(buf, "PNG")
    return buf.getvalue()


class TestFetchCover:
    COVER = "https://8.8.8.8/c.png"

    @pytest.mark.asyncio
    async def test_embeds_resized_jpeg_data_uri(self, make_client, monkeypatch):
        client = make_client(domains=["https://mine.example"])
        seen = {}

        async def guarded_get(url, *, headers=None, cookies=None, timeout_total):
            seen.update(url=url, headers=headers, timeout=timeout_total)
            return FakeResponse(200, body=_png_bytes()), ""

        monkeypatch.setattr(client.transport, "guarded_get", guarded_get)
        uri = await client.fetch_cover_data_uri(self.COVER)
        assert uri.startswith("data:image/jpeg;base64,")
        assert seen["headers"] == {"Referer": "https://mine.example/"}
        assert seen["timeout"] == 8

    @pytest.mark.asyncio
    async def test_empty_url_makes_no_request(self, make_client, monkeypatch):
        client = make_client()

        async def boom(*a, **k):
            raise AssertionError("不应发起请求")

        monkeypatch.setattr(client.transport, "guarded_get", boom)
        assert await client.fetch_cover_data_uri("") == ""

    @pytest.mark.asyncio
    async def test_guard_rejection_gives_empty(self, make_client, monkeypatch):
        client = make_client()
        fake_fetch(client, monkeypatch, None)
        assert await client.fetch_cover_data_uri("http://10.0.0.1/c.png") == ""

    @pytest.mark.asyncio
    async def test_non_200_gives_empty(self, make_client, monkeypatch):
        client = make_client()
        fake_fetch(client, monkeypatch, _png_bytes(), status=404)
        assert await client.fetch_cover_data_uri(self.COVER) == ""

    @pytest.mark.asyncio
    async def test_oversized_cover_gives_empty(self, make_client, monkeypatch):
        client = make_client()
        fake_fetch(client, monkeypatch, _png_bytes(600, 900), chunk_size=100)
        assert await client.fetch_cover_data_uri(self.COVER, max_bytes=100) == ""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", [b"", b"<html>not an image</html>"])
    async def test_empty_or_undecodable_gives_empty(
        self, make_client, monkeypatch, body
    ):
        client = make_client()
        fake_fetch(client, monkeypatch, body)
        assert await client.fetch_cover_data_uri(self.COVER) == ""


# ---------- 状态与诊断 ----------


class TestStatusAndDiagnose:
    def test_account_status_rows(self, make_client):
        client = make_client(
            [
                remix("r", logged_in=True, downloads_today=3, is_premium=True),
                pwd("p", last_error="bad"),
            ]
        )
        rows = client.account_status()
        assert rows[0] == {
            "name": "r",
            "logged_in": True,
            "premium": True,
            "login_mode": "remix",
            "downloads_today": 3,
            "downloads_limit": 10,
            "downloads_left": 7,
            "last_error": "",
        }
        assert rows[1]["login_mode"] == "password"
        assert rows[1]["last_error"] == "bad"

    def test_account_status_has_no_credentials(self, make_client):
        text = str(make_client([remix(), pwd()]).account_status())
        assert SECRET_KEY not in text
        assert SECRET_PW not in text

    @pytest.mark.asyncio
    async def test_diagnose_probes_every_domain_and_relogs(self, make_client):
        def answer(call):
            if call["base_url"] == BUILTIN[0]:
                return ZlibError("diamwall_blocked", "blocked")
            return ok(books=[])

        api = FakeApi().on("POST", EP_SEARCH, answer).on("GET", EP_PROFILE, profile())
        client = make_client(
            [remix(logged_in=True)], api, proxy="http://user:pw@127.0.0.1:7890"
        )
        diag = await client.diagnose()
        assert len(diag.domains) == len(BUILTIN)
        assert [d.ok for d in diag.domains].count(False) == 1
        assert diag.proxy == "http://127.0.0.1:7890"
        assert diag.accounts[0]["logged_in"] is True
        probe = next(c for c in api.calls if c["path"] == EP_SEARCH)
        assert probe["data"] == {"message": "test", "limit": 1}
        assert ("GET", EP_PROFILE) in api.paths()

    @pytest.mark.asyncio
    async def test_diagnose_treats_business_error_as_failed_probe(self, make_client):
        api = FakeApi().on("POST", EP_SEARCH, biz_error("Please login", 403))
        diag = await make_client(api=api).diagnose()
        assert not any(d.ok for d in diag.domains)

    @pytest.mark.asyncio
    async def test_close_flushes_cache(self, make_client, tmp_path):
        path = tmp_path / "cache.json"
        client = make_client(cache_path=path)
        client.book_cache.put_many([make_book(1)])
        await client.close()
        assert path.exists()


# ---------- 凭据不外泄 ----------


class TestNoCredentialLeak:
    @pytest.mark.asyncio
    async def test_failed_logins_keep_credentials_out_of_logs_and_errors(
        self, make_client, log
    ):
        api = (
            FakeApi()
            .on("GET", EP_PROFILE, LOGIN_ERR)
            .on("POST", EP_LOGIN, biz_error("Incorrect email or password"))
        )
        client = make_client([remix("r"), pwd("p", email="leak@example.com")], api)
        await client.ensure_login()
        blob = log.text + "".join(a.last_error for a in client.accounts)
        assert log.lines
        assert SECRET_KEY not in blob
        assert SECRET_PW not in blob
        assert "leak@example.com" not in blob

    @pytest.mark.asyncio
    async def test_successful_flow_logs_without_credentials(
        self, make_client, monkeypatch, log
    ):
        book = make_book(1)
        client = make_client([remix(logged_in=True)])
        fake_fetch(client, monkeypatch)
        client.api.on("GET", file_path(book), download_link_response())
        client.api.on("GET", EP_PROFILE, profile(key=SECRET_KEY))
        await client.download(book)
        assert log.lines
        assert SECRET_KEY not in log.text

    @pytest.mark.asyncio
    async def test_domain_failure_errors_do_not_contain_password(self, make_client):
        client = make_client([pwd()])
        client.api.on("POST", EP_LOGIN, ZlibError("network_error", "down"))
        with pytest.raises(ZlibError) as exc:
            await client.login_account(client.accounts[0])
        assert SECRET_PW not in str(exc.value) + exc.value.detail
