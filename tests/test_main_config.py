"""main.parse_accounts / parse_domains：配置解析的容错与规范化。"""

from __future__ import annotations

import pytest
from stubs import RecordingLogger

from astrbot_plugin_zlibrary_assistant import main as plugin_main


class TestParseAccounts:
    @pytest.mark.parametrize(
        "config", [{}, {"accounts": None}, {"accounts": []}, {"accounts": ""}]
    )
    def test_empty_config_gives_no_accounts(self, config):
        assert plugin_main.parse_accounts(config) == []

    @pytest.mark.parametrize("raw", ["x", 5, {"name": "a"}, ("a",)])
    def test_non_list_value_ignored(self, raw):
        assert plugin_main.parse_accounts({"accounts": raw}) == []

    def test_full_entry(self):
        (acc,) = plugin_main.parse_accounts(
            {
                "accounts": [
                    {
                        "name": "main",
                        "email": " a@b.c ",
                        "password": " pw ",
                        "remix_userid": " 12 ",
                        "remix_userkey": " key ",
                    }
                ]
            }
        )
        assert acc.name == "main"
        assert acc.email == "a@b.c"
        assert acc.password == " pw "
        assert (acc.remix_userid, acc.remix_userkey) == ("12", "key")
        assert acc.has_remix
        assert acc.has_password_login
        assert acc.logged_in is False
        assert acc.downloads_today == 0

    def test_missing_fields_default_to_empty_without_credentials(self):
        (acc,) = plugin_main.parse_accounts({"accounts": [{"name": "x"}]})
        assert (acc.email, acc.password, acc.remix_userid, acc.remix_userkey) == (
            "",
            "",
            "",
            "",
        )
        assert not acc.has_remix
        assert not acc.has_password_login

    def test_none_values_do_not_become_the_string_none(self):
        (acc,) = plugin_main.parse_accounts(
            {
                "accounts": [
                    {
                        "name": None,
                        "email": None,
                        "password": None,
                        "remix_userid": None,
                    }
                ]
            }
        )
        assert acc.name == "账号1"
        assert acc.email == ""
        assert acc.password == ""
        assert acc.remix_userid == ""

    def test_half_remix_pair_is_not_a_remix_account(self):
        (acc,) = plugin_main.parse_accounts({"accounts": [{"remix_userid": "1"}]})
        assert not acc.has_remix

    def test_unnamed_accounts_numbered_by_position(self):
        accounts = plugin_main.parse_accounts(
            {"accounts": [{"name": ""}, {}, {"name": "named"}, {}]}
        )
        assert [a.name for a in accounts] == ["账号1", "账号2", "named", "账号4"]

    def test_non_dict_items_skipped_but_still_count_for_numbering(self):
        accounts = plugin_main.parse_accounts({"accounts": ["junk", {}, None, {}]})
        assert [a.name for a in accounts] == ["账号2", "账号4"]

    def test_duplicate_entries_are_kept_as_separate_accounts(self):
        entry = {"name": "dup", "remix_userid": "1", "remix_userkey": "k"}
        accounts = plugin_main.parse_accounts({"accounts": [entry, dict(entry)]})
        assert len(accounts) == 2
        assert accounts[0] is not accounts[1]

    def test_numeric_name_coerced_to_string(self):
        (acc,) = plugin_main.parse_accounts({"accounts": [{"name": 7}]})
        assert acc.name == "7"


class TestParseDomains:
    @pytest.fixture
    def log(self, monkeypatch):
        rec = RecordingLogger()
        monkeypatch.setattr(plugin_main, "logger", rec)
        return rec

    @pytest.mark.parametrize(
        "config", [{}, {"domains": None}, {"domains": []}, {"domains": ""}]
    )
    def test_empty_config(self, config, log):
        assert plugin_main.parse_domains(config) == []

    @pytest.mark.parametrize("raw", ["z-lib.by", 5, {"a": 1}])
    def test_non_list_value_ignored(self, raw, log):
        assert plugin_main.parse_domains({"domains": raw}) == []

    def test_normalised_to_base_urls(self, log):
        domains = plugin_main.parse_domains(
            {
                "domains": [
                    "z-lib.by",
                    " HTTPS://Mirror.Example ",
                    "http://plain.example",
                ]
            }
        )
        assert domains == [
            "https://z-lib.by",
            "https://mirror.example",
            "http://plain.example",
        ]

    def test_onion_defaults_to_http(self, log):
        host = "a" * 56 + ".onion"
        assert plugin_main.parse_domains({"domains": [host]}) == [f"http://{host}"]

    def test_invalid_entries_skipped_and_logged_without_aborting(self, log):
        domains = plugin_main.parse_domains(
            {
                "domains": [
                    "ok.example",
                    "",
                    "ftp://bad.example",
                    "https://x.example/path",
                    "last.example",
                ]
            }
        )
        assert domains == ["https://ok.example", "https://last.example"]
        assert len(log.lines) == 3

    def test_long_invalid_entry_truncated_in_log(self, log):
        plugin_main.parse_domains({"domains": ["ftp://" + "a" * 500]})
        assert len(log.text) < 300

    def test_duplicates_pass_through_for_the_domain_pool_to_collapse(self, log):
        domains = plugin_main.parse_domains(
            {"domains": ["a.example", "https://A.example"]}
        )
        assert domains == ["https://a.example", "https://a.example"]
