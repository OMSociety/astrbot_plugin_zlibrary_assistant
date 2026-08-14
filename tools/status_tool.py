"""zlib_get_status 工具：查询账号池健康度与下载额度。"""

from __future__ import annotations

from typing import Any

from mcp.types import CallToolResult, TextContent
from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..zlib_client import ZlibClient, ZlibError


@dataclass(config=ConfigDict(arbitrary_types_allowed=True))
class ZlibGetStatusTool(FunctionTool[AstrAgentContext]):
    name: str = "zlib_get_status"
    description: str = (
        "查询 Z-Library 账号池状态：各账号登录是否正常、今日已用/剩余下载额度。"
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
