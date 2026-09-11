import os
import stat
import tempfile
import unittest
from pathlib import Path

from scraper import build_site


class AtomicArtifactTests(unittest.TestCase):
    def test_atomic_write_preserves_public_read_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "index.html"
            target.write_text("old", encoding="utf-8")
            os.chmod(target, 0o644)
            build_site._atomic_write_text(target, "new")
            self.assertEqual(target.read_text(encoding="utf-8"), "new")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)

            fresh = Path(directory) / "data.json"
            build_site._atomic_write_text(fresh, "{}")
            self.assertEqual(stat.S_IMODE(fresh.stat().st_mode), 0o644)


class ResponsiveCssTests(unittest.TestCase):
    def test_tablet_breakpoint_contains_wide_sticky_controls(self):
        self.assertIn("@media(max-width:800px)", build_site.CSS)
        self.assertIn(".controls{align-items:flex-start", build_site.CSS)
        self.assertIn("overflow-x:auto;scrollbar-width:none", build_site.CSS)
        self.assertIn(".controls-left,.control-groups{flex:none}", build_site.CSS)
        self.assertIn(".seg.view-tabs button{min-height:44px", build_site.CSS)


class ViewSwitchTests(unittest.TestCase):
    def test_view_tabs_keep_their_larger_desktop_target(self):
        self.assertIn(".seg.view-tabs button{min-height:38px", build_site.CSS)

    def test_renders_accessible_price_and_plan_tabs(self):
        tabs = build_site._view_tabs(has_plans=True, has_websearch=False)

        self.assertIn('role="tablist"', tabs)
        self.assertIn('data-view-btn="prices"', tabs)
        self.assertIn('data-view-btn="plans"', tabs)
        self.assertIn('aria-controls="price-overview"', tabs)
        self.assertIn('aria-controls="plan-overview"', tabs)
        self.assertIn('aria-selected="true"', tabs)
        self.assertIn('aria-selected="false"', tabs)

    def test_renders_websearch_tab_when_present(self):
        tabs = build_site._view_tabs(has_plans=True, has_websearch=True)

        self.assertIn('data-view-btn="websearch"', tabs)
        self.assertIn('aria-controls="websearch-overview"', tabs)
        self.assertIn('联网搜索</button>', tabs)

    def test_child_hashes_keep_their_owning_module_visible(self):
        self.assertIn("v.indexOf('imagegen-')===0", build_site.JS)
        self.assertIn("v.indexOf('videogen-')===0", build_site.JS)
        self.assertIn("v.indexOf('ws-')===0", build_site.JS)
        self.assertIn("v.indexOf('plan-')===0", build_site.JS)

    def test_renders_both_media_generation_tabs_when_present(self):
        tabs = build_site._view_tabs(
            has_plans=True, has_websearch=True,
            has_imagegen=True, has_videogen=True)

        self.assertIn('data-view-btn="imagegen"', tabs)
        self.assertIn('aria-controls="imagegen-overview"', tabs)
        self.assertIn('AI 生图</button>', tabs)
        self.assertIn('data-view-btn="videogen"', tabs)
        self.assertIn('aria-controls="videogen-overview"', tabs)
        self.assertIn('AI 生视频</button>', tabs)


class ModelLabelTests(unittest.TestCase):
    def test_separates_official_display_name_from_api_identifier(self):
        self.assertEqual(
            build_site._model_label_parts({
                "model": "deepseek-flash",
                "display_name": "DeepSeek-V4.1-Flash",
            }),
            ("DeepSeek-V4.1-Flash", "deepseek-flash"),
        )

    def test_uses_api_identifier_when_no_separate_display_name_exists(self):
        self.assertEqual(
            build_site._model_label_parts({"model": "gpt-5"}),
            ("gpt-5", ""),
        )


