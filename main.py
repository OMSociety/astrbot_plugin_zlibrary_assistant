"""ZLibrary Assistant - AstrBot 插件入口。

本插件为 LLM 提供 Z-Library 图书搜索与下载工具（FunctionTool），
不注册任何用户手动指令；LLM 在对话中自行判断何时调用工具。

M1 阶段：最小可加载骨架。
M3 阶段：在此注册 LLM 工具（zlib_search_books / zlib_download_book / zlib_get_status）。
"""

from astrbot.api import AstrBotConfig, logger
from astrbot.api.star import Context, Star, register


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
        # TODO(M2): 初始化 zlib_client（eapi 异步客户端 + 账号池）
        # TODO(M3): 注册 LLM 工具
        #   from .tools.search_tool import ZlibSearchBooksTool
        #   from .tools.download_tool import ZlibDownloadBookTool
        #   from .tools.status_tool import ZlibGetStatusTool
        #   self.context.add_llm_tools(
        #       ZlibSearchBooksTool(client=self.client),
        #       ZlibDownloadBookTool(client=self.client),
        #       ZlibGetStatusTool(client=self.client),
        #   )
        logger.info("ZLibrary Assistant 插件已加载")

    async def initialize(self):
        """插件初始化（实例化后自动调用）。"""
        # TODO(M2): 预登录账号池，获取各账号额度
        pass

    async def terminate(self):
        """插件卸载时调用。"""
        # TODO(M2): 关闭 aiohttp 会话
        pass
