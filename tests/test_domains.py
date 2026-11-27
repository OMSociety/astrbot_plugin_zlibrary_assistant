"""zlib_domains：域名规范化与 DomainPool 的排序、故障转移、冷却、学习、探测。

时间与等待全部注入（clock / sleep），不产生真实延迟。
"""

from __future__ import annotations

import pytest

from astrbot_plugin_zlibrary_assistant import zlib_security
from astrbot_plugin_zlibrary_assistant.zlib_domains import (
    BUILTIN_MIRRORS,
    COOLDOWN_SECONDS,
    MAX_LEARNED,
    TIER_LEARNED,
    DomainPool,
    normalize_base_url,
)
from astrbot_plugin_zlibrary_assistant.zlib_errors import ZlibError

BUILTIN = [f"https://{h}" for h in BUILTIN_MIRRORS]
ONION = "a" * 56 + ".onion"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_pool(user=(), **kw):
    clock = kw.pop("clock", None) or Clock()
    sleeps: list[float] = []

    async def sleep(seconds):
        sleeps.append(seconds)

    pool = DomainPool(user, clock=clock, sleep=sleep, **kw)
    pool.clock = clock
    pool.sleeps = sleeps
    return pool


def failing(category, message="x"):
    async def op(_base_url):
        raise ZlibError(category, message)

    return op


class TestNormalizeBaseUrl:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("z-lib.example", "https://z-lib.example"),
            ("HTTPS://Z-Lib.Example/", "https://z-lib.example"),
            ("http://z-lib.example:8080", "http://z-lib.example:8080"),
            (f"{ONION}", f"http://{ONION}"),
            (f"  https://{ONION}  ", f"https://{ONION}"),
        ],
    )
    def test_valid(self, raw, expected):
        assert normalize_base_url(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "ftp://x.example",
            "file:///etc/passwd",
            "https://x.example/path",
            "https://x.example?q=1",
            "https://x.example#f",
        ],
    )
    def test_invalid(self, raw):
        with pytest.raises(ValueError):
            normalize_base_url(raw)


class TestPoolConstruction:
    def test_user_domains_precede_builtin(self):
        pool = make_pool(["https://mine.example"])
        assert pool.candidates()[0] == "https://mine.example"
        assert pool.primary == "https://mine.example"
        assert pool.candidates()[1:] == BUILTIN

    def test_without_user_domains_builtin_order(self):
        assert make_pool().candidates() == BUILTIN

    def test_duplicate_user_domain_ignored(self):
        pool = make_pool(
            ["https://mine.example", "mine.example", "https://MINE.example/"]
        )
        assert len(pool.snapshot()) == 1 + len(BUILTIN)

    def test_user_domain_equal_to_builtin_not_duplicated(self):
        pool = make_pool([BUILTIN[3]])
        assert pool.candidates()[0] == BUILTIN[3]
        assert len(pool.snapshot()) == len(BUILTIN)

    def test_blank_entries_skipped(self):
        assert len(make_pool(["", "  "]).snapshot()) == len(BUILTIN)

    def test_onion_requires_socks(self):
        with pytest.raises(ValueError, match=r"\.onion"):
            make_pool([ONION])

    def test_onion_allowed_with_socks(self):
        pool = make_pool([ONION], allow_onion=True)
        assert pool.candidates()[0] == f"http://{ONION}"


class TestRanking:
    def test_proven_domain_preferred_within_tier(self):
        pool = make_pool()
        pool.mark_ok(BUILTIN[2], 0.5)
        assert pool.candidates()[0] == BUILTIN[2]

    def test_lower_latency_preferred_among_proven(self):
        pool = make_pool()
        pool.mark_ok(BUILTIN[1], 2.0)
        pool.mark_ok(BUILTIN[2], 0.1)
        assert pool.candidates()[:2] == [BUILTIN[2], BUILTIN[1]]

    def test_proven_builtin_beats_unproven_user(self):
        pool = make_pool(["https://mine.example"])
        pool.mark_ok(BUILTIN[2], 0.01)
        assert pool.candidates()[0] == BUILTIN[2]

    def test_user_tier_wins_within_same_latency_bucket(self):
        pool = make_pool(["https://mine.example"])
        pool.mark_ok(BUILTIN[0], 0.1)
        pool.mark_ok("https://mine.example", 0.3)
        assert pool.candidates()[0] == "https://mine.example"

    def test_failed_domain_loses_top_spot(self):
        pool = make_pool()
        pool.mark_ok(BUILTIN[3], 0.1)
        pool.mark_failed(BUILTIN[3], "network_error")
        assert pool.candidates()[0] != BUILTIN[3]

    def test_latency_is_smoothed(self):
        pool = make_pool()
        pool.mark_ok(BUILTIN[0], 1.0)
        pool.mark_ok(BUILTIN[0], 2.0)
        (row,) = [r for r in pool.snapshot() if r["base_url"] == BUILTIN[0]]
        assert row["latency_ms"] == 1400  # 0.6*1.0 + 0.4*2.0
        assert row["ok_count"] == 2


