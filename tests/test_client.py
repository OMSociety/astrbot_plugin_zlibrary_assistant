"""zlib_client：登录、账号轮换、搜索、封面、下载与诊断。

E-API 层用 FakeApi 脚本化应答替换 ZlibTransport.request_json，文件/封面下载替换
ZlibTransport.guarded_get；不发起任何真实网络请求。
"""

from __future__ import annotations

import asyncio
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
    PROBE_MIN_INTERVAL,
    PROBE_TTL,
    ZlibClient,
    _classify_business_error,
    mask_proxy,
)
from astrbot_plugin_zlibrary_assistant.zlib_domains import BUILTIN_MIRRORS

BUILTIN = [f"https://{h}" for h in BUILTIN_MIRRORS]
SECRET_KEY = "KEY-SECRET-123"
SECRET_PW = "PW-SECRET-456"
LOGIN_ERR = biz_error("Please login", 401)
REMIX_COOKIES = {"remix_userid": "1", "remix_userkey": SECRET_KEY}


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
            (200, {"error": "Please login"}, "auth_failed"),
            (200, {"error": "Daily download limit reached"}, "quota_exhausted"),
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
    @pytest.mark.parametrize(
        ("account_factory", "method", "path", "cookies", "data", "identity"),
        [
            (remix, "GET", EP_PROFILE, REMIX_COOKIES, None, ("1", "k")),
            (
                pwd,
                "POST",
                EP_LOGIN,
                {},
                {"email": "u@example.com", "password": SECRET_PW},
                ("42", "NEWKEY"),
            ),
            # remix 凭证优先：两种凭据都填时不得改走邮箱密码登录
            (
                lambda: remix(email="a@b.c", password=SECRET_PW),
                "GET",
                EP_PROFILE,
                REMIX_COOKIES,
                None,
                ("1", "k"),
            ),
        ],
        ids=["remix", "password", "remix_preferred"],
    )
    async def test_credential_dispatch(
        self, make_client, account_factory, method, path, cookies, data, identity
    ):
        api = (
            FakeApi()
            .on("GET", EP_PROFILE, profile())
            .on("POST", EP_LOGIN, profile(user_id="42", key="NEWKEY"))
        )
        client = make_client([account_factory()], api)
        await client.ensure_login()
        (call,) = api.calls
        assert (call["method"], call["path"]) == (method, path)
        assert call["cookies"] == cookies
        assert call["data"] == data
        acc = client.accounts[0]
        assert acc.logged_in and acc.last_error == ""
        assert (acc.remix_userid, acc.remix_userkey) == identity

    @pytest.mark.asyncio
    async def test_profile_quota_and_premium_applied(self, make_client):
        resp = ok(
            user={
                "id": 7,
                "remix_userkey": "k",
                "isPremium": 1,
                "downloads_today": 3,
            }
        )
        client = make_client([remix()], FakeApi().on("GET", EP_PROFILE, resp))
        await client.ensure_login()
        acc = client.accounts[0]
        assert (acc.downloads_today, acc.downloads_limit, acc.is_premium) == (
            3,
            10,
            True,
        )

    @pytest.mark.asyncio
    async def test_account_without_credentials_fails_without_request(self, make_client):
        api = FakeApi()
        client = make_client([make_account("empty")], api)
        await client.ensure_login()
        assert api.calls == []
        assert client.accounts[0].logged_in is False
        assert "缺少凭据" in client.accounts[0].last_error

    @pytest.mark.asyncio
    async def test_failed_login_marks_account_and_does_not_block_others(
        self, make_client
    ):
        api = FakeApi().on("GET", EP_PROFILE, LOGIN_ERR).on("POST", EP_LOGIN, profile())
        client = make_client([remix("bad"), pwd("good")], api)
        await client.ensure_login()
        bad, good = client.accounts
        assert bad.logged_in is False and bad.last_error
        assert good.logged_in is True

    @pytest.mark.asyncio
    async def test_logged_in_accounts_skipped_unless_forced(self, make_client):
        api = FakeApi().on("GET", EP_PROFILE, profile())
        client = make_client([remix(logged_in=True)], api)
        await client.ensure_login()
        assert api.calls == []
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
        """一个账号的冷却时间戳不得挡住另一个账号。"""
        api = FakeApi().on("GET", EP_PROFILE, LOGIN_ERR)
        client = make_client([remix("a"), remix("b")], api)
        await client.ensure_login()
        assert len(api.calls) == 2