class QuickVariantTests(unittest.TestCase):
    def test_summarizes_context_tier(self):
        self.assertEqual(
            build_site._quick_variant("Standard，短上下文；促销价格"),
            "标准·短",
        )

    def test_recognizes_english_short_and_long_context(self):
        self.assertEqual(
            build_site._quick_variant("Standard；Short context"),
            "标准·短",
        )
        self.assertEqual(
            build_site._quick_variant("Standard；Long context"),
            "标准·长",
        )

    def test_combines_english_context_with_batch_and_fast_tiers(self):
        self.assertEqual(
            build_site._quick_variant("Batch；Long context"),
            "批量·长",
        )
        self.assertEqual(
            build_site._quick_variant("Fast mode；Short context"),
            "极速·短",
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


    def test_shows_official_display_name_and_api_id(self):
        providers = [{"id": "deepseek", "name": "DeepSeek", "region": "国内"}]
        records = {"deepseek": {"currency": "CNY", "models": [{
            "model": "deepseek-flash",
            "display_name": "DeepSeek-V4.1-Flash",
            "input_per_1m": 1,
            "output_per_1m": 4,
        }]}}

        chart = build_site._quick_chart(providers, records, rate=7.0)

        self.assertIn("DeepSeek-V4.1-Flash", chart)
        self.assertIn("API · deepseek-flash", chart)

class CheapestChartTests(unittest.TestCase):
    def test_chooses_lowest_total_from_each_providers_first_four_rows(self):
        providers = [{
            "id": "demo",
            "name": "Demo",
            "name_cn": "示例厂商",
            "region": "国际",
        }]
        records = {"demo": {"currency": "USD", "models": [
            {"model": "expensive", "input_per_1m": 4, "output_per_1m": 8},
            {"model": "cheapest", "input_per_1m": 1, "output_per_1m": 2},
            {"model": "partial", "input_per_1m": None, "output_per_1m": 5},
            {"model": "other", "input_per_1m": 2, "output_per_1m": 3},
            {"model": "ignored-fifth", "input_per_1m": .1, "output_per_1m": .1},
        ]}}

        chart = build_site._cheapest_chart(providers, records, rate=7.0)

        self.assertIn("各厂商最新 4 条中的最低价", chart)
        self.assertIn("价格 = 输入价 + 输出价", chart)
        self.assertIn("示例厂商", chart)
        self.assertIn("cheapest", chart)
        self.assertIn("¥21", chart)
        self.assertNotIn("expensive", chart)
        self.assertNotIn("ignored-fifth", chart)
        self.assertIn('role="list"', chart)

    def test_uses_a_single_available_price_component(self):
        providers = [{"id": "demo", "name": "Demo", "region": "国内"}]
        records = {"demo": {"currency": "CNY", "models": [
            {"model": "input-only", "input_per_1m": .5, "output_per_1m": None},
            {"model": "both", "input_per_1m": .2, "output_per_1m": .4},
        ]}}

        chart = build_site._cheapest_chart(providers, records, rate=7.0)

        self.assertIn("input-only", chart)
        self.assertIn("¥0.5", chart)
        self.assertIn('data-region="domestic"', chart)

    def test_sorts_provider_columns_by_total_price_ascending(self):
        providers = [
            {"id": "high", "name": "High Provider", "region": "国际"},
            {"id": "low", "name": "Low Provider", "region": "国内"},
        ]
        records = {
            "high": {"currency": "CNY", "models": [
                {"model": "high-model", "input_per_1m": 8, "output_per_1m": 12},
            ]},
            "low": {"currency": "CNY", "models": [
                {"model": "low-model", "input_per_1m": 1, "output_per_1m": 2},
            ]},
        }

        chart = build_site._cheapest_chart(providers, records, rate=7.0)

        self.assertLess(chart.index("Low Provider"), chart.index("High Provider"))

    def test_shows_the_same_short_variant_as_the_quick_overview(self):
        providers = [{"id": "demo", "name": "Demo", "region": "国际"}]
        records = {"demo": {"currency": "USD", "models": [
            {"model": "same", "input_per_1m": 1, "output_per_1m": 2,
             "note": "Standard，短上下文；促销价格"},
            {"model": "same", "input_per_1m": 2, "output_per_1m": 4,
             "note": "Standard，长上下文"},
        ]}}

        chart = build_site._cheapest_chart(providers, records, rate=7.0)

        self.assertIn(
            '<span class="lowest-variant" title="Standard，短上下文；促销价格">标准·短</span>',
            chart,
        )


class ProviderSectionTests(unittest.TestCase):
    def test_shows_official_display_name_above_api_id(self):
        section = build_site._prov_section(
            {"id": "deepseek", "name": "DeepSeek", "region": "国内",
             "pricing_url": "https://api-docs.deepseek.com/pricing"},
            {"currency": "CNY", "source": "claude", "models": [{
                "model": "deepseek-flash",
                "display_name": "DeepSeek-V4.1-Flash",
                "input_per_1m": 1,
                "output_per_1m": 4,
            }]},
            rate=7.0,
        )

        self.assertIn("模型 / API ID", section)
        self.assertIn('<span class="c-model-name">DeepSeek-V4.1-Flash</span>', section)
        self.assertIn('<span class="c-model-api">API · deepseek-flash</span>', section)

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


class PlansSectionTests(unittest.TestCase):
    def test_explains_primary_quota_as_a_short_readable_sentence(self):
        cases = [
            (
                {
                    "label": "Usage Capacity",
                    "value": "5x Pro capacity per session",
                    "window": "per session",
                },
                "每次会话约为 Pro 套餐的 5 倍额度",
            ),
            (
                {"label": "Usage credits", "value": "$50", "window": ""},
                "包含 $50 使用额度",
            ),
            (
                {
                    "label": "请求数",
                    "value": "最多约1200次请求",
                    "window": "每5小时",
                },
                "每 5 小时最多约 1,200 次请求",
            ),
            (
                {
                    "label": "Agent 用量",
                    "value": "约 30 个",
                    "window": "每个计费周期（按月刷新）",
                },
                "每月约 30 个 Agent 用量",
            ),
            (
                {"label": "M3 用量", "value": "约 6 亿+ token", "window": "月度"},
                "每月约 6 亿+ M3 Tokens",
            ),
            (
                {
                    "label": "积分",
                    "value": "4,489 积分",
                    "window": "购买之日起 1 年内有效",
                },
                "1 年有效期内包含 4,489 积分",
            ),
            (
                {
                    "label": "包含输入和输出总Tokens",
                    "value": "1,200万/1.1亿",
                    "window": "",
                },
                "输入和输出合计 1,200 万 / 1.1 亿 Tokens",
            ),
        ]

        for quota, expected in cases:
            with self.subTest(quota=quota):
                self.assertEqual(build_site._quota_explanation(quota), expected)

    def test_parses_official_quota_magnitudes_without_cross_unit_conversion(self):
        self.assertEqual(build_site._quota_magnitude("5x Pro capacity"), 5)
        self.assertEqual(build_site._quota_magnitude("约 6 亿+ token"), 600_000_000)
        self.assertEqual(
            build_site._quota_magnitude("1,200万/1.1亿"),
            110_000_000,
        )
        self.assertIsNone(build_site._quota_magnitude("百万 Tokens"))

    def test_normalizes_chart_bars_only_within_the_same_official_quota(self):
        plans = [
            {"name": "Lite", "price": "¥10", "quotas": [
                {"label": "请求数", "value": "最多约1200次", "window": "每5小时"},
            ]},
            {"name": "Pro", "price": "¥50", "quotas": [
                {"label": "请求数", "value": "最多约6000次", "window": "每5小时"},
            ]},
            {"name": "Token Pack", "price": "¥20", "quotas": [
                {"label": "Token", "value": "100万", "window": "每月"},
            ]},
        ]

        self.assertEqual(
            build_site._plan_bar_heights(plans),
            [20.0, 100.0, 100.0],
        )

    def test_renders_only_plans_with_officially_stated_quotas(self):
        providers = [{
            "id": "demo",
            "name": "Demo",
            "name_cn": "示例厂商",
            "region": "国内",
        }]
        records = {"demo": {
            "source_urls": ["https://example.com/official-plans"],
            "fetched_at": "2026-09-01T08:00:00Z",
            "plans": [
                {
                    "name": "Plus",
                    "plan_type": "Token Plan",
                    "price": "¥49 / 月",
                    "quotas": [{
                        "label": "固定窗口",
                        "value": "1,500 次请求",
                        "window": "每 5 小时",
                    }],
                    "models": ["Demo-M3"],
                    "note": "官网标注数值",
                    "source_url": "https://example.com/official-plans#plus",
                },
                {
                    "name": "Derived",
                    "plan_type": "Community estimate",
                    "price": "¥99 / 月",
                    "quotas": [],
                    "note": "按周推算月用量",
                },
            ],
        }}

        section = build_site._plans_section(providers, records)

        self.assertIn('id="plans"', section)
        self.assertIn("套餐与额度", section)
        self.assertIn("示例厂商", section)
        self.assertIn("¥49 / 月", section)
        self.assertIn("1,500 次请求", section)
        self.assertIn("每 5 小时", section)
        self.assertIn("Demo-M3", section)
        self.assertIn('href="https://example.com/official-plans#plus"', section)
        self.assertIn('data-region="domestic"', section)
        self.assertIn('class="plan-quota-chart"', section)
        self.assertIn('class="plan-reference"', section)
        self.assertIn('href="https://www.codingplan.fyi/"', section)
        self.assertIn("具体套餐测评", section)
        self.assertIn("本项目只展示厂商官网直接标注", section)
        self.assertLess(
            section.index('class="plan-reference"'),
            section.index('class="plan-quota-chart"'),
        )
        self.assertIn('class="plan-bar-col"', section)
        self.assertIn('class="plan-bar-summary"', section)
        self.assertIn("每 5 小时包含 1,500 次请求", section)
        self.assertIn('style="--plan-bar-height:100.0%"', section)
        self.assertNotIn("Derived", section)
        self.assertNotIn("按周推算月用量", section)

    def test_omits_section_when_no_official_quota_is_available(self):
        providers = [{"id": "demo", "name": "Demo", "region": "国际"}]

        self.assertEqual(
            build_site._plans_section(
                providers,
                {"demo": {"plans": [{"name": "Price only", "quotas": []}]}},
            ),
            "",
        )


class WebSearchSectionTests(unittest.TestCase):
    def _cfg(self):
        return [
            {"id": "a", "name": "A", "region": "国际", "category": "ai-search"},
            {"id": "b", "name": "B", "region": "国内", "category": "serp"},
        ]

    def test_renders_objective_facts_seed_badge_and_price_chart(self):
        section = build_site._websearch_section(self._cfg(), {
            "a": {"source": "seed", "seed_verified": True, "has_search": True, "offerings": [{
                "name": "Search API", "pricing": "$5 / 1000 queries",
                "price_per_1k_usd": 5, "price_basis": "PAYG basic",
                "free_quota": "1000/月", "output_type": "结构化搜索结果",
                "cites_sources": True, "default_on": False, "note": "返回引用"}]},
        })

        self.assertIn("联网搜索 · 价格与效果", section)
        self.assertIn("Search API", section)
        self.assertIn("搜索 API 基础价柱状图", section)
        self.assertIn("$5 / 1k", section)
        self.assertIn("PAYG basic", section)
        self.assertIn("结构化搜索结果", section)
        self.assertIn('class="ws-yes"', section)
        self.assertIn('class="ws-no"', section)
        self.assertIn("官网事实种子 · 待自动校准", section)
        self.assertIn("1</b> 家联网搜索厂商", section)
        self.assertIn("1</b> 家独立搜索 API", section)
        self.assertIn("1</b> 项可按千次比较", section)

    def test_chart_scales_linearly_within_category(self):
        records = {
            "a": {"offerings": [
                {"name": "Basic", "price_per_1k_usd": 5, "price_basis": "basic"},
                {"name": "Deep", "price_per_1k_usd": 10, "price_basis": "deep"},
            ]},
        }

        chart = build_site._websearch_price_chart(self._cfg(), records)

        self.assertIn('style="--ws-bar-height:50.0%"', chart)
        self.assertIn('style="--ws-bar-height:100.0%"', chart)
        self.assertIn("每种产品类型独立缩放", chart)

    def test_chart_omits_incomparable_token_only_price(self):
        chart = build_site._websearch_price_chart(self._cfg(), {
            "a": {"offerings": [{"name": "Token only", "pricing": "按 token"}]},
        })

        self.assertEqual(chart, "")

    def test_omits_section_when_no_offerings(self):
        self.assertEqual(
            build_site._websearch_section(self._cfg(), {}),
            "",
        )


class GenerationSectionTests(unittest.TestCase):
    def _configs(self):
        return [
            {
                "id": "intl", "name": "Intl Lab", "region": "国际",
                "examples": [{
                    "title": "Official output",
                    "url": "https://official.example/gallery",
                    "media_url": "https://official.example/sample.webp",
                    "note": "Official source",
                }],
            },
            {"id": "cn", "name": "CN Lab", "region": "国内"},
        ]

    def test_image_chart_scales_only_inside_strict_currency_and_quality_groups(self):
        base = {
            "api_available": True, "resolution": "1024x1024",
            "comparison_width": 1024, "comparison_height": 1024,
            "quality_tier": "standard", "currency": "USD",
            "comparison_group": "usd-standard-1mp", "price_basis": "1K default",
        }
        records = {
            "intl": {"offerings": [
                {**base, "name": "Low", "price_per_image": .02},
                {**base, "name": "High", "price_per_image": .04},
                {**base, "name": "Wrong currency", "currency": "CNY", "price_per_image": .01},
                {"name": "Dynamic token", "currency": "USD", "price_per_image": None,
                 "comparison_group": None, "pricing": "token based"},
                {**base, "name": "Missing currency", "currency": None, "price_per_image": .01},
                {**base, "name": "No public API", "api_available": False, "price_per_image": .01},
                {**base, "name": "Wrong shape", "resolution": "1024x2048",
                 "comparison_width": 1024, "comparison_height": 2048, "price_per_image": .01},
                {**base, "name": "Wrong tier", "quality_tier": "premium", "price_per_image": .01},
            ]},
            "cn": {"offerings": [{
                **base, "name": "CN Standard", "currency": "CNY",
                "comparison_group": "cny-standard-1mp", "price_per_image": .1,
            }]},
        }

        chart = build_site._generation_price_chart("imagegen", self._configs(), records)

        self.assertIn("USD · 标准约 1MP", chart)
        self.assertIn("人民币 · 标准约 1MP", chart)
        self.assertIn('style="--ws-bar-height:50.0%"', chart)
        self.assertIn('style="--ws-bar-height:100.0%"', chart)
        for excluded in ("Wrong currency", "Dynamic token", "Missing currency",
                         "No public API", "Wrong shape", "Wrong tier"):
            self.assertNotIn(excluded, chart)
        self.assertIn("不同组的柱高不可横向比较", chart)

    def test_video_chart_keeps_audio_and_silent_rates_separate(self):
        base = {
            "api_available": True, "resolution": "720p", "comparison_resolution": "720p",
            "currency": "USD", "price_basis": "720p official",
        }
        records = {"intl": {"offerings": [
            {**base, "name": "Audio", "price_per_second": .1,
             "comparison_group": "usd-720p-audio", "native_audio": True},
            {**base, "name": "Silent", "price_per_second": .05,
             "comparison_group": "usd-720p-silent", "native_audio": False},
            {**base, "name": "Bad audio claim", "price_per_second": .01,
             "comparison_group": "usd-720p-audio", "native_audio": False},
            {**base, "name": "Unknown silent audio", "price_per_second": .01,
             "comparison_group": "usd-720p-silent", "native_audio": None},
            {**base, "name": "Wrong video resolution", "comparison_resolution": "1080p",
             "price_per_second": .01, "comparison_group": "usd-720p-silent",
             "native_audio": False},
            {**base, "name": "Video no API", "api_available": False,
             "price_per_second": .01, "comparison_group": "usd-720p-silent",
             "native_audio": False},
            {**base, "name": "Deprecated model", "price_per_second": .08,
             "comparison_group": "usd-720p-silent", "native_audio": False,
             "lifecycle_status": "deprecated"},
        ]}}

        chart = build_site._generation_price_chart("videogen", self._configs(), records)

        self.assertIn("USD · 720p · 原生音频", chart)
        self.assertIn("USD · 720p · 无原生音频", chart)
        for excluded in ("Bad audio claim", "Unknown silent audio",
                         "Wrong video resolution", "Video no API"):
            self.assertNotIn(excluded, chart)
        self.assertIn('class="ws-bar-col gen-bar-col is-transitional"', chart)
        self.assertIn('data-life="已弃用（仍可调用）"', chart)
        self.assertNotIn("即将停用", chart)

    def test_lifecycle_cutoff_excludes_expired_chart_but_keeps_future_striped(self):
        from datetime import date
        offering = {
            "name": "Scheduled", "api_available": True, "currency": "USD",
            "price_per_second": .1, "comparison_group": "usd-720p-silent",
            "native_audio": False, "comparison_resolution": "720p",
            "lifecycle_status": "sunsetting", "sunset_at": "2026-09-24",
        }
        records = {"intl": {"offerings": [offering]}}
        before = build_site._generation_price_entries(
            "videogen", self._configs(), records, date(2026, 9, 23))
        on_date = build_site._generation_price_entries(
            "videogen", self._configs(), records, date(2026, 9, 24))
        after = build_site._generation_price_entries(
            "videogen", self._configs(), records, date(2026, 9, 25))
        self.assertEqual(len(before["usd-720p-silent"]), 1)
        self.assertTrue(before["usd-720p-silent"][0]["transitional"])
        self.assertEqual(len(on_date["usd-720p-silent"]), 1)
        self.assertEqual(after["usd-720p-silent"], [])

    def test_mixed_service_regions_publish_dynamic_chart_counts(self):
        providers = [{"id": "mixed", "name": "Mixed", "region": "国内"}]
        records = {"mixed": {"offerings": [
            {"name": "Global", "region": "intl", "api_available": True,
             "currency": "USD", "price_per_image": .03,
             "comparison_group": "usd-standard-1mp", "comparison_width": 1024,
             "comparison_height": 1024, "quality_tier": "standard",
             "price_basis": "global 1K"},
            {"name": "China", "region": "domestic", "api_available": True,
             "currency": "CNY", "price_per_image": .2,
             "comparison_group": "cny-standard-1mp", "comparison_width": 1024,
             "comparison_height": 1024, "quality_tier": "standard",
             "price_basis": "China 1K"},
        ]}}
        chart = build_site._generation_price_chart("imagegen", providers, records)
        self.assertEqual(chart.count('data-total="1" data-intl="1" data-domestic="0"'), 2)
        self.assertEqual(chart.count('data-total="1" data-intl="0" data-domestic="1"'), 2)
        self.assertIn('data-region="intl"', chart)
        self.assertIn('data-region="domestic"', chart)
        self.assertIn('data-region-bars', chart)

    def test_discontinued_provider_tombstone_and_unstated_quota_are_factual(self):
        providers = [{"id": "gone", "name": "Gone API", "region": "国际"},
                     {"id": "live", "name": "Live API", "region": "国际"}]
        records = {
            "gone": {"source": "official", "has_image_generation": False,
                     "product_status": "discontinued",
                     "product_status_date": "2026-08-17",
                     "product_status_note": "Official shutdown",
                     "source_urls": ["https://official.example/deprecations"],
                     "offerings": []},
            "live": {"source": "official", "offerings": [{
                "name": "Current", "api_available": True, "modes": ["text-to-image"],
                "free_quota": None,
            }]},
        }
        section = build_site._imagegen_section(providers, records)
        self.assertIn("Gone API", section)
        self.assertIn("已于 2026-08-17 停止", section)
        self.assertIn("Official shutdown", section)
        self.assertIn('class="gen-row gen-tombstone"', section)
        self.assertIn("官网未说明", section)

    def test_mobile_generation_gallery_is_horizontal_and_details_jump_remains(self):
        self.assertIn(".gen-sample-card{flex:0 0 min(82vw,320px)", build_site.CSS)
        self.assertIn("body[data-view=imagegen] .jump-imagegen", build_site.CSS)
        self.assertIn("scroller.scrollLeft=Math.max", build_site.JS)
        self.assertEqual(build_site.JS.count("scrollIntoView"), 2)
        self.assertNotIn("x.scrollIntoView", build_site.JS)

    def test_small_official_decimal_is_not_rounded(self):
        self.assertEqual(build_site._fmt(.0035), "0.0035")

    def test_official_gallery_renders_media_and_rejects_unsafe_links(self):
        configs = self._configs() + [{
            "id": "unsafe", "name": "Unsafe", "region": "国际",
            "examples": [{"title": "bad", "url": "javascript:alert(1)"}],
        }]

        gallery = build_site._generation_examples("imagegen", configs, {})

        self.assertIn('src="https://official.example/sample.webp"', gallery)
        self.assertIn('href="https://official.example/gallery"', gallery)
        self.assertIn("查看厂商官方样例", gallery)
        self.assertNotIn("javascript:", gallery)
        self.assertNotIn(">bad<", gallery)

    def test_video_gallery_can_show_an_official_gif_as_image(self):
        configs = [{
            "id": "demo", "name": "Demo", "region": "国际",
            "examples": [{
                "title": "Official montage", "url": "https://example.com/gallery",
                "media_url": "https://example.com/montage.gif", "media_type": "image",
            }],
        }]

        gallery = build_site._generation_examples("videogen", configs, {})

        self.assertIn('<img src="https://example.com/montage.gif"', gallery)
        self.assertNotIn("<video", gallery)

    def test_fact_table_shows_api_status_sources_and_no_subjective_score(self):
        configs = [{
            "id": "demo", "name": "Demo", "region": "国际",
            "examples": [{"title": "Sample", "url": "https://example.com/sample"}],
        }]
        records = {"demo": {
            "source": "official_seed", "seed_verified": True,
            "source_url": "https://example.com/pricing",
            "offerings": [{
                "name": "Web-only model", "api_available": False,
                "modes": ["text-to-image"], "pricing": "$10/month",
                "currency": "USD", "price_per_image": None,
                "comparison_group": None, "resolution": "1024x1024",
                "free_quota": "none", "note": "automation prohibited",
            }],
        }}

        section = build_site._imagegen_section(configs, records)

        self.assertIn("AI 生图 · 价格与能力", section)
        self.assertIn("不做主观画质评分", section)
        self.assertIn("✗ 无公开 API", section)
        self.assertIn("官网事实种子 · 待自动校准", section)
        self.assertIn('href="https://example.com/pricing"', section)
        self.assertIn('href="https://example.com/sample"', section)
        self.assertNotIn("评分：", section)


class NewsCardTests(unittest.TestCase):
    def test_uses_official_homepage_and_resolves_relative_entry_url(self):
        card = build_site._news_card(
            {
                "id": "demo",
                "name": "Demo",
                "region": "国际",
                "news_url": "https://docs.example.com/changelog/index.html",
                "official_news_url": "https://www.example.com/news",
            },
            {"entries": [{
                "date": "2026-09-01",
                "title": "Demo Model 发布",
                "url": "/news/demo-model",
                "summary": "发布新模型。",
            }]},
        )

        self.assertIn('href="https://www.example.com/news"', card)
        self.assertIn('>官方公告 ↗</a>', card)
        self.assertIn('href="https://docs.example.com/news/demo-model"', card)


if __name__ == "__main__":
    unittest.main()
