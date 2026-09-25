"""zlib_get_status 工具：查询账号池健康度与下载额度。"""

from __future__ import annotations

from typing import Any

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from mcp.types import CallToolResult, TextContent
from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass

from ..zlib_client import ZlibClient

# 非管理员看到的固定短语：账号池状态含账号邮箱与登录失败详情，只对管理员可见
_ADMIN_ONLY = "账号状态仅管理员可查。"


def _is_admin_context(context: ContextWrapper[AstrAgentContext]) -> bool:
    """判定本次工具调用的发起者是否为 AstrBot 管理员。

    AstrBot 侧的无参调用路径（如 cron/后台任务）不带 event，
    AstrAgentContext.event 可能为 None，因此逐层取属性并兜底为 False。
    """
    event = getattr(getattr(context, "context", None), "event", None)
    if event is None:
        return False
    is_admin = getattr(event, "is_admin", None)
    if not callable(is_admin):
        return False
    try:
        return bool(is_admin())
    except Exception:  # noqa: BLE001 - 判定失败一律按非管理员处理
        return False


@dataclass(config=ConfigDict(arbitrary_types_allowed=True))
class ZlibGetStatusTool(FunctionTool[AstrAgentContext]):
    name: str = "zlib_get_status"
    description: str = (
        "查询 Z-Library 账号池状态：各账号登录是否正常、今日已用/剩余下载额度。"
        "仅管理员会话可用。"
        "当用户询问下载次数、额度、账号状态，或下载前想确认额度时调用。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
        }
    )
    client: ZlibClient | None = None

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:
        if not _is_admin_context(context):
            return _ADMIN_ONLY
        if self.client is None:
            return "插件客户端未初始化，请检查插件配置"

        statuses = await self.client.get_status()
        lines = ["Z-Library 账号池状态："]
        for s in statuses:
            state = "✅ 正常" if s["logged_in"] else "❌ 登录失败"
            lines.append(
                f"- {s['name']}（{s['email'] or 'remix-key'}）{state}，"
                f"今日下载 {s['downloads_today']}/{s['downloads_limit']} 次"
            )
            if s.get("last_error"):
                lines.append(f"  最近错误：{s['last_error']}")
        if not statuses:
            lines.append("- 未配置任何账号，请在插件配置中添加")
        return CallToolResult(
            content=[TextContent(type="text", text="\n".join(lines))],
            isError=False,
        )
