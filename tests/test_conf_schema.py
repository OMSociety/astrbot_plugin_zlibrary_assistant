"""_conf_schema.json：配置项集合、类型与默认值，以及与解析器的契约。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "_conf_schema.json"
EXPECTED = {
    "accounts": ("template_list", []),
    "domains": ("list", []),
    "proxy": ("string", ""),
    "max_download_mb": ("int", 80),
    "search_limit": ("int", 5),
    "search_timeout": ("int", 30),
    "card_enabled": ("bool", True),
    "download_admin_only": ("bool", False),
}
ACCOUNT_FIELDS = {"label", "remix_userid", "remix_userkey", "email", "password"}


@pytest.fixture(scope="module")
def schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _assert_displayable(name, item):
    for field in ("type", "description", "hint"):
        assert isinstance(item.get(field), str) and item[field].strip(), (
            f"{name}.{field} 缺失或为空"
        )
    assert "default" in item, f"{name} 缺少 default"


def test_option_set_types_and_defaults(schema):
    """多一项或少一项都意味着代码读取的配置与面板展示的不一致。"""
    assert set(schema) == set(EXPECTED)
    for key, (type_, default) in EXPECTED.items():
        _assert_displayable(key, schema[key])
        assert schema[key]["type"] == type_
        assert schema[key]["default"] == default
        assert type(schema[key]["default"]) is type(default)


def test_account_template_fields(schema):
    template = schema["accounts"]["templates"]["account"]
    assert set(template["items"]) == ACCOUNT_FIELDS
    assert template["display_item"] == "label"
    for name, item in template["items"].items():
        _assert_displayable(name, item)
        assert item["type"] == "string"


def test_schema_defaults_are_accepted_by_the_parsers(schema):
    from astrbot_plugin_zlibrary_assistant import main as plugin_main

    config = {key: item["default"] for key, item in schema.items()}
    assert plugin_main.parse_accounts(config) == []
    assert plugin_main.parse_domains(config) == []


def test_search_timeout_default_is_within_the_accepted_range(schema):
    from astrbot_plugin_zlibrary_assistant.tools.common import (
        MAX_DEADLINE,
        MIN_DEADLINE,
        clamp_deadline,
    )

    default = schema["search_timeout"]["default"]
    assert MIN_DEADLINE <= default <= MAX_DEADLINE
    assert clamp_deadline(default) == float(default)