# ---------- 域名池接入 ----------


class TestDomainRouting:
    """带账号凭据的请求只发往可信域名；学习到的域名只服务匿名请求。"""

    LEARNED = "https://learned.example"

    @pytest.fixture(autouse=True)
    def _no_dns(self, monkeypatch):
        monkeypatch.setattr(
            zlib_security, "_resolve_host_ips", lambda h: ["93.184.216.34"]
        )

    def _learned_only_ok(self, client):
        assert client.domains.learn(["learned.example"]) == 1
        client.domains.max_attempts = 20

        def answer(call):
            if call["base_url"] == self.LEARNED:
                return ok(books=[])
            return ZlibError("network_error", "down")

        return answer

    @pytest.mark.asyncio
    async def test_anonymous_request_may_use_learned_domain(self, make_client):
        client = make_client()
        client.api.on("POST", EP_SEARCH, self._learned_only_ok(client))
        assert (await client.search("x")).books == []
        assert client.api.calls[-1]["base_url"] == self.LEARNED

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("account_factory", "method", "path"),
        [(remix, "GET", EP_PROFILE), (pwd, "POST", EP_LOGIN)],
        ids=["remix", "password"],
    )
    async def test_login_never_sent_to_learned_domain(
        self, make_client, account_factory, method, path
    ):
        client = make_client([account_factory()])
        client.api.on(method, path, self._learned_only_ok(client))
        await client.ensure_login()
        assert client.accounts[0].logged_in is False
        assert {c["base_url"] for c in client.api.calls} == set(BUILTIN)

    @pytest.mark.asyncio
    async def test_download_link_request_never_sent_to_learned_domain(
        self, make_client
    ):
        book = make_book(1)
        client = make_client([remix(logged_in=True)])
        client.api.on("GET", file_path(book), self._learned_only_ok(client))
        with pytest.raises(ZlibError) as exc:
            await client.get_download_link(client.accounts[0], book)
        assert exc.value.category == "all_domains_failed"
        assert self.LEARNED not in {c["base_url"] for c in client.api.calls}

    @pytest.mark.asyncio
    async def test_discover_domains_learns_valid_and_swallows_failure(
        self, make_client
    ):
        resp = ok(
            domains=[
                {"domain": "fresh.example"},
                {"domain": "127.0.0.1"},
                "junk",
                {"x": 1},
            ]
        )
        api = FakeApi().on("GET", EP_DOMAINS, resp, ZlibError("domain_invalid", "x"))
        client = make_client(api=api)
        assert await client.discover_domains() == 1
        assert await client.discover_domains() == 0


# ---------- 搜索 ----------


class TestSearch:
    @pytest.mark.asyncio
    async def test_request_payload_and_result(self, make_client):
        resp = ok(
            books=[api_book(1), api_book(2)],
            pagination={"current": 2, "total_pages": 5, "total_items": 99},
        )
        api = FakeApi().on("POST", EP_SEARCH, resp)
        result = await make_client(api=api).search(
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

    @pytest.mark.asyncio
    async def test_page_floor_and_optional_filters_omitted(self, make_client):
        api = FakeApi().on("POST", EP_SEARCH, ok(books=[]))
        await make_client(api=api).search("x", page=0)
        data = api.calls[0]["data"]
        assert data["page"] == 1
        assert "languages[]" not in data and "extensions[]" not in data

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("given", "sent"), [(0, 1), (500, 50)])
    async def test_limit_clamped(self, make_client, given, sent):
        api = FakeApi().on("POST", EP_SEARCH, ok(books=[]))
        await make_client(api=api).search("x", limit=given)
        assert api.calls[0]["data"]["limit"] == sent

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
    async def test_non_auth_errors_not_retried(self, make_client):
        api = FakeApi().on("POST", EP_SEARCH, biz_error("boom"))
        client = make_client([remix(logged_in=True)], api)
        with pytest.raises(ZlibError) as exc:
            await client.search("x")
        assert exc.value.category == "api_error"
        assert len(api.calls) == 1