class TestCooldown:
    @pytest.mark.parametrize("category", sorted(COOLDOWN_SECONDS))
    def test_failed_domain_skipped_during_cooldown(self, category):
        pool = make_pool()
        pool.mark_failed(BUILTIN[0], category)
        assert BUILTIN[0] not in pool.candidates()
        pool.clock.now += COOLDOWN_SECONDS[category] - 1
        assert BUILTIN[0] not in pool.candidates()
        pool.clock.now += 2
        assert BUILTIN[0] in pool.candidates()

    def test_unknown_category_uses_default_cooldown(self):
        pool = make_pool()
        pool.mark_failed(BUILTIN[0], "weird")
        pool.clock.now += 119
        assert BUILTIN[0] not in pool.candidates()
        pool.clock.now += 2
        assert BUILTIN[0] in pool.candidates()

    def test_mark_ok_clears_cooldown(self):
        pool = make_pool()
        pool.mark_failed(BUILTIN[0], "diamwall_blocked")
        pool.mark_ok(BUILTIN[0], 0.1)
        assert BUILTIN[0] in pool.candidates()

    def test_all_cooling_falls_back_to_soonest_recovering(self):
        pool = make_pool()
        for i, host in enumerate(BUILTIN):
            pool.mark_failed(host, "network_error")
            pool.clock.now += 1
        got = pool.candidates()
        assert len(got) == pool.max_attempts
        assert got[0] == BUILTIN[0]

    def test_snapshot_reports_cooling_and_category(self):
        pool = make_pool()
        pool.mark_failed(BUILTIN[0], "cloudflare_blocked")
        row = next(r for r in pool.snapshot() if r["base_url"] == BUILTIN[0])
        assert row["cooling"] is True
        assert row["last_category"] == "cloudflare_blocked"
        assert row["fail_count"] == 1


