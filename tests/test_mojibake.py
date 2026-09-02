"""_fix_mojibake 测试：Z-Library 响应的双重编码文本修复（简报 §3.4 经验）。"""

from astrbot_plugin_zlibrary_assistant.zlib_client import (
    _fix_mojibake,
    _fix_mojibake_recursive,
)


class TestFixMojibake:
    def test_double_encoded_accents_fixed(self):
        # UTF-8 字节被按 Latin-1 解码后的典型形态（作者名 Frédéric）
        assert _fix_mojibake("FrÃ©dÃ©ric") == "Frédéric"
        assert _fix_mojibake("cafÃ©") == "café"

    def test_normal_text_untouched(self):
        assert _fix_mojibake("Python 基础教程") == "Python 基础教程"
        assert _fix_mojibake("Hello World") == "Hello World"
        assert _fix_mojibake("三体") == "三体"

    def test_invalid_utf8_returns_original(self):
        # Latin-1 可编码但回不出合法 UTF-8 → 原样返回
        assert _fix_mojibake("ÿÿ") == "ÿÿ"


class TestFixMojibakeRecursive:
    def test_nested_structure(self):
        obj = {
            "author": "FrÃ©dÃ©ric",
            "books": [{"title": "cafÃ©"}, "GÃ¶the"],
            "year": 2020,  # 非字符串原样通过
        }
        fixed = _fix_mojibake_recursive(obj)
        assert fixed["author"] == "Frédéric"
        assert fixed["books"][0]["title"] == "café"
        assert fixed["books"][1] == "Göthe"
        assert fixed["year"] == 2020
