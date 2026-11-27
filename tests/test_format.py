"""zlib_format：指令参数解析与状态/诊断文本。"""

from __future__ import annotations

import pytest

from astrbot_plugin_zlibrary_assistant.zlib_client import Diagnosis
from astrbot_plugin_zlibrary_assistant.zlib_domains import ProbeReport
from astrbot_plugin_zlibrary_assistant.zlib_format import (
    CATEGORY_LABELS,
    HELP_TEXT,
    ArgError,
    format_diagnosis,
    format_status,
    parse_search_args,
    strip_command,
)

SUBS = {"搜索", "search", "s"}


class TestStripCommand:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("/zlib 搜索 资本论 -l zh", "资本论 -l zh"),
            ("  /zlib   search  a b c", "a b c"),
            ("zlib 下载 1", ""),
            ("", ""),
        ],
    )
    def test_strip(self, text, expected):
        assert strip_command(text, SUBS) == expected


class TestParseSearchArgs:
    def test_defaults(self):
        args = parse_search_args("das kapital marx")
        assert (args.query, args.language, args.extension, args.limit) == (
            "das kapital marx",
            "",
            "",
            None,
        )

    def test_all_options_between_words(self):
        args = parse_search_args("-l en deep learning -e epub book -n 8")
        assert (args.query, args.language, args.extension, args.limit) == (
            "deep learning book",
            "en",
            "epub",
            8,
        )

    def test_quoted_query_keeps_phrase_and_last_option_wins(self):
        args = parse_search_args('"the art of war" -e pdf -e epub')
        assert (args.query, args.extension) == ("the art of war", "epub")

    @pytest.mark.parametrize("text", ["", "-l zh", "-e pdf -n 3"])
    def test_missing_query(self, text):
        with pytest.raises(ArgError, match="关键词"):
            parse_search_args(text)

    @pytest.mark.parametrize("opt", ["-l", "-e", "-n"])
    def test_option_without_value(self, opt):
        with pytest.raises(ArgError, match=f"选项 {opt} 后需要一个值"):
            parse_search_args(f"书名 {opt}")

    @pytest.mark.parametrize("value", ["0", "-1", "abc", "1.5"])
    def test_invalid_limit(self, value):
        with pytest.raises(ArgError, match="正整数"):
            parse_search_args(f'书名 -n "{value}"')

    def test_unbalanced_quote(self):
        with pytest.raises(ArgError, match="参数无法解析"):
            parse_search_args('"未闭合 -l zh')


def _row(**over):
    row = {
        "name": "主号",
        "logged_in": True,
        "premium": False,
        "login_mode": "remix",
        "downloads_today": 2,
        "downloads_limit": 10,
        "downloads_left": 8,
        "last_error": "",
    }
    row.update(over)
    return row


class TestFormatStatus:
    def test_empty(self):
        assert "未配置任何账号" in format_status([])

    def test_row_fields(self):
        text = format_status([_row()])
        assert "Z-Library 账号池" in text
        assert "- 主号（remix）正常，今日 2/10，剩余 8" in text
        assert "Premium" not in text
        assert "最近错误" not in text

    def test_premium_and_logged_out_and_error(self):
        text = format_status(
            [_row(premium=True, logged_in=False, last_error="密码错误")]
        )
        assert "未登录，Premium" in text
        assert "最近错误：密码错误" in text

    def test_never_prints_credentials(self):
        row = _row()
        row.update(password="PW-SECRET", remix_userkey="KEY-SECRET")
        assert "SECRET" not in format_status([row])


class TestFormatDiagnosis:
    def test_mixed_report(self):
        diag = Diagnosis(
            domains=[
                ProbeReport("https://a.example", 1, True, latency_ms=120),
                ProbeReport("https://b.example", 1, False, "diamwall_blocked", "m"),
                ProbeReport("https://c.example", 1, False, "weird_category", "m"),
            ],
            accounts=[_row()],
            proxy="http://127.0.0.1:7890",
        )
        text = format_diagnosis(diag)
        assert "镜像域名：1/3 可用" in text
        assert "- a.example  可用  120 ms" in text
        assert f"- b.example  不可用（{CATEGORY_LABELS['diamwall_blocked']}）" in text
        assert "- c.example  不可用（weird_category）" in text
        assert "代理：http://127.0.0.1:7890" in text
        assert "账号池" in text
        assert "所有镜像都不可用" not in text

    def test_none_ok_adds_advice_and_unset_proxy(self):
        diag = Diagnosis(
            domains=[ProbeReport("https://a.example", 1, False, "network_error", "m")],
            accounts=[],
            proxy="",
        )
        text = format_diagnosis(diag)
        assert "镜像域名：0/1 可用" in text
        assert "代理：未配置" in text
        assert "所有镜像都不可用" in text


def test_help_text_formats_with_prefix():
    text = HELP_TEXT.format(p="/zlib")
    assert "/zlib 搜索" in text and "{p}" not in text
