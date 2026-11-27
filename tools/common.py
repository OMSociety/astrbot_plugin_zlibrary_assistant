"""LLM 工具共用的上下文辅助。"""

from __future__ import annotations

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext


def _event(context: ContextWrapper[AstrAgentContext]):
    """取本次调用的消息事件；AstrBot 的后台/定时调用路径不带 event，返回 None。"""
    return getattr(getattr(context, "context", None), "event", None)


def is_admin_context(context: ContextWrapper[AstrAgentContext]) -> bool:
    """判定本次工具调用的发起者是否为 AstrBot 管理员；取不到信息一律按非管理员。"""
    is_admin = getattr(_event(context), "is_admin", None)
    if not callable(is_admin):
        return False
    try:
        return bool(is_admin())
    except Exception:  # noqa: BLE001 - 判定失败一律按非管理员处理
        return False


def session_key(context: ContextWrapper[AstrAgentContext]) -> str:
    """会话标识（umo）；没有事件时用固定占位，使搜索记忆仍可工作但不跨会话共享。"""
    return str(getattr(_event(context), "unified_msg_origin", "") or "tool-session")
