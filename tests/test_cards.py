"""zlib_cards：卡片数据构造与模板转义。

渲染端不开启 Jinja2 autoescape，因此书目文本字段必须在模板里显式 `| e`。
"""

from __future__ import annotations

import re

import pytest
from jinja2 import Environment
from stubs import make_book

from astrbot_plugin_zlibrary_assistant.zlib_cards import (
    MAX_CARD_BOOKS,
    SEARCH_CARD_TMPL,
    build_card_data,
)
from astrbot_plugin_zlibrary_assistant.zlib_models import SearchResult
from astrbot_plugin_zlibrary_assistant.zlib_text import FENCE_CLOSE, FENCE_OPEN


def _render(data: dict) -> str:
    return Environment(autoescape=False).from_string(SEARCH_CARD_TMPL).render(**data)


class TestBuildCardData:
    def test_meta_and_book_fields_mapped(self):
        book = make_book(
            1,
            title="资本论",
            author="马克思",
            publisher="人民出版社",
            extension="pdf",
            size="5 MB",
            language="chinese",
            year="1867",
        )
        result = SearchResult("资本论", [book], page=2, total_pages=5, total_items=48)
        data = build_card_data(result, {1: "data:image/jpeg;base64,AAA"}, "/zlib")
        assert data["query"] == "资本论"
        assert data["meta"] == "第 2/5 页 · 共 48 条"
        row = data["books"][0]
        assert (row["no"], row["title"], row["author"], row["publisher"]) == (
            1,
            "资本论",
            "马克思",
            "人民出版社",
        )
        assert (row["extension"], row["size"], row["language"], row["year"]) == (
            "PDF",
            "5 MB",
            "chinese",
            "1867",
        )
        assert row["cover"] == "data:image/jpeg;base64,AAA"

    @pytest.mark.parametrize(
        ("total_pages", "quota_hint", "tail"),
        [
            (5, "", "，/z 下一页"),
            (1, "", ""),
            (1, "今日剩余 4 次下载", "，今日剩余 4 次下载"),
        ],
    )
    def test_footer_branches(self, total_pages, quota_hint, tail):
        data = build_card_data(
            SearchResult("q", [make_book(1)], page=1, total_pages=total_pages),
            {},
            "/z",
            quota_hint=quota_hint,
        )
        assert data["footer"] == "回复 /z 详情 序号 看简介，/z 下载 序号 取文件" + tail

    def test_missing_title_author_and_cover_defaults(self):
        book = make_book(1, title="", author="")
        (row,) = build_card_data(SearchResult("q", [book]), {}, "/z")["books"]
        assert (row["title"], row["author"], row["cover"]) == (
            "未知标题",
            "未知作者",
            "",
        )

    def test_truncated_to_max_card_books_with_sequential_numbers(self):
        books = [make_book(i) for i in range(1, MAX_CARD_BOOKS + 6)]
        data = build_card_data(SearchResult("q", books), {}, "/z")
        assert [b["no"] for b in data["books"]] == list(range(1, MAX_CARD_BOOKS + 1))

    def test_cover_matched_by_book_id(self):
        books = [make_book(10), make_book(20)]
        data = build_card_data(SearchResult("q", books), {20: "data:x"}, "/z")
        assert [b["cover"] for b in data["books"]] == ["", "data:x"]

    def test_field_limits_and_flattening(self):
        book = make_book(
            1, title="t" * 500, author="a\nb" * 100, publisher="p" * 500, size="s" * 99
        )
        (row,) = build_card_data(SearchResult("q" * 200, [book]), {}, "/z")["books"]
        assert len(row["title"]) == 200
        assert len(row["author"]) == 80 and "\n" not in row["author"]
        assert len(row["publisher"]) == 60
        assert len(row["size"]) == 20
        assert (
            len(build_card_data(SearchResult("q" * 200, []), {}, "/z")["query"]) == 60
        )

    def test_fence_markers_removed_from_card_text(self):
        evil = f"{FENCE_OPEN}{FENCE_CLOSE}"
        data = build_card_data(SearchResult(evil, [make_book(1, title=evil)]), {}, "/z")
        assert FENCE_OPEN not in str(data) and FENCE_CLOSE not in str(data)


EVIL = "<script>alert(1)</script>"
TEMPLATE_INJECTION = "{{7*7}}"


class TestTemplateEscaping:
    def test_hostile_book_fields_are_escaped_without_autoescape(self):
        book = make_book(
            1,
            title=f"T {EVIL} {TEMPLATE_INJECTION}",
            author=f"A {EVIL}",
            publisher=f"P {EVIL}",
            size=f"S {EVIL}",
            language=f"L {EVIL}",
        )
        data = build_card_data(SearchResult(f"Q {EVIL}", [book]), {}, "/z")
        html = _render(data)
        assert "<script>" not in html
        assert html.count("&lt;script&gt;") == 6
        assert "{{7*7}}" in html and "49" not in html

    def test_every_dynamic_text_expression_in_template_has_escape_filter(self):
        """模板里除内部配色常量外，所有 {{ ... }} 输出都带 | e。"""
        exprs = re.findall(r"\{\{(.*?)\}\}", SEARCH_CARD_TMPL)
        unescaped = [e.strip() for e in exprs if "| e" not in e]
        assert sorted(unescaped) == ["b.fmt_bg", "b.fmt_fg"]
