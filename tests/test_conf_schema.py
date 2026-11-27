"""_conf_schema.json：配置项集合与每项必备的展示字段。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "_conf_schema.json"
EXPECTED_KEYS = {
    "accounts",
    "domains",
    "proxy",
    "max_download_mb",
    "search_limit",
    "card_enabled",
    "download_admin_only",
}
ACCOUNT_FIELDS = {"name", "remix_userid", "remix_userkey", "email", "password"}


@pytest.fixture(scope="module")
def schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_top_level_keys_are_exactly_the_supported_options(schema):
    assert set(schema) == EXPECTED_KEYS


@pytest.mark.parametrize("key", sorted(EXPECTED_KEYS))
def test_every_option_has_type_description_hint(schema, key):
    item = schema[key]
    for field in ("type", "description", "hint"):
        assert isinstance(item.get(field), str)
        assert item[field].strip(), f"{key}.{field} 为空"


@pytest.mark.parametrize("key", sorted(EXPECTED_KEYS))
def test_every_option_has_default(schema, key):
    assert "default" in schema[key]


@pytest.mark.parametrize(
    ("key", "type_", "default"),
    [
        ("accounts", "template_list", []),
        ("domains", "list", []),
        ("proxy", "string", ""),
        ("max_download_mb", "int", 80),
        ("search_limit", "int", 5),
        ("card_enabled", "bool", True),
        ("download_admin_only", "bool", False),
    ],
)
def test_types_and_defaults(schema, key, type_, default):
    assert schema[key]["type"] == type_
    assert schema[key]["default"] == default
    assert type(schema[key]["default"]) is type(default)


def test_account_template_fields(schema):
    template = schema["accounts"]["templates"]["account"]
    assert set(template["items"]) == ACCOUNT_FIELDS
    assert template["display_item"] == "name"
    for name, item in template["items"].items():
        for field in ("type", "description", "hint"):
            assert isinstance(item.get(field), str) and item[field].strip(), (
                f"{name}.{field}"
            )
        assert item["type"] == "string"
        assert "default" in item


def test_schema_defaults_are_accepted_by_the_parsers(schema):
    from astrbot_plugin_zlibrary_assistant import main as plugin_main

    config = {key: item["default"] for key, item in schema.items()}
    assert plugin_main.parse_accounts(config) == []
    assert plugin_main.parse_domains(config) == []
