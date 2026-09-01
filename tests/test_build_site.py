import unittest

from scraper import build_site


class QuickVariantTests(unittest.TestCase):
    def test_summarizes_context_tier(self):
        self.assertEqual(
            build_site._quick_variant("Standard，短上下文；促销价格"),
            "标准·短",
        )

    def test_summarizes_input_length_band(self):
        self.assertEqual(
            build_site._quick_variant("在线推理（常规）；输入长度 (32, 128] 千 tokens"),
            "32–128K",
        )

    def test_keeps_zero_in_input_length_band(self):
        self.assertEqual(
            build_site._quick_variant("在线推理（常规）；输入长度 [0, 256] 千 tokens"),
            "0–256K",
        )

    def test_summarizes_bracket_only_input_band(self):
        self.assertEqual(
            build_site._quick_variant("输入长度 [0, 32)；缓存存储限时免费"),
            "0–32K",
        )

    def test_summarizes_open_ended_input_band(self):
        self.assertEqual(
            build_site._quick_variant("输入长度 [32+)；缓存存储限时免费"),
            "32K+",
        )

    def test_summarizes_batch_tier(self):
        self.assertEqual(build_site._quick_variant("批量；输出含思考 token"), "批量")

    def test_combines_service_and_length_tiers(self):
        self.assertEqual(
            build_site._quick_variant("优先服务；> 512k 输入 tokens"),
            "优先·>512K",
        )

    def test_summarizes_peak_period(self):
        self.assertEqual(
            build_site._quick_variant("高峰时段：北京时间周一至周五"),
            "高峰",
        )


class QuickChartTests(unittest.TestCase):
    def test_shows_four_models_and_labels_duplicate_price_tiers(self):
        providers = [{"id": "demo", "name": "Demo", "region": "国际"}]
        records = {"demo": {"currency": "USD", "models": [
            {"model": "same", "input_per_1m": 1, "output_per_1m": 2,
             "note": "Standard，短上下文"},
            {"model": "same", "input_per_1m": 2, "output_per_1m": 4,
             "note": "Standard，长上下文"},
            {"model": "third", "input_per_1m": 3, "output_per_1m": 6},
            {"model": "fourth", "input_per_1m": 4, "output_per_1m": 8},
            {"model": "fifth", "input_per_1m": 5, "output_per_1m": 10},
        ]}}

        chart = build_site._quick_chart(providers, records, rate=7.0)

        self.assertIn("每家最新的 4 个模型", chart)
        self.assertIn("third", chart)
        self.assertIn("fourth", chart)
        self.assertNotIn("fifth", chart)
        self.assertIn('<span class="bvariant" title="Standard，短上下文">标准·短</span>', chart)
        self.assertIn('<span class="bvariant" title="Standard，长上下文">标准·长</span>', chart)


class ProviderSectionTests(unittest.TestCase):
    def test_price_details_are_collapsed_by_default(self):
        section = build_site._prov_section(
            {
                "id": "demo",
                "name": "Demo",
                "region": "国际",
                "pricing_url": "https://example.com/pricing",
            },
            {
                "currency": "USD",
                "source": "claude",
                "fetched_at": "2026-08-31T12:00:00Z",
                "models": [{
                    "model": "demo-model",
                    "input_per_1m": 1,
                    "output_per_1m": 2,
                    "currency": "USD",
                }],
            },
            rate=7.0,
        )

        self.assertIn('<details class="prov"', section)
        self.assertIn('<summary class="prov-head">', section)
        self.assertIn('<span class="prov-toggle">', section)
        self.assertNotIn('<details class="prov" open', section)
        self.assertIn('</summary>', section)
        self.assertIn('</details>', section)
        self.assertGreater(
            section.index('官网价格页 ↗'),
            section.index('</summary>'),
        )


if __name__ == "__main__":
    unittest.main()
