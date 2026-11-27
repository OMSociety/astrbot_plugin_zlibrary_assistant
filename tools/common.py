"""LLM 工具共用的上下文辅助与消息投递。"""

from __future__ import annotations

from astrbot.api import logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.message.message_event_result import MessageChain


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


async def deliver(
    context: ContextWrapper[AstrAgentContext], message: MessageChain
) -> bool:
    """把消息直接投递给用户，返回是否已发出。

    插件自己发而不是把路径交给模型转述：模型的上下文里不该出现文件路径，
    用户在对话里也该立刻看到结果。AstrBot 4.30 起由回合的投递策略决定
    工具能否直接发（`prepare_tool_output` 返回 False 表示宿主会代为投递），
    4.29 及更早没有这层策略，直接经消息事件发送即可。
    """
    agent_context = getattr(context, "context", None)
    event = _event(context)
    if event is None:
        return False
    should_send = True
    prepare = _prepare_tool_output()
    if prepare is not None:
        try:
            should_send = bool(prepare(agent_context, message))
        except Exception as e:  # noqa: BLE001 - 投递策略异常时退回直接发送
            logger.debug(f"工具输出投递策略不可用，改为直接发送: {e}")
            should_send = True
    if not should_send:
        return True
    try:
        await event.send(message)
    except Exception as e:  # noqa: BLE001 - 发送失败只影响这一条附件
        logger.warning(f"发送消息失败: {type(e).__name__}: {e}")
        return False
    return True


def _prepare_tool_output():
    """取 AstrBot 的投递策略函数；4.29 及更早没有它，返回 None。"""
    try:
        from astrbot.core.astr_agent_runtime.tools.output import (
            prepare_tool_output,
        )
    except Exception:  # noqa: BLE001 - 旧版本没有该模块属正常情况
        return None
    return prepare_tool_output
