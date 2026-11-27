"""测试夹具：把插件以命名空间包方式挂进 sys.path，并提供共用的 aiohttp 桩件。

插件根目录无 __init__.py（AstrBot 以目录名整包加载），因此按 PEP 420
命名空间包导入：sys.path 需含插件父目录（解析 astrbot_plugin_zlibrary_assistant）
与 AstrBot 源码根（解析 astrbot 包）。
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

_WORKSPACE = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
# AstrBot 源码根：环境变量 ASTRBOT_SRC 优先；已 pip 安装 astrbot 时可不设
_ASTRBOT = os.environ.get("ASTRBOT_SRC", "")

for p in (_WORKSPACE, _ASTRBOT):
    if p and p not in sys.path:
        sys.path.insert(0, p)


class FakeHeaders:
    """模拟 aiohttp 的 CIMultiDict 响应头：大小写不敏感、支持同名多值。

    被测代码用 `headers.get("Location")` 与 `headers.getall("Set-Cookie", [])`
    （同名多值是真实现象，例如响应里种多个防护 cookie），普通 dict 无法表达。
    """

    def __init__(self, items=None):
        self._items: list[tuple[str, str]] = []
        if isinstance(items, dict):
            self._items = [(str(k), str(v)) for k, v in items.items()]
        elif items:
            self._items = [(str(k), str(v)) for k, v in items]

    def get(self, key, default=None):
        for name, value in self._items:
            if name.lower() == key.lower():
                return value
        return default

    def getall(self, key, default=None):
        found = [value for name, value in self._items if name.lower() == key.lower()]
        return found if found else (default if default is not None else [])

    def __contains__(self, key):
        return self.get(key) is not None


@pytest.fixture
def tmp_path():
    """每个用例独立的临时目录：直接用 tempfile，避免依赖 pytest 共享的临时根目录。"""
    with tempfile.TemporaryDirectory(prefix="zlib_test_") as d:
        yield Path(d)