class TestCall:
    @pytest.mark.asyncio
    async def test_first_candidate_success(self):
        pool = make_pool()
        seen = []

        async def op(url):
            seen.append(url)
            return "ok"

        assert await pool.call(op) == "ok"
        assert seen == [BUILTIN[0]]

    @pytest.mark.asyncio
    async def test_failover_in_candidate_order_and_marks_state(self):
        pool = make_pool()
        seen = []

        async def op(url):
            seen.append(url)
            if len(seen) < 3:
                raise ZlibError("network_error", "down")
            return "ok"

        assert await pool.call(op) == "ok"
        assert seen == BUILTIN[:3]
        assert BUILTIN[0] not in pool.candidates()
        assert pool.candidates()[0] == BUILTIN[2]  # 刚成功的域名升为首选

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "category",
        [
            "network_error",
            "tls_error",
            "diamwall_blocked",
            "cloudflare_blocked",
            "domain_invalid",
            "rate_limited",
        ],
    )
    async def test_infrastructure_errors_trigger_failover(self, category):
        pool = make_pool()
        calls = []

        async def op(url):
            calls.append(url)
            if len(calls) == 1:
                raise ZlibError(category, "x")
            return "ok"

        assert await pool.call(op) == "ok"
        assert len(calls) == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "category", ["auth_failed", "quota_exhausted", "api_error"]
    )
    async def test_business_errors_not_retried_and_domain_not_penalised(self, category):
        pool = make_pool()
        calls = []

        async def op(url):
            calls.append(url)
            raise ZlibError(category, "biz")

        with pytest.raises(ZlibError) as exc:
            await pool.call(op)
        assert exc.value.category == category
        assert calls == [BUILTIN[0]]
        assert BUILTIN[0] in pool.candidates()

    @pytest.mark.asyncio
    async def test_all_failed_raises_all_domains_failed_with_summary(self):
        pool = make_pool()
        with pytest.raises(ZlibError) as exc:
            await pool.call(failing("diamwall_blocked"))
        assert exc.value.category == "all_domains_failed"
        assert "domains" in exc.value.message
        assert exc.value.detail.count("diamwall_blocked") == pool.max_attempts
        assert "z-lib.by" in exc.value.detail

    @pytest.mark.asyncio
    @pytest.mark.parametrize("category", ["network_error", "tls_error"])
    async def test_connectivity_only_failures_hint_at_network(self, category):
        with pytest.raises(ZlibError) as exc:
            await make_pool().call(failing(category))
        assert "网络" in exc.value.message

    @pytest.mark.asyncio
    async def test_attempts_capped(self):
        pool = make_pool(max_attempts=2)
        calls = []

        async def op(url):
            calls.append(url)
            raise ZlibError("network_error", "x")

        with pytest.raises(ZlibError):
            await pool.call(op)
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_cooling_domains_not_retried_by_next_call(self):
        pool = make_pool(max_attempts=2)
        with pytest.raises(ZlibError):
            await pool.call(failing("network_error"))
        seen = []

        async def op(url):
            seen.append(url)
            return "ok"

        await pool.call(op)
        assert seen == [BUILTIN[2]]

    @pytest.mark.asyncio
    async def test_rate_limit_backs_off_via_injected_sleep(self):
        pool = make_pool()
        with pytest.raises(ZlibError):
            await pool.call(failing("rate_limited"))
        assert pool.sleeps == [2.0] * pool.max_attempts

    @pytest.mark.asyncio
    async def test_other_failures_do_not_sleep(self):
        pool = make_pool()
        with pytest.raises(ZlibError):
            await pool.call(failing("network_error"))
        assert pool.sleeps == []

    @pytest.mark.asyncio
    async def test_latency_recorded_from_clock(self):
        pool = make_pool()

        async def op(url):
            pool.clock.now += 0.25
            return "ok"

        await pool.call(op)
        row = next(r for r in pool.snapshot() if r["base_url"] == BUILTIN[0])
        assert row["latency_ms"] == 250


class TestLearn:
    @pytest.fixture(autouse=True)
    def _no_dns(self, monkeypatch):
        monkeypatch.setattr(
            zlib_security, "_resolve_host_ips", lambda h: ["93.184.216.34"]
        )

    def test_adds_valid_hostnames_as_learned_tier(self):
        pool = make_pool()
        assert pool.learn(["new-mirror.example", "  Other.Example  "]) == 2
        rows = {r["base_url"]: r for r in pool.snapshot()}
        assert rows["https://new-mirror.example"]["tier"] == TIER_LEARNED
        assert "https://other.example" in rows

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "localhost",
            "127.0.0.1",
            "10.0.0.1",
            "[::1]",
            "a b.example",
            "x.example/path",
            "http://x.example",
            "x.example:8080",
            "user@x.example",
            "x.e",
            "x" * 64 + ".example",
            "single",
            "x.example\nevil.example",
            None,
            123,
            ONION,
        ],
    )
    def test_rejects_non_whitelisted(self, bad):
        pool = make_pool()
        assert pool.learn([bad]) == 0
        assert len(pool.snapshot()) == len(BUILTIN)

    def test_dedupes_against_existing_and_within_batch(self):
        pool = make_pool(["https://mine.example"])
        added = pool.learn(
            ["mine.example", "z-lib.gd", "fresh.example", "FRESH.example"]
        )
        assert added == 1

    def test_capped(self):
        pool = make_pool()
        assert (
            pool.learn([f"m{i}.example" for i in range(MAX_LEARNED + 5)]) == MAX_LEARNED
        )
        assert pool.learn(["another.example"]) == 0

    def test_learned_sorted_after_trusted(self):
        pool = make_pool()
        pool.learn(["fresh.example"])
        assert pool.candidates()[-1] == "https://fresh.example"

    def test_private_resolution_not_checked_but_literal_internal_hosts_rejected(self):
        pool = make_pool()
        assert pool.learn(["169.254.169.254", "metadata.google.internal"]) == 0