class TestSearchAuthRetry:
    @pytest.mark.asyncio
    async def test_logs_in_then_retries_with_account_cookies(self, make_client):
        api = (
            FakeApi()
            .on(
                "POST",
                EP_SEARCH,
                biz_error("Please login", 400),
                ok(books=[api_book(1)]),
            )
            .on("GET", EP_PROFILE, profile(key=SECRET_KEY))
        )
        client = make_client([remix()], api)
        result = await client.search("x")
        assert [b.id for b in result.books] == [1]
        assert api.paths() == [
            ("POST", EP_SEARCH),
            ("GET", EP_PROFILE),
            ("POST", EP_SEARCH),
        ]
        first, _, retry = api.calls
        assert first["cookies"] == {}
        assert retry["cookies"] == REMIX_COOKIES

    @pytest.mark.asyncio
    @pytest.mark.parametrize("accounts", [[], [remix()]], ids=["none", "login_fails"])
    async def test_no_usable_account_raises_auth_failed(self, make_client, accounts):
        api = (
            FakeApi()
            .on("POST", EP_SEARCH, biz_error("Please login", 403))
            .on("GET", EP_PROFILE, LOGIN_ERR)
        )
        client = make_client(accounts, api)
        with pytest.raises(ZlibError) as exc:
            await client.search("x")
        assert exc.value.category == "auth_failed"
        assert len([p for p in api.paths() if p == ("POST", EP_SEARCH)]) == 1

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
    @pytest.mark.parametrize(
        ("description", "expected"), [("a.pdf (x)", "a.pdf"), ("N (x)", "N.pdf")]
    )
    async def test_extension_not_duplicated(self, make_client, description, expected):
        resp = download_link_response(description=description, extension="pdf")
        (_, name), _ = await self._link(make_client, resp)
        assert name == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("extension", "suffix"),
        [
            ("pdf/../../evil", ".pdfevil"),
            ("", ".epub"),
            ("a" * 20, "." + "a" * 8),
        ],
    )
    async def test_extension_whitelisted(self, make_client, extension, suffix):
        """远端 extension 是路径注入入口，只允许字母数字且限长。"""
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
        ["../../etc/passwd (x)", "a/b\\c:d*e?f (x)"],
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
    async def test_cap_boundary_and_zero_config_floor(self, make_client, monkeypatch):
        client = make_client()
        client.max_download_bytes = 1000
        fake_fetch(client, monkeypatch, b"x" * 1000, chunk_size=64)
        assert len(await client._fetch_file(self.URL)) == 1000
        # 配置成 0 MB 时下限保底 1 MiB，否则任何文件都会被判超限
        assert make_client(max_download_mb=0).max_download_bytes == 1024 * 1024

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

    @pytest.mark.asyncio
    async def test_sink_receives_every_chunk_with_filename_and_progress(
        self, make_client, monkeypatch
    ):
        client = make_client()
        body = bytes(range(256)) * 4
        fake_fetch(client, monkeypatch, body, chunk_size=300)
        got, progress = [], []

        async def sink(name, chunk):
            got.append((name, chunk))

        async def on_progress(read, total):
            progress.append(read)

        out = await client._fetch_file(
            self.URL, filename="b.pdf", sink=sink, on_progress=on_progress
        )
        assert out == b""  # 给了 sink 就不在内存里再留一份
        assert {n for n, _ in got} == {"b.pdf"}
        assert b"".join(c for _, c in got) == body
        assert progress == sorted(progress) and progress[-1] == len(body)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("size", [0, 5000], ids=["empty", "over_cap"])
    async def test_sink_path_still_enforces_cap_and_non_empty(
        self, make_client, monkeypatch, size
    ):
        client = make_client()
        client.max_download_bytes = 1000
        fake_fetch(client, monkeypatch, b"x" * size, chunk_size=64)

        async def sink(name, chunk):
            return None

        with pytest.raises(ZlibError):
            await client._fetch_file(self.URL, filename="f", sink=sink)

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
    async def test_success_and_quota_refreshed_from_server(
        self, make_client, monkeypatch
    ):
        book = make_book(1)
        client = make_client([remix(logged_in=True)])
        self._wire(client, monkeypatch, book)
        client.api.on("GET", EP_PROFILE, profile(today=7, limit=10))
        account, filename, content = await client.download(book)
        assert (account.name, filename, content) == ("r", "My Book.pdf", b"CONTENT")
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
    async def test_account_selection_prefers_quota_and_skips_full(
        self, make_client, monkeypatch
    ):
        book = make_book(1)
        client = make_client(
            [
                remix("full", logged_in=True, downloads_today=10),
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
        assert expired.logged_in is False and expired.last_error

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
    @pytest.mark.parametrize(
        ("accounts", "path", "category"),
        [
            ([], None, "auth_failed"),
            ([remix()], ("GET", EP_PROFILE), "auth_failed"),
            ([remix(logged_in=True, downloads_today=10)], None, "quota_exhausted"),
        ],
        ids=["no_accounts", "cannot_log_in", "all_over_quota"],
    )
    async def test_no_usable_account(self, make_client, accounts, path, category):
        client = make_client(accounts)
        if path:
            client.api.on(*path, LOGIN_ERR)
        with pytest.raises(ZlibError) as exc:
            await client.download(make_book(1))
        assert exc.value.category == category

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
    @pytest.mark.parametrize("book_id", [11, 99])
    async def test_download_by_id(self, make_client, monkeypatch, book_id):
        book = make_book(11)
        client = make_client([remix(logged_in=True)])
        client.book_cache.put_many([book])
        self._wire(client, monkeypatch, book)
        client.api.on("GET", EP_PROFILE, profile())
        if book_id == 99:
            with pytest.raises(ZlibError, match="id=99"):
                await client.download_by_id(book_id)
        else:
            assert (await client.download_by_id(book_id))[1] == "My Book.pdf"


# ---------- 封面 ----------


def _png_bytes(w=60, h=90):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "blue").save(buf, "PNG")
    return buf.getvalue()


PNG = _png_bytes()


class TestFetchCover:
    COVER = "https://8.8.8.8/c.png"

    @pytest.mark.asyncio
    async def test_embeds_resized_jpeg_data_uri_with_referer(
        self, make_client, monkeypatch
    ):
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
    @pytest.mark.parametrize(
        ("body", "status", "chunk_size", "max_bytes"),
        [
            (None, 200, None, 1024 * 1024),  # 安全校验拒绝
            (PNG, 404, None, 1024 * 1024),  # 非 200
            (PNG, 200, 100, 100),  # 超过 max_bytes
            (b"", 200, None, 1024 * 1024),  # 空响应
            (b"<html>not an image</html>", 200, None, 1024 * 1024),
        ],
        ids=["blocked", "non_200", "oversized", "empty", "undecodable"],
    )
    async def test_failures_degrade_to_empty(
        self, make_client, monkeypatch, body, status, chunk_size, max_bytes
    ):
        """封面是锦上添花：任何失败都返回空串（卡片用占位），绝不抛也不能返回原图。"""
        client = make_client()
        fake_fetch(client, monkeypatch, body, status=status, chunk_size=chunk_size)
        assert await client.fetch_cover_data_uri(self.COVER, max_bytes=max_bytes) == ""

    @pytest.mark.asyncio
    async def test_empty_url_makes_no_request(self, make_client, monkeypatch):
        client = make_client()

        async def boom(*a, **k):
            raise AssertionError("不应发起请求")

        monkeypatch.setattr(client.transport, "guarded_get", boom)
        assert await client.fetch_cover_data_uri("") == ""


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
        assert (rows[1]["login_mode"], rows[1]["last_error"]) == ("password", "bad")

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


class TestSharedProbe:
    """冷启动时由第一个调用方探测整个域名池，结果全局共享。"""

    @staticmethod
    def probe_count(api):
        return sum(1 for c in api.calls if c["path"] == EP_SEARCH)

    @pytest.fixture
    def pool_clock(self, clock):
        # 域名池自带时钟（zlib_domains.time），与 zlib_client 的时钟分开注入
        return clock

    @pytest.mark.asyncio
    async def test_cold_pool_is_probed_once_then_served_from_state(
        self, make_client, pool_clock
    ):
        api = FakeApi().on("POST", EP_SEARCH, lambda c: ok(books=[]))
        client = make_client(api=api)
        client.domains._clock = pool_clock
        await client.ensure_probed()
        assert self.probe_count(api) == len(BUILTIN)
        assert client.domains.has_recent_ok(PROBE_TTL)
        await client.ensure_probed()
        assert self.probe_count(api) == len(BUILTIN)

    @pytest.mark.asyncio
    async def test_concurrent_callers_share_a_single_probe(
        self, make_client, pool_clock
    ):
        api = FakeApi().on("POST", EP_SEARCH, lambda c: ok(books=[]))
        client = make_client(api=api)
        client.domains._clock = pool_clock
        await asyncio.gather(*(client.ensure_probed() for _ in range(5)))
        assert self.probe_count(api) == len(BUILTIN)

    @pytest.mark.asyncio
    async def test_stale_pool_is_probed_again(self, make_client, pool_clock):
        api = FakeApi().on("POST", EP_SEARCH, lambda c: ok(books=[]))
        client = make_client(api=api)
        client.domains._clock = pool_clock
        await client.ensure_probed()
        pool_clock.now += PROBE_TTL + 1
        await client.ensure_probed()
        assert self.probe_count(api) == 2 * len(BUILTIN)

    @pytest.mark.asyncio
    async def test_unreachable_pool_is_not_reprobed_within_min_interval(
        self, make_client, pool_clock
    ):
        api = FakeApi().on(
            "POST", EP_SEARCH, lambda c: ZlibError("network_error", "down")
        )
        client = make_client(api=api)
        client.domains._clock = pool_clock
        await client.ensure_probed()
        await client.ensure_probed()
        assert self.probe_count(api) == len(BUILTIN)
        pool_clock.now += PROBE_MIN_INTERVAL + 1
        await client.ensure_probed()
        assert self.probe_count(api) == 2 * len(BUILTIN)

    @pytest.mark.asyncio
    async def test_probe_survives_a_cancelled_caller(self, make_client, pool_clock):
        client = make_client()
        client.domains._clock = pool_clock
        gate = asyncio.Event()

        async def slow_probe(base_url):
            await gate.wait()

        client._probe_domain = slow_probe
        caller = asyncio.create_task(client.ensure_probed())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        gate.set()
        await client._probe_task
        assert not client._probe_task.cancelled()
        assert client.domains.has_recent_ok(PROBE_TTL)

    @pytest.mark.asyncio
    async def test_slow_mirror_counts_as_network_error(
        self, make_client, pool_clock, monkeypatch
    ):
        client = make_client()
        client.domains._clock = pool_clock

        async def hang(*args, **kwargs):
            await asyncio.sleep(30)

        monkeypatch.setattr(client.transport, "request_json", hang)
        monkeypatch.setattr(zlib_client, "PROBE_TIMEOUT", 0.01)
        await client.ensure_probed()
        rows = client.domains.snapshot()
        assert {r["last_category"] for r in rows} == {"network_error"}
        assert all(r["cooling"] for r in rows)

    @pytest.mark.asyncio
    async def test_close_cancels_an_inflight_probe(self, make_client, pool_clock):
        client = make_client()
        client.domains._clock = pool_clock
        gate = asyncio.Event()

        async def slow_probe(base_url):
            await gate.wait()

        client._probe_domain = slow_probe
        caller = asyncio.create_task(client.ensure_probed())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await client.close()
        with pytest.raises(asyncio.CancelledError):
            await caller


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

    def test_account_status_has_no_credentials(self, make_client):
        text = str(make_client([remix(), pwd()]).account_status())
        assert SECRET_KEY not in text and SECRET_PW not in text
