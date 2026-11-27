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
    RENDER_OPTIONS,
    SEARCH_CARD_TMPL,
    build_card_data,
)
from astrbot_plugin_zlibrary_assistant.zlib_models import SearchResult
from astrbot_plugin_zlibrary_assistant.zlib_text import FENCE_CLOSE, FENCE_OPEN


def _render(data: dict) -> str:
    return Environment(autoescape=False).from_string(SEARCH_CARD_TMPL).render(**data)


class TestBuildCardData:
    def test_fields(self):
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
        assert data["books"] == [
            {
                "no": 1,
                "title": "资本论",
                "author": "马克思",
                "publisher": "人民出版社",
                "extension": "PDF",
                "size": "5 MB",
                "language": "chinese",
                "year": "1867",
                "cover": "data:image/jpeg;base64,AAA",
                "fmt_bg": data["books"][0]["fmt_bg"],
                "fmt_fg": data["books"][0]["fmt_fg"],
            }
        ]
        assert data["footer"] == "回复 /zlib 下载 序号 获取文件，/zlib 下一页 翻页"

    def test_footer_without_next_page(self):
        data = build_card_data(
            SearchResult("q", [make_book(1)], page=1, total_pages=1), {}, "/z"
        )
        assert data["footer"] == "回复 /z 下载 序号 获取文件"

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

    def test_known_and_unknown_format_colors(self):
        data = build_card_data(
            SearchResult(
                "q", [make_book(1, extension="pdf"), make_book(2, extension="zzz")]
            ),
            {},
            "/z",
        )
        pdf, other = data["books"]
        assert re.fullmatch(r"#[0-9a-f]{6}", pdf["fmt_bg"]) and re.fullmatch(
            r"#[0-9a-f]{6}", other["fmt_fg"]
        )
        assert pdf["fmt_bg"] != other["fmt_bg"]

    def test_render_options(self):
        assert RENDER_OPTIONS["type"] == "jpeg"
        assert RENDER_OPTIONS["full_page"] is True


EVIL = "<script>alert(1)</script>"
TEMPLATE_INJECTION = "{{7*7}}"


def _hostile_book(**over):
    fields = dict(
        title=f"T {EVIL} {TEMPLATE_INJECTION}",
        author=f"A {EVIL} {TEMPLATE_INJECTION}",
        publisher=f"P {EVIL} {TEMPLATE_INJECTION}",
        extension="pdf",
        size=f"S {EVIL}",
        language=f"L {EVIL}",
        year="1999",
    )
    fields.update(over)
    return make_book(1, **fields)


class TestTemplateEscaping:
    def test_hostile_book_fields_are_escaped_without_autoescape(self):
        data = build_card_data(
            SearchResult(f"Q {EVIL} {TEMPLATE_INJECTION}", [_hostile_book()]), {}, "/z"
        )
        html = _render(data)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "{{7*7}}" in html
        assert "49" not in html

    @pytest.mark.parametrize(
        "field", ["title", "author", "publisher", "size", "language"]
    )
    def test_each_text_field_escaped_individually(self, field):
        data = build_card_data(
            SearchResult(
                "q",
                [
                    _hostile_book(
                        **{
                            f: "ok"
                            for f in (
                                "title",
                                "author",
                                "publisher",
                                "size",
                                "language",
                            )
                            if f != field
                        }
                    )
                ],
            ),
            {},
            "/z",
        )
        html = _render(data)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_extension_year_query_meta_footer_cover_escaped(self):
        """未经 build_card_data 清洗的直传数据同样被模板转义（模板自身是最后一道防线）。"""
        data = {
            "query": EVIL,
            "meta": EVIL,
            "footer": EVIL,
            "books": [
                {
                    "no": EVIL,
                    "title": EVIL,
                    "author": EVIL,
                    "publisher": EVIL,
                    "extension": EVIL,
                    "size": EVIL,
                    "language": EVIL,
                    "year": EVIL,
                    "cover": '"><script>x</script>',
                    "fmt_bg": "#fff",
                    "fmt_fg": "#000",
                }
            ],
        }
        html = _render(data)
        assert "<script>" not in html
        assert html.count("&lt;script&gt;") >= 11

    def test_template_expressions_in_data_are_not_evaluated(self):
        data = build_card_data(
            SearchResult(TEMPLATE_INJECTION, [_hostile_book()]), {}, "/z"
        )
        assert "49" not in _render(data)

    def test_every_dynamic_text_expression_in_template_has_escape_filter(self):
        """模板里除内部配色常量外，所有 {{ ... }} 输出都带 | e。"""
        exprs = re.findall(r"\{\{(.*?)\}\}", SEARCH_CARD_TMPL)
        unescaped = [e.strip() for e in exprs if "| e" not in e]
        assert sorted(unescaped) == ["b.fmt_bg", "b.fmt_fg"]

    def test_all_books_rendered_in_order(self):
        books = [make_book(i, title=f"Book{i}") for i in range(1, 4)]
        html = _render(build_card_data(SearchResult("q", books), {}, "/z"))
        assert html.index("Book1") < html.index("Book2") < html.index("Book3")
