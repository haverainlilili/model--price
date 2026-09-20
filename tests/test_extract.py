"""extract.py 的客户端配置: 网关要求的自定义请求头。

有些网关靠请求头做路由: 例如 OpenCode Go 要求每次对话带稳定的
`x-opencode-session`, 缺失时直接返回 MissingSessionID 拒绝服务。
"""
import os
import unittest
from unittest.mock import patch

from scraper import extract


class ExtraHeadersTests(unittest.TestCase):
    def test_parses_comma_separated_name_value_pairs(self):
        with patch.dict(os.environ,
                        {"OPENAI_EXTRA_HEADERS": "x-opencode-session:scraper"}):
            self.assertEqual(extract._extra_headers(),
                             {"x-opencode-session": "scraper"})

    def test_trims_whitespace_around_names_and_values(self):
        with patch.dict(os.environ,
                        {"OPENAI_EXTRA_HEADERS": " A:1 ,B: 2 ,  ,"}):
            self.assertEqual(extract._extra_headers(), {"A": "1", "B": "2"})

    def test_keeps_colons_inside_header_value(self):
        with patch.dict(os.environ, {"OPENAI_EXTRA_HEADERS": "X-Trace:a:b:c"}):
            self.assertEqual(extract._extra_headers(), {"X-Trace": "a:b:c"})

    def test_ignores_malformed_entries(self):
        with patch.dict(os.environ,
                        {"OPENAI_EXTRA_HEADERS": "novalue,:empty,name:,ok:v"}):
            self.assertEqual(extract._extra_headers(), {"ok": "v"})

    def test_absent_or_blank_setting_yields_no_headers(self):
        with patch.dict(os.environ, {"OPENAI_EXTRA_HEADERS": ""}):
            self.assertEqual(extract._extra_headers(), {})
        env = {k: v for k, v in os.environ.items()
               if k != "OPENAI_EXTRA_HEADERS"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(extract._extra_headers(), {})


class ClientHeaderWiringTests(unittest.TestCase):
    def test_client_sends_configured_headers(self):
        with patch.dict(os.environ,
                        {"OPENAI_EXTRA_HEADERS": "x-opencode-session:scraper"}), \
                patch.object(extract.openai, "OpenAI") as client:
            extract._client()
        self.assertEqual(client.call_args.kwargs["default_headers"],
                         {"x-opencode-session": "scraper"})

    def test_client_omits_headers_when_not_configured(self):
        env = {k: v for k, v in os.environ.items()
               if k != "OPENAI_EXTRA_HEADERS"}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(extract.openai, "OpenAI") as client:
            extract._client()
        self.assertNotIn("default_headers", client.call_args.kwargs)

    def test_client_keeps_timeout_and_base_url(self):
        with patch.dict(os.environ, {"OPENAI_BASE_URL": "https://gw.example/v1",
                                     "OPENAI_EXTRA_HEADERS": ""}), \
                patch.object(extract.openai, "OpenAI") as client:
            extract._client()
        self.assertEqual(client.call_args.kwargs["timeout"], 240.0)
        self.assertEqual(client.call_args.kwargs["base_url"],
                         "https://gw.example/v1")


if __name__ == "__main__":
    unittest.main()
