"""ZLibrary Assistant - AstrBot 插件入口。

用户指令（/zlib 命令组）：搜索、详情、下载、下一页、上一页、状态、诊断、帮助。
LLM 工具：zlib_search_books、zlib_download_book、zlib_get_status。
指令与工具共用 ZlibService，两条入口互不掺和：指令侧一切由指令驱动，
工具的交付（卡片、文件）由插件直接发出，不要求模型转述路径。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.message.components import File, Image

from .tools.common import clamp_deadline
from .tools.download_tool import ZlibDownloadBookTool
from .tools.search_tool import ZlibSearchBooksTool
from .tools.status_tool import ZlibGetStatusTool
from .zlib_client import ZlibClient
from .zlib_domains import normalize_base_url
from .zlib_errors import ZlibError
from .zlib_format import (
    HELP_TEXT,
    ArgError,
    format_diagnosis,
    format_status,
    parse_search_args,
    strip_command,
)
from .zlib_models import Account, book_detail_lines
from .zlib_paths import plugin_data_dir
from .zlib_service import ZlibService, format_book_lines

COMMAND_PREFIX = "/zlib"

# 子指令名与别名：参数解析靠它从原始消息里剥掉“组名 + 子指令”
_SUB_SEARCH = {"搜索", "搜", "search", "s"}
_SUB_DETAIL = {"详情", "详", "detail", "d"}
_SUB_DOWNLOAD = {"下载", "下", "download", "dl"}
_SUB_NEXT = {"下一页", "下页", "next", "n"}
_SUB_PREV = {"上一页", "上页", "prev", "p"}
_SUB_STATUS = {"状态", "status"}
_SUB_DIAG = {"诊断", "diag"}
_SUB_HELP = {"帮助", "help"}

# 后台预热的重试节奏：网络刚起来时首次探测常失败，有限次重试避免永久停在未就绪
_WARMUP_DELAYS = (0, 10, 20, 30, 45, 60)


def parse_accounts(config: AstrBotConfig) -> list[Account]:
    """把配置中的账号池（template_list：每项一个账号）解析成账号对象。"""
    raw = config.get("accounts") or []
    accounts: list[Account] = []
    if not isinstance(raw, list):
        return accounts
    for i, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        accounts.append(
            Account(
                name=str(item.get("label") or f"账号{i}"),
                email=str(item.get("email") or "").strip(),
                password=str(item.get("password") or ""),
                remix_userid=str(item.get("remix_userid") or "").strip(),
                remix_userkey=str(item.get("remix_userkey") or "").strip(),
            )
        )
    return accounts


def parse_domains(config: AstrBotConfig) -> list[str]:
    """读取用户补充的镜像域名；格式不合法的条目记录后跳过。"""
    raw = config.get("domains") or []
    domains: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        try:
            domains.append(normalize_base_url(str(item)))
        except ValueError as e:
            logger.warning(f"忽略无效的镜像域名配置 {str(item)[:60]!r}: {e}")
    return domains


class ZLibraryAssistantPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        data_dir = Path(plugin_data_dir())
        self.client = ZlibClient(
            parse_accounts(config),
            domains=parse_domains(config),
            proxy=str(config.get("proxy") or "").strip(),
            max_download_mb=int(config.get("max_download_mb", 80) or 80),
            cache_path=data_dir / "book_cache.json",
        )
        self.service = ZlibService(
            self.client,
            data_dir=data_dir,
            renderer=self._render,
            search_limit=int(config.get("search_limit", 5) or 5),
            card_enabled=bool(config.get("card_enabled", True)),
            command_prefix=COMMAND_PREFIX,
        )
        self.download_admin_only = bool(config.get("download_admin_only", False))
        self.search_timeout = clamp_deadline(config.get("search_timeout"))
        self._warmup_task: asyncio.Task | None = None
        self.context.add_llm_tools(
            ZlibSearchBooksTool(service=self.service, deadline=self.search_timeout),
            ZlibDownloadBookTool(
                service=self.service, admin_only=self.download_admin_only
            ),
            ZlibGetStatusTool(client=self.client, deadline=self.search_timeout),
        )
        logger.info("ZLibrary Assistant 已加载：/zlib 指令组与 3 个 LLM 工具")

    async def _render(self, tmpl: str, data: dict, options: dict, umo: str) -> str:
        return await self.html_render(
            tmpl, data, return_url=False, options=options, umo=umo
        )

    # ---------- 生命周期 ----------

    async def initialize(self):
        """后台预热：发现镜像域名并登录账号池（失败不阻塞启动）。"""
        if self._warmup_task is not None and not self._warmup_task.done():
            return
        self._warmup_task = asyncio.create_task(self._warmup())

    async def _warmup(self):
        for attempt, delay in enumerate(_WARMUP_DELAYS, start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                await self.client.ensure_probed()
                await self.client.discover_domains()
                await self.client.ensure_login()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - 后台预热失败不应影响插件可用性
                if attempt == len(_WARMUP_DELAYS):
                    logger.warning(f"ZLibrary 后台预热失败: {e}")
                    return
                logger.info(
                    f"ZLibrary 尚未就绪，{_WARMUP_DELAYS[attempt]} 秒后重试: {e}"
                )

    async def terminate(self):
        """插件卸载/重载时取消预热任务并关闭网络会话。"""
        if self._warmup_task is not None and not self._warmup_task.done():
            self._warmup_task.cancel()
            try:
                await self._warmup_task
            except asyncio.CancelledError:
                logger.debug("后台预热任务已取消")
            except Exception as e:  # noqa: BLE001 - 关闭阶段异常不再向上抛
                logger.debug(f"后台预热任务异常结束: {e}")
        await self.client.close()

    # ---------- 指令 ----------

    @filter.command_group("zlib", alias={"zl"})
    def zlib(self):
        """Z-Library 图书搜索与下载"""

    @zlib.command("搜索", alias=_SUB_SEARCH)
    async def cmd_search(self, event: AstrMessageEvent):
        """搜索图书：搜索 关键词 [-l 语言] [-e 格式] [-n 条数]"""
        try:
            args = parse_search_args(strip_command(event.message_str, _SUB_SEARCH))
        except ArgError as e:
            yield event.plain_result(str(e))
            return
        async for r in self._search_reply(
            event,
            self.service.search(
                event.unified_msg_origin,
                args.query,
                limit=args.limit,
                language=args.language,
                extension=args.extension,
            ),
        ):
            yield r

    @zlib.command("详情", alias=_SUB_DETAIL)
    async def cmd_detail(self, event: AstrMessageEvent):
        """查看某本书的完整信息：详情 序号"""
        try:
            book = self.service.resolve_book(
                event.unified_msg_origin,
                strip_command(event.message_str, _SUB_DETAIL),
            )
        except ZlibError as e:
            yield event.plain_result(e.message)
            return
        yield event.plain_result("\n".join(book_detail_lines(book)))

    @zlib.command("下一页", alias=_SUB_NEXT)
    async def cmd_next(self, event: AstrMessageEvent):
        """搜索结果下一页"""
        async for r in self._search_reply(
            event, self.service.turn_page(event.unified_msg_origin, +1)
        ):
            yield r

    @zlib.command("上一页", alias=_SUB_PREV)
    async def cmd_prev(self, event: AstrMessageEvent):
        """搜索结果上一页"""
        async for r in self._search_reply(
            event, self.service.turn_page(event.unified_msg_origin, -1)
        ):
            yield r

    async def _search_reply(self, event: AstrMessageEvent, pending):
        try:
            outcome = await asyncio.wait_for(pending, self.search_timeout)
        except asyncio.TimeoutError:
            yield event.plain_result(
                f"搜索超时：镜像在 {self.search_timeout:.0f} 秒内没有返回结果，"
                "稍后重试、换个关键词，或在插件配置里调大 search_timeout。"
            )
            return
        except ZlibError as e:
            yield event.plain_result(f"{e.message}")
            return
        result = outcome.result
        if not result.books:
            yield event.plain_result(
                f"未找到与「{result.query}」相关的图书，换个关键词试试。"
            )
            return
        if outcome.card_path:
            yield event.chain_result([Image.fromFileSystem(outcome.card_path)])
            return
        lines = format_book_lines(result.books)
        footer = (
            f"第 {result.page}/{result.total_pages} 页，共 {result.total_items} 条。"
            f"回复 {COMMAND_PREFIX} 详情 序号 看简介，"
            f"{COMMAND_PREFIX} 下载 序号 取文件"
        )
        if result.has_next:
            footer += f"；{COMMAND_PREFIX} 下一页 翻页"
        yield event.plain_result("\n".join(lines) + "\n" + footer)

    @zlib.command("下载", alias=_SUB_DOWNLOAD)
    async def cmd_download(self, event: AstrMessageEvent):
        """下载图书：下载 序号（或书的 id）"""
        async for r in self._download_reply(
            event, strip_command(event.message_str, _SUB_DOWNLOAD)
        ):
            yield r

    async def _download_reply(self, event: AstrMessageEvent, ref: str):
        if self.download_admin_only and not event.is_admin():
            yield event.plain_result("下载仅管理员可用。")
            return
        try:
            book = self.service.resolve_book(event.unified_msg_origin, ref)
        except ZlibError as e:
            yield event.plain_result(e.message)
            return
        started = time.monotonic()
        yield event.plain_result(f"正在下载《{book.title or book.id}》，请稍候…")
        try:
            done = await self.service.download(book)
        except ZlibError as e:
            yield event.plain_result(f"下载失败：{e.message}")
            return
        yield event.chain_result([File(name=done.filename, file=str(done.path))])
        yield event.plain_result(
            f"已发送 {done.filename}（{done.size / 1024 / 1024:.1f} MB，"
            f"耗时 {time.monotonic() - started:.1f} 秒），"
            f"账号池今日剩余 {done.pool_left} 次"
        )

    @zlib.command("状态", alias=_SUB_STATUS)
    async def cmd_status(self, event: AstrMessageEvent):
        """查看账号与额度（管理员）"""
        if not event.is_admin():
            yield event.plain_result("账号状态仅管理员可查。")
            return
        note = ""
        try:
            await asyncio.wait_for(self.client.ensure_login(), self.search_timeout)
        except asyncio.TimeoutError:
            note = "登录检查超时（镜像不稳），以下为本地记录的状态。\n"
        yield event.plain_result(note + format_status(self.client.account_status()))

    @zlib.command("诊断", alias=_SUB_DIAG)
    async def cmd_diagnose(self, event: AstrMessageEvent):
        """探测镜像域名与账号状态（管理员）"""
        if not event.is_admin():
            yield event.plain_result("诊断仅管理员可用。")
            return
        yield event.plain_result("正在探测镜像域名，请稍候…")
        diag = await self.client.diagnose()
        yield event.plain_result(format_diagnosis(diag))

    @zlib.command("帮助", alias=_SUB_HELP)
    async def cmd_help(self, event: AstrMessageEvent):
        """查看用法"""
        yield event.plain_result(HELP_TEXT.format(p=COMMAND_PREFIX))
