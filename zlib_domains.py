"""镜像域名池：候选排序、冷却、故障转移与探测。

官方主域（z-library.sk 等）前置 DiamWall 验证，纯 HTTP 客户端无法通过；
部分镜像域名（见 BUILTIN_MIRRORS）直接暴露 E-API。域名池按"用户配置 >
已验证可用（按延迟）> 内置镜像 > 从接口学到的域名"排序，失败的域名按失败类别
进入冷却，期间不再尝试。
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar
from urllib.parse import urlparse

from astrbot.api import logger

from .zlib_errors import DOMAIN_FAILOVER_CATEGORIES, ZlibError
from .zlib_security import is_safe_public_url

T = TypeVar("T")

# 内置域名：前四个是官方入口，优先尝试（部分出口 IP 能通过其浏览器验证，不能通过的
# 会按 DiamWall 冷却后自动转到镜像）；其后是实测直连返回 E-API JSON 的镜像。
# 域名会随站点变化，失效时靠 `domains` 配置项与接口学习补充
BUILTIN_MIRRORS: tuple[str, ...] = (
    "z-library.sk",
    "z-lib.sk",
    "z-library.im",
    "z-library.hn",
    "z-lib.by",
    "z-library.website",
    "z-lib.gd",
    "z-lib.gl",
    "article.sk",
    "articles.sk",
    "z-library.qa",
    "zlib.bz",
)

# 各失败类别的冷却秒数：DiamWall / TLS 属于稳定的结构性问题，冷却久；网络抖动与限流冷却短
COOLDOWN_SECONDS = {
    "diamwall_blocked": 1800.0,
    "cloudflare_blocked": 900.0,
    "tls_error": 1800.0,
    "domain_invalid": 300.0,
    "network_error": 60.0,
    "rate_limited": 45.0,
}
_DEFAULT_COOLDOWN = 120.0

# 限流后换域名前的退避（秒）；同一出口 IP 的限流换域名通常无效，先缓一缓
_RATE_LIMIT_BACKOFF = 2.0

MAX_ATTEMPTS = 8
MAX_LEARNED = 8

TIER_USER, TIER_BUILTIN, TIER_LEARNED = 0, 1, 2

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$"
)


def normalize_base_url(value: str) -> str:
    """规范化域名配置；onion 默认 HTTP，普通域名默认 HTTPS。

    只接受协议 + 主机（+ 端口），不接受路径、查询或片段。
    """
    raw = (value or "").strip()
    if not raw:
        raise ValueError("域名不能为空")
    if "://" not in raw:
        onion = raw.rstrip("/").lower().endswith(".onion")
        raw = ("http://" if onion else "https://") + raw
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("域名必须是主机名或 http(s):// URL")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("域名只能包含协议和主机，不能带路径、查询或片段")
    return f"{parsed.scheme}://{parsed.netloc}".lower()


@dataclass
class DomainState:
    base_url: str
    tier: int
    index: int
    ok_count: int = 0
    fail_count: int = 0
    latency: float | None = None
    last_category: str = ""
    cooldown_until: float = 0.0
    last_ok: float = 0.0


@dataclass
class ProbeReport:
    base_url: str
    tier: int
    ok: bool
    category: str = ""
    message: str = ""
    latency_ms: int | None = None
    cooling: bool = False
    extra: dict = field(default_factory=dict)


class DomainPool:
    """镜像域名池。不做网络请求，请求逻辑由调用方以回调注入。"""

    def __init__(
        self,
        user_domains: list[str] | tuple[str, ...] = (),
        *,
        allow_onion: bool = False,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_attempts: int = MAX_ATTEMPTS,
    ):
        self._clock = clock
        self._sleep = sleep
        self._allow_onion = allow_onion
        self.max_attempts = max_attempts
        self._states: dict[str, DomainState] = {}
        self._probe_locks: dict[str, asyncio.Lock] = {}
        for raw in user_domains:
            if not str(raw).strip():
                continue
            self._add(normalize_base_url(str(raw)), TIER_USER, check_onion=True)
        for raw in BUILTIN_MIRRORS:
            self._add(normalize_base_url(raw), TIER_BUILTIN)
        if not self._states:
            raise ValueError("域名池为空")

    def _add(self, base_url: str, tier: int, *, check_onion: bool = False) -> bool:
        if base_url in self._states:
            return False
        host = urlparse(base_url).hostname or ""
        if host.endswith(".onion") and not self._allow_onion:
            if check_onion:
                raise ValueError(
                    ".onion 域名必须配置 SOCKS 代理，例如 socks5://tor:9050"
                )
            return False
        self._states[base_url] = DomainState(base_url, tier, len(self._states))
        return True

    # ---------- 排序 ----------

    def _rank(self, state: DomainState):
        proven = 0 if state.last_ok else 1
        latency = state.latency if state.latency is not None else 1e9
        return (state.tier, proven, latency, state.index)

    def candidates(self, *, trusted_only: bool = False) -> list[str]:
        """按优先级排序的候选域名；全部冷却时退回"最先恢复"的几个。

        trusted_only=True 排除接口告知的学习域名：带账号凭证的请求只发往
        用户配置与内置镜像，避免被镜像返回的域名列表把凭证引向第三方。
        """
        now = self._clock()
        pool = [
            s
            for s in self._states.values()
            if not (trusted_only and s.tier == TIER_LEARNED)
        ]
        ready = [s for s in pool if s.cooldown_until <= now]
        if ready:
            return [s.base_url for s in sorted(ready, key=self._rank)]
        soonest = sorted(pool, key=lambda s: s.cooldown_until)
        return [s.base_url for s in soonest[: self.max_attempts]]

    @property
    def primary(self) -> str:
        return self.candidates()[0]

    # ---------- 状态更新 ----------

    def mark_ok(self, base_url: str, elapsed: float) -> None:
        state = self._states[base_url]
        state.ok_count += 1
        state.cooldown_until = 0.0
        state.last_category = ""
        state.last_ok = self._clock()
        # 指数滑动平均，抹平单次抖动
        state.latency = (
            elapsed if state.latency is None else state.latency * 0.6 + elapsed * 0.4
        )

    def mark_failed(self, base_url: str, category: str) -> None:
        state = self._states[base_url]
        state.fail_count += 1
        state.last_category = category
        state.last_ok = 0.0
        state.cooldown_until = self._clock() + COOLDOWN_SECONDS.get(
            category, _DEFAULT_COOLDOWN
        )

    def learn(self, domains) -> int:
        """登记接口告知的新域名（校验为公网主机名；与用户/内置重复者忽略）。

        学习域名只承接匿名请求（见 candidates 的 trusted_only）。
        """
        learned = sum(1 for s in self._states.values() if s.tier == TIER_LEARNED)
        added = 0
        for raw in domains:
            if learned + added >= MAX_LEARNED:
                break
            host = str(raw or "").strip().lower()
            if not _HOSTNAME_RE.match(host):
                continue
            ok, _ = is_safe_public_url(f"https://{host}", dns_check=False)
            if not ok:
                continue
            if self._add(f"https://{host}", TIER_LEARNED):
                added += 1
        return added

    # ---------- 故障转移 ----------

    async def call(
        self, op: Callable[[str], Awaitable[T]], *, trusted_only: bool = False
    ) -> T:
        """依次对候选域名执行 op；基础设施类错误换域名重试，业务错误原样抛出。"""
        failures: list[tuple[str, ZlibError]] = []
        for base_url in self.candidates(trusted_only=trusted_only)[: self.max_attempts]:
            state = self._states[base_url]
            # 尚无成功记录的域名同一时刻只放一个请求去试：并发调用排队后会看到
            # 它已进入冷却而直接跳过，避免对同一个坏域名重复等待超时
            lock = (
                None
                if state.last_ok
                else self._probe_locks.setdefault(base_url, asyncio.Lock())
            )
            if lock is not None:
                await lock.acquire()
                if state.cooldown_until > self._clock():
                    lock.release()
                    continue
            started = self._clock()
            try:
                result = await op(base_url)
            except ZlibError as e:
                if e.category not in DOMAIN_FAILOVER_CATEGORIES:
                    raise
                self.mark_failed(base_url, e.category)
                failures.append((base_url, e))
                logger.info(f"镜像域名 {base_url} 不可用（{e.category}），尝试下一个")
                if e.category == "rate_limited":
                    await self._sleep(_RATE_LIMIT_BACKOFF)
                continue
            finally:
                if lock is not None and lock.locked():
                    lock.release()
            self.mark_ok(base_url, self._clock() - started)
            return result
        summary = "；".join(
            f"{urlparse(url).hostname}: {err.category}" for url, err in failures
        )
        kinds = {err.category for _, err in failures}
        hint = (
            "所有候选域名都连不上，请检查网络或代理配置"
            if kinds <= {"network_error", "tls_error"}
            else "所有候选镜像域名均不可用，可在配置的 domains 中补充可用镜像"
        )
        raise ZlibError("all_domains_failed", hint, summary)

    # ---------- 诊断 ----------

    def snapshot(self) -> list[dict]:
        now = self._clock()
        rows = []
        for s in sorted(self._states.values(), key=self._rank):
            rows.append(
                {
                    "base_url": s.base_url,
                    "tier": s.tier,
                    "ok_count": s.ok_count,
                    "fail_count": s.fail_count,
                    "latency_ms": None if s.latency is None else int(s.latency * 1000),
                    "last_category": s.last_category,
                    "cooling": s.cooldown_until > now,
                }
            )
        return rows

    async def probe_all(
        self,
        probe: Callable[[str], Awaitable[object]],
        *,
        concurrency: int = 4,
    ) -> list[ProbeReport]:
        """并发探测全部域名，结果同时写回域名池状态。"""
        sem = asyncio.Semaphore(concurrency)

        async def one(state: DomainState) -> ProbeReport:
            async with sem:
                started = self._clock()
                try:
                    await probe(state.base_url)
                except ZlibError as e:
                    self.mark_failed(state.base_url, e.category)
                    return ProbeReport(
                        state.base_url, state.tier, False, e.category, e.message
                    )
                elapsed = self._clock() - started
                self.mark_ok(state.base_url, elapsed)
                return ProbeReport(
                    state.base_url, state.tier, True, latency_ms=int(elapsed * 1000)
                )

        reports = await asyncio.gather(*(one(s) for s in list(self._states.values())))
        order = {
            s.base_url: i
            for i, s in enumerate(sorted(self._states.values(), key=self._rank))
        }
        return sorted(reports, key=lambda r: order[r.base_url])
