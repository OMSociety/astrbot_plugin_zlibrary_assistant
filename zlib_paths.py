"""插件数据目录解析。

书籍缓存与下载文件统一存放在 AstrBot 插件数据目录
``<data>/plugin_data/zlibrary_assistant/`` 下。
"""

from __future__ import annotations

import os

from astrbot.core.utils.astrbot_path import get_astrbot_data_path

# 与 metadata.yaml 的 name 及插件市场条目一致
PLUGIN_NAME = "zlibrary_assistant"


def plugin_data_dir() -> str:
    """返回本插件的数据目录（由调用方按需创建）。"""
    return os.path.join(get_astrbot_data_path(), "plugin_data", PLUGIN_NAME)
