"""zlib_get_status 工具：查询账号池健康度与下载额度。"""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from mcp.types import CallToolResult, TextContent
from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass

from ..zlib_client import ZlibClient
from ..zlib_format import format_status
from .common import TOOL_DEADLINE, is_admin_context

# 非管理员看到的固定短语：账号状态含登录失败详情，只对管理员可见
ADMIN_ONLY_TEXT = "账号状态仅管理员可查。"

# 登录检查超时时的说明：状态照旧给出，但要说清可能不是最新的
STALE_NOTE = "登录检查超时（镜像不稳），以下为本地记录的状态。\n"


@dataclass(config=ConfigDict(arbitrary_types_allowed=True))
class ZlibGetStatusTool(FunctionTool[AstrAgentContext]):
    name: str = "zlib_get_status"
    description: str = (
        "查询 Z-Library 账号池状态：各账号登录是否正常、今日已用/剩余下载额度。"
        "仅管理员会话可用。"
        "当用户询问下载次数、额度、账号状态，或下载前想确认额度时调用。"
    )
    parameters: dict = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    client: ZlibClient | None = None

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:
        if not is_admin_context(context):
            return ADMIN_ONLY_TEXT
        if self.client is None:
            return "插件未初始化，请检查插件配置"
        note = ""
        try:
            await asyncio.wait_for(self.client.ensure_login(), TOOL_DEADLINE)
        except asyncio.TimeoutError:
            note = STALE_NOTE
        return CallToolResult(
            content=[
                TextContent(
                    type="text", text=note + format_status(self.client.account_status())
                )
            ],
            isError=False,
        )