class TestTrustedOnly:
    """带账号凭据的请求只能发往可信层（用户配置 + 内置），学习到的域名只服务匿名请求。"""

    @pytest.fixture(autouse=True)
    def _no_dns(self, monkeypatch):
        monkeypatch.setattr(
            zlib_security, "_resolve_host_ips", lambda h: ["93.184.216.34"]
        )

    def test_learned_present_only_for_anonymous_candidates(self):
        pool = make_pool(max_attempts=20)
        pool.learn(["fresh.example"])
        assert "https://fresh.example" in pool.candidates()
        assert "https://fresh.example" not in pool.candidates(trusted_only=True)

    def test_trusted_candidates_keep_user_and_builtin(self):
        pool = make_pool(["https://mine.example"], max_attempts=20)
        pool.learn(["fresh.example"])
        got = pool.candidates(trusted_only=True)
        assert got[0] == "https://mine.example"
        assert set(BUILTIN) <= set(got)

    def test_all_cooling_fallback_still_excludes_learned(self):
        pool = make_pool(max_attempts=20)
        pool.learn(["fresh.example"])
        for state in pool.snapshot():
            pool.mark_failed(state["base_url"], "network_error")
        got = pool.candidates(trusted_only=True)
        assert got
        assert "https://fresh.example" not in got
        assert "https://fresh.example" in pool.candidates()

    @pytest.mark.asyncio
    async def test_trusted_call_never_reaches_learned_even_when_builtin_all_fail(self):
        pool = make_pool(max_attempts=20)
        pool.learn(["fresh.example", "second.example"])
        seen = []

        async def op(url):
            seen.append(url)
            if "fresh" in url or "second" in url:
                return "LEAKED"
            raise ZlibError("network_error", "down")

        with pytest.raises(ZlibError) as exc:
            await pool.call(op, trusted_only=True)
        assert exc.value.category == "all_domains_failed"
        assert not [u for u in seen if "fresh" in u or "second" in u]
        assert set(seen) == set(BUILTIN)

    @pytest.mark.asyncio
    async def test_anonymous_call_can_fall_through_to_learned(self):
        pool = make_pool(max_attempts=20)
        pool.learn(["fresh.example"])

        async def op(url):
            if "fresh" in url:
                return url
            raise ZlibError("network_error", "down")

        assert await pool.call(op) == "https://fresh.example"


class TestProbeAll:
    @pytest.mark.asyncio
    async def test_reports_every_domain_and_updates_state(self):
        pool = make_pool(["https://mine.example"])

        async def probe(url):
            pool.clock.now += 0.2
            if url == BUILTIN[0]:
                raise ZlibError("diamwall_blocked", "blocked")

        reports = await pool.probe_all(probe, concurrency=1)
        assert len(reports) == 1 + len(BUILTIN)
        by_url = {r.base_url: r for r in reports}
        assert by_url[BUILTIN[0]].ok is False
        assert by_url[BUILTIN[0]].category == "diamwall_blocked"
        assert by_url["https://mine.example"].ok is True
        assert by_url["https://mine.example"].latency_ms == 200
        assert by_url["https://mine.example"].tier == 0
        assert BUILTIN[0] not in pool.candidates()

    @pytest.mark.asyncio
    async def test_all_failing(self):
        pool = make_pool()

        async def probe(_url):
            raise ZlibError("network_error", "down")

        reports = await pool.probe_all(probe)
        assert [r.ok for r in reports] == [False] * len(BUILTIN)
        assert {r.category for r in reports} == {"network_error"}

    @pytest.mark.asyncio
    async def test_concurrency_bounded(self):
        import asyncio

        pool = make_pool()
        running = peak = 0

        async def probe(_url):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0)
            running -= 1

        await pool.probe_all(probe, concurrency=3)
        assert peak <= 3


@pytest.mark.asyncio
class TestConcurrentProbe:
    async def test_concurrent_calls_probe_bad_domain_once(self):
        import asyncio

        pool = make_pool()
        bad = pool.candidates()[0]
        hits: list[str] = []

        async def op(base_url):
            hits.append(base_url)
            await asyncio.sleep(0)
            if base_url == bad:
                raise ZlibError("network_error", "x")
            return base_url

        results = await asyncio.gather(*(pool.call(op) for _ in range(3)))
        assert hits.count(bad) == 1
        assert len(set(results)) == 1 and results[0] != bad
