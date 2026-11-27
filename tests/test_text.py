"""zlib_text：乱码修复、文件名清理、字段压平（提示注入围栏）、封面缩放（像素预算）。"""

from __future__ import annotations

import io
import struct
import zlib

import pytest
from PIL import Image

from astrbot_plugin_zlibrary_assistant import zlib_text
from astrbot_plugin_zlibrary_assistant.zlib_text import (
    FENCE_CLOSE,
    FENCE_OPEN,
    fix_mojibake,
    fix_mojibake_recursive,
    flatten_field,
    guess_image_mime,
    resize_cover,
    sanitize_filename,
)


def _png(width: int, height: int) -> bytes:
    """合成只含 IHDR/IDAT/IEND 的 PNG：头部声明任意尺寸，数据体积很小。"""

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b""))
        + chunk(b"IEND", b"")
    )


def _real_image(width: int, height: int, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(buf, fmt)
    return buf.getvalue()


class TestFixMojibake:
    def test_repairs_utf8_read_as_latin1(self):
        garbled = "资本论".encode().decode("latin-1")
        assert fix_mojibake(garbled) == "资本论"

    @pytest.mark.parametrize("text", ["资本论", "Élan vital"])
    def test_clean_text_unchanged(self, text):
        """本身就是合法 UTF-8 的文本（含 latin-1 可表示的西文）不得被改写。"""
        assert fix_mojibake(text) == text

    def test_recursive_walks_dicts_and_lists(self):
        garbled = "中文".encode().decode("latin-1")
        data = {"a": [garbled, {"b": garbled}], "n": 1}
        assert fix_mojibake_recursive(data) == {"a": ["中文", {"b": "中文"}], "n": 1}


class TestSanitizeFilename:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("a/b\\c.pdf", "a_b_c.pdf"),
            ('x:y*z?"<>|\x00.txt', "x_y_z______.txt"),
        ],
    )
    def test_forbidden_characters_replaced(self, raw, expected):
        assert sanitize_filename(raw) == expected

    @pytest.mark.parametrize("raw", ["", "..."])
    def test_empty_result_falls_back(self, raw):
        assert sanitize_filename(raw) == "book"

    def test_leading_trailing_dots_and_spaces_stripped(self):
        assert sanitize_filename(" .name.pdf. ") == "name.pdf"

    def test_chinese_name_kept(self):
        assert sanitize_filename("资本论.pdf") == "资本论.pdf"


class TestFlattenField:
    def test_newlines_flattened(self):
        assert "\n" not in flatten_field("a\nb\r\nc")
        assert "\r" not in flatten_field("a\nb\r\nc")

    def test_truncates_to_limit(self):
        assert len(flatten_field("x" * 500, limit=50)) <= 50

    @pytest.mark.parametrize(("raw", "expected"), [(None, ""), (2020, "2020")])
    def test_non_string_input_stringified(self, raw, expected):
        assert flatten_field(raw) == expected

    def test_fence_markers_removed(self):
        """书目字段不能携带围栏标记，否则可提前闭合围栏并注入指令。"""
        out = flatten_field(f"before {FENCE_OPEN} {FENCE_CLOSE} after")
        assert FENCE_OPEN not in out
        assert FENCE_CLOSE not in out

    def test_fence_marker_split_by_stripping_is_not_reassembled(self):
        out = flatten_field("<<<ZLIB_<<<ZLIB_BOOK_DATA" + "BOOK_DATA>>>>")
        assert FENCE_OPEN not in out
        assert FENCE_CLOSE not in out


class TestGuessImageMime:
    @pytest.mark.parametrize(
        ("data", "mime"),
        [
            (b"\x89PNG\r\n\x1a\nxxxx", "image/png"),
            (b"RIFFxxxxWEBPxxxx", "image/webp"),
            (b"unknown", "image/jpeg"),
        ],
    )
    def test_mime(self, data, mime):
        assert guess_image_mime(data) == mime


class TestResizeCover:
    def test_outputs_jpeg_within_max_width(self):
        out = resize_cover(_real_image(800, 1200), max_width=240)
        img = Image.open(io.BytesIO(out))
        assert img.format == "JPEG"
        assert img.width <= 240

    def test_small_image_not_enlarged(self):
        out = resize_cover(_real_image(100, 150), max_width=240)
        assert Image.open(io.BytesIO(out)).width == 100

    def test_rgba_png_converted(self):
        buf = io.BytesIO()
        Image.new("RGBA", (50, 50), (0, 0, 0, 0)).save(buf, "PNG")
        assert Image.open(io.BytesIO(resize_cover(buf.getvalue()))).format == "JPEG"

    def test_forged_header_over_pixel_budget_rejected(self):
        """头部声明超大尺寸的小文件（解压炸弹）在解码前被拒，返回空而非原图。"""
        forged = _png(60000, 60000)
        assert len(forged) < 200
        assert resize_cover(forged) == b""

    def test_pixel_budget_boundary(self, monkeypatch):
        monkeypatch.setattr(zlib_text, "MAX_COVER_PIXELS", 100 * 100)
        assert resize_cover(_real_image(100, 100)) != b""
        assert resize_cover(_real_image(101, 100)) == b""

    def test_decompression_bomb_warning_is_error(self, monkeypatch):
        monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
        assert resize_cover(_real_image(200, 200)) == b""

    @pytest.mark.parametrize("data", [b"", b"not an image"])
    def test_undecodable_returns_empty_never_original(self, data):
        assert resize_cover(data) == b""
