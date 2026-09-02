"""_build_render_data 转义测试：第三方内容与用户输入进模板前必须转义。

背景：搜索卡片经云端 t2i 渲染，云端 Jinja2 是否自动转义不受控，
书名/作者（第三方内容）与 query（用户输入）若原样进模板存在 HTML 注入风险
（reverse_searcher 1.0.3 同源问题）。
"""

from astrbot_plugin_zlibrary_assistant.tools.search_tool import _build_render_data


class TestBuildRenderData:
    """渲染数据转义"""

    def test_script_tag_escaped(self):
        books = [{"title": "<script>alert(1)</script>", "author": "x"}]
        data = _build_render_data(books, "query")
        assert "<script>" not in data["books"][0]["title"]
        assert "&lt;script&gt;" in data["books"][0]["title"]

    def test_html_tag_and_ampersand_escaped(self):
        books = [{"title": "A <b>bold</b> & B", "author": "y"}]
        data = _build_render_data(books, "q")
        assert data["books"][0]["title"] == "A &lt;b&gt;bold&lt;/b&gt; &amp; B"

    def test_quotes_escaped(self):
        books = [{"title": 'say "hi"', "author": "z"}]
        data = _build_render_data(books, "q")
        assert '"hi"' not in data["books"][0]["title"]
        assert "&quot;hi&quot;" in data["books"][0]["title"]

    def test_query_escaped(self):
        data = _build_render_data([], "<img src=x onerror=alert(1)>")
        assert "<img" not in data["query"]
        assert "&lt;img" in data["query"]

    def test_normal_text_untouched(self):
        books = [{"title": "Python 基础教程", "author": "Frédéric"}]
        data = _build_render_data(books, "三体")
        assert data["books"][0]["title"] == "Python 基础教程"
        assert data["books"][0]["author"] == "Frédéric"
        assert data["query"] == "三体"

    def test_cover_data_uri_untouched(self):
        # base64 字符集（A-Za-z0-9+/=）不含 & < >，data URI 应原样通过
        uri = "data:image/jpeg;base64,/9j/4AAQSk=="
        books = [{"title": "t", "cover": uri}]
        data = _build_render_data(books, "q")
        assert data["books"][0]["cover"] == uri

    def test_all_template_fields_covered(self):
        # 模板插值的全部字段都必须出现在转义输出中
        book = {
            "title": "t",
            "author": "a",
            "extension": "pdf",
            "language": "en",
            "year": "2020",
            "filesizeString": "1 MB",
            "cover": "",
        }
        data = _build_render_data([book], "q")
        assert set(data["books"][0].keys()) == set(book.keys())

    def test_none_and_missing_fields_become_empty(self):
        data = _build_render_data([{"title": None}], "q")
        assert data["books"][0]["title"] == ""
        assert data["books"][0]["author"] == ""
        assert data["books"][0]["cover"] == ""
