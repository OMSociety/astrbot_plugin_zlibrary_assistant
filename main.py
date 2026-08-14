"""ZLibrary Assistant - AstrBot 插件入口。

本插件为 LLM 提供 Z-Library 图书搜索与下载工具（FunctionTool），
不注册任何用户手动指令；LLM 在对话中自行判断何时调用工具。

工具：
- zlib_search_books   关键词搜索，返回 HTML 卡片图片（封面/标题/作者/格式）
- zlib_download_book  按 id 下载书籍（账号池轮换，额度管控）
- zlib_get_status     账号池状态与下载额度查询
"""

import asyncio

from astrbot.api import AstrBotConfig, logger
from astrbot.api.star import Context, Star, register

from .tools.download_tool import ZlibDownloadBookTool
from .tools.search_tool import ZlibSearchBooksTool
from .tools.status_tool import ZlibGetStatusTool
from .zlib_client import ZlibClient


def _parse_accounts(config: AstrBotConfig) -> list[dict]:
    """把配置中的账号池（template_list：数组，每项一个账号）解析成账号列表。"""
    raw = config.get("accounts") or []
    accounts = []
    if isinstance(raw, list):
        for acc in raw:
            if not isinstance(acc, dict):
                continue
            item = {"name": str(acc.get("name", "account"))}
            item.update({k: v for k, v in acc.items() if k != "__template_key" and v is not None})
            accounts.append(item)
    elif isinstance(raw, dict):
        # 兼容旧结构（键=备注名，值=账号配置）
        for name, acc in raw.items():
            if not isinstance(acc, dict):
                continue
            item = {"name": str(name)}
            item.update({k: v for k, v in acc.items() if v is not None})
            accounts.append(item)
    return accounts


@register(
    "astrbot_plugin_zlibrary_assistant",
    "OMSociety",
    "Z-Library 图书搜索与下载工具（LLM 自动调用，账号池，HTML 卡片结果）",
    "0.1.0",
)
class ZLibraryAssistantPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.client = ZlibClient(
            accounts=_parse_accounts(config),
            domain=config.get("domain", "z-library.sk"),
            proxy=config.get("proxy", ""),
        )
        # 注册 LLM 工具（>= v4.5.1 的标准方式）
        self.context.add_llm_tools(
            ZlibSearchBooksTool(client=self.client),
            ZlibDownloadBookTool(client=self.client),
            ZlibGetStatusTool(client=self.client),
        )
        logger.info("ZLibrary Assistant 插件已加载，已注册 3 个 LLM 工具")

    async def initialize(self):
        """插件初始化：后台预登录账号池（失败不阻塞启动）。"""
        asyncio.create_task(self._background_login())

    async def _background_login(self):
        try:
            await self.client.login_all()
            logger.info("ZLibrary 账号池登录完成")
        except Exception as e:
            logger.warning(f"ZLibrary 账号池后台登录失败: {e}")

    async def terminate(self):
        """插件卸载时关闭网络会话。"""
        await self.client.close()
