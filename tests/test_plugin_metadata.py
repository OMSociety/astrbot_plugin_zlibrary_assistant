"""插件元数据与路径常量的一致性校验。"""

from __future__ import annotations

import os
import re

from astrbot_plugin_zlibrary_assistant import zlib_paths

_METADATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "metadata.yaml"
)


def test_plugin_name_matches_metadata_name() -> None:
    """PLUGIN_NAME 必须与 metadata.yaml 的 name 一致，避免数据目录漂移。"""
    with open(_METADATA, encoding="utf-8") as f:
        text = f.read()
    match = re.search(r"^name:\s*(\S+)\s*$", text, re.MULTILINE)
    assert match is not None, "metadata.yaml 缺少 name 字段"
    assert match.group(1) == zlib_paths.PLUGIN_NAME
