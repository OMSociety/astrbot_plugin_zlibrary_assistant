"""测试夹具：把插件以命名空间包方式挂进 sys.path。

插件根目录无 __init__.py（AstrBot 以目录名整包加载），因此按 PEP 420
命名空间包导入：sys.path 需含插件父目录（解析 astrbot_plugin_zlibrary_assistant）
与 AstrBot 源码根（解析 astrbot 包）。
"""

import os
import sys

_WORKSPACE = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_ASTRBOT = os.path.join(_WORKSPACE, "AstrBot")

for p in (_WORKSPACE, _ASTRBOT):
    if p not in sys.path:
        sys.path.insert(0, p)
