"""extract.py 的客户端配置: 网关要求的自定义请求头。

有些网关靠请求头做路由: 例如 OpenCode Go 要求每次对话带稳定的
`x-opencode-session`, 缺失时直接返回 MissingSessionID 拒绝服务。
"""
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scraper import extract, models


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


class CompactSchemaTests(unittest.TestCase):
    """固定开销里 Pydantic 的 title 与属性名重复, 删掉零语义损失。"""

    def test_titles_are_dropped_but_descriptions_kept(self):
        schema = extract._compact_schema(models.PricingPage)
        self.assertNotIn('"title"', schema)
        # description 里带着抽取规则("不能机械换算则 null"等), 不能删
        self.assertIn("每百万 tokens", schema)

    def test_compact_schema_is_smaller_than_the_raw_one(self):
        raw = json.dumps(models.PricingPage.model_json_schema(),
                         ensure_ascii=False)
        self.assertLess(len(extract._compact_schema(models.PricingPage)),
                        len(raw))

    def test_compact_schema_stays_valid_json(self):
        parsed = json.loads(
            extract._compact_schema(models.VideoGenerationPage))
        self.assertIn("properties", parsed)


class ThrottleAndRetryTests(unittest.TestCase):
    """自建网关在连续请求下会重置连接, 需要能拉开间隔并在失败后退避重试。"""

    def _response(self):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])

    def test_throttle_is_off_by_default(self):
        with patch.object(extract, "MIN_CALL_INTERVAL_SECONDS", 0), \
                patch.object(extract.time, "sleep") as sleep:
            extract._wait_for_turn()
        sleep.assert_not_called()

    def test_throttle_sleeps_for_the_remaining_interval(self):
        with patch.object(extract, "MIN_CALL_INTERVAL_SECONDS", 3), \
                patch.object(extract.time, "monotonic", return_value=1.0), \
                patch.object(extract.time, "sleep") as sleep:
            extract._last_call_started = 0.0
            extract._wait_for_turn()
        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 2.0)

    def test_connection_error_is_retried_then_succeeds(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            extract.openai.APIConnectionError(request=MagicMock()),
            self._response(),
        ]
        with patch.object(extract, "MIN_CALL_INTERVAL_SECONDS", 0), \
                patch.object(extract, "CONNECTION_RETRIES", 2), \
                patch.object(extract, "CONNECTION_BACKOFF_SECONDS", 0), \
                patch.object(extract.time, "sleep"):
            extract._call(client, [{"role": "user", "content": "x"}], "pricing")
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_connection_error_gives_up_after_configured_retries(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = (
            extract.openai.APIConnectionError(request=MagicMock()))
        with patch.object(extract, "MIN_CALL_INTERVAL_SECONDS", 0), \
                patch.object(extract, "CONNECTION_RETRIES", 1), \
                patch.object(extract, "CONNECTION_BACKOFF_SECONDS", 0), \
                patch.object(extract.time, "sleep"):
            with self.assertRaises(extract.openai.APIConnectionError):
                extract._call(client, [{"role": "user", "content": "x"}],
                              "pricing")
        self.assertEqual(client.chat.completions.create.call_count, 2)


class UsageMeteringTests(unittest.TestCase):
    """每次调用都记账, 优化前后的 token 对比才有真实依据。"""

    def setUp(self):
        extract.reset_usage()

    def tearDown(self):
        extract.reset_usage()

    def test_summary_reports_no_calls_when_idle(self):
        self.assertEqual(extract.usage_summary(), "[计量] 本轮未调用模型")

    def test_summary_aggregates_per_label(self):
        extract.record_usage("pricing", 1_000, 200)
        extract.record_usage("pricing", 500, 100)
        extract.record_usage("imagegen", 20_000, 900)
        summary = extract.usage_summary()
        self.assertIn("模型调用 3 次", summary)
        self.assertIn("输入 21500 字符", summary)
        self.assertIn("输出 1200 字符", summary)
        self.assertIn("imagegen", summary)

    def test_call_records_input_and_output_sizes(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])
        client = MagicMock()
        client.chat.completions.create.return_value = response
        extract._call(client, [{"role": "user", "content": "12345"}], "plans")
        summary = extract.usage_summary()
        self.assertIn("plans", summary)
        self.assertIn("输入 5 字符", summary)

    def test_reset_usage_clears_previous_totals(self):
        extract.record_usage("news", 10, 1)
        extract.reset_usage()
        self.assertEqual(extract.usage_summary(), "[计量] 本轮未调用模型")

    def test_call_survives_response_without_content(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=None))])
        client = MagicMock()
        client.chat.completions.create.return_value = response
        extract._call(client, [{"role": "user", "content": "x"}], "news")
        self.assertIn("输出 0 字符", extract.usage_summary())


if __name__ == "__main__":
    unittest.main()
