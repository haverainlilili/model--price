import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scraper import run


class FetchNewsTextTests(unittest.TestCase):
    @patch.object(run, "fetch_rendered", return_value="rendered news")
    @patch.object(run, "fetch", side_effect=AssertionError("plain fetch must not run"))
    def test_uses_browser_for_rendered_news_sources(self, _fetch, _fetch_rendered):
        text = run._fetch_news_text({
            "news_url": "https://example.com/news",
            "news_render": True,
        })

        self.assertEqual(text, "rendered news")
        _fetch_rendered.assert_called_once_with(
            "https://example.com/news", preserve_links=True,
            language="zh-CN")

    @patch.object(run, "fetch", return_value="plain news")
    def test_uses_plain_fetch_by_default(self, _fetch):
        text = run._fetch_news_text({"news_url": "https://example.com/news"})

        self.assertEqual(text, "plain news")
        _fetch.assert_called_once_with(
            "https://example.com/news", language="zh-CN")


class FetchPlanTextTests(unittest.TestCase):
    @patch.object(run, "fetch_rendered", return_value="rendered plan")
    @patch.object(run, "fetch", side_effect=AssertionError("plain fetch must not run"))
    def test_uses_browser_for_rendered_plan_sources(self, _fetch, _fetch_rendered):
        url, text = run._fetch_plan_text({
            "plan_url": "https://example.com/plans",
            "plan_render": True,
        })

        self.assertEqual(url, "https://example.com/plans")
        self.assertIn("rendered plan", text)
        _fetch_rendered.assert_called_once_with(
            "https://example.com/plans", language="zh-CN")

    @patch.object(run, "fetch", side_effect=["price page", "quota page"])
    def test_combines_multiple_official_plan_sources(self, _fetch):
        url, text = run._fetch_plan_text({
            "plan_urls": [
                "https://example.com/price",
                "https://example.com/quota",
            ],
        })

        self.assertEqual(url, "https://example.com/price")
        self.assertIn("price page", text)
        self.assertIn("quota page", text)
        self.assertEqual(
            _fetch.call_args_list,
            [
                unittest.mock.call(
                    "https://example.com/price", language="zh-CN"),
                unittest.mock.call(
                    "https://example.com/quota", language="zh-CN"),
            ],
        )


class FetchLanguageSelectionTests(unittest.TestCase):
    @patch.object(run, "fetch", return_value="English pricing")
    def test_international_provider_uses_english(self, _fetch):
        run._fetch_pricing_text({
            "pricing_url": "https://example.com/pricing",
            "region": "国际",
        })

        _fetch.assert_called_once_with(
            "https://example.com/pricing", language="en-US")

    @patch.object(run, "fetch", return_value="中文价格")
    def test_domestic_provider_uses_chinese(self, _fetch):
        run._fetch_pricing_text({
            "pricing_url": "https://example.cn/pricing",
            "region": "国内",
        })

        _fetch.assert_called_once_with(
            "https://example.cn/pricing", language="zh-CN")


class OfficialPlanUrlTests(unittest.TestCase):
    def test_accepts_another_official_subdomain(self):
        self.assertEqual(
            run._official_plan_url(
                {
                    "plan_url": "https://help.aliyun.com/model-studio/plans",
                },
                "https://common-buy.aliyun.com/official-package",
            ),
            "https://common-buy.aliyun.com/official-package",
        )

    def test_rejects_affiliate_or_community_domains(self):
        self.assertIsNone(
            run._official_plan_url(
                {"plan_url": "https://support.claude.com/plans"},
                "https://affiliate.example/ref/claude",
            )
        )


class NewsFingerprintTests(unittest.TestCase):
    def test_ignores_dynamic_page_noise_and_link_order(self):
        first = (
            "当前时间 10:01\n"
            "[模型 A](/news/model-a)\n"
            "[模型 B](https://example.com/news/model-b)"
        )
        second = (
            "随机推荐内容 999\n"
            "[模型 B](https://example.com/news/model-b)\n"
            "[模型 A](/news/model-a)"
        )

        self.assertEqual(
            run._news_fingerprint("https://example.com/news", first),
            run._news_fingerprint("https://example.com/news", second),
        )

    def test_changes_when_an_announcement_link_changes(self):
        before = "[模型 A](/news/model-a)"
        after = before + "\n[模型 B](/news/model-b)"

        self.assertNotEqual(
            run._news_fingerprint("https://example.com/news", before),
            run._news_fingerprint("https://example.com/news", after),
        )


class ProviderConfigurationTests(unittest.TestCase):
    def test_every_provider_has_crawl_source_and_official_news_homepage(self):
        for provider in run.load_providers():
            with self.subTest(provider=provider["id"]):
                self.assertTrue(provider.get("news_url"))
                self.assertTrue(provider.get("official_news_url"))

    def test_international_providers_explicitly_use_english(self):
        international = [
            provider for provider in run.load_providers()
            if provider.get("region") == "国际"
        ]

        self.assertTrue(international)
        for provider in international:
            with self.subTest(provider=provider["id"]):
                self.assertEqual(provider.get("language"), "en-US")

    def test_google_sources_pin_the_english_locale(self):
        google = next(
            provider for provider in run.load_providers()
            if provider["id"] == "google"
        )

        self.assertIn("hl=en", google["pricing_url"])
        self.assertIn("hl=en", google["news_url"])


class ProcessProviderTests(unittest.TestCase):
    @patch.object(run.history, "append_changes")
    @patch.object(run.history, "save_provider")
    @patch.object(run.extract, "extract_pricing")
    @patch.object(run.extract, "has_api_key", return_value=True)
    @patch.object(run, "_fetch_pricing_text",
                  return_value=("https://example.com/pricing", "new page"))
    @patch.object(run.history, "load_provider")
    def test_empty_extraction_preserves_previous_models_and_records_hash(
            self, load_provider, _fetch_pricing_text, _has_api_key,
            extract_pricing, save_provider, append_changes):
        previous_models = [{
            "model": "Example Pro",
            "input_per_1m": 3,
            "output_per_1m": 15,
        }]
        load_provider.return_value = {
            "source": "claude",
            "models": previous_models,
            "price_hash": "old-hash",
        }
        extract_pricing.return_value = SimpleNamespace(
            models=[],
            currency="USD",
            promotions=None,
            page_has_pricing=False,
        )

        run.process_provider({
            "id": "example",
            "name": "Example",
            "pricing_url": "https://example.com/pricing",
        })

        saved = save_provider.call_args.args[1]
        self.assertEqual(saved["models"], previous_models)
        self.assertEqual(saved["price_hash"], run._sha("new page"))
        self.assertIn("保留上次", saved["status_note"])
        self.assertIn("保留上次", saved["last_error"])
        append_changes.assert_not_called()


class ProcessNewsTests(unittest.TestCase):
    @patch.object(run.history, "save_news")
    @patch.object(run.extract, "extract_news")
    @patch.object(run.extract, "has_api_key", return_value=True)
    @patch.object(run, "_fetch_news_text", return_value="changed news page")
    @patch.object(run.history, "load_news", return_value={"entries": []})
    def test_resolves_relative_entry_url_before_saving(
            self, _load_news, _fetch_news_text, _has_api_key,
            extract_news, save_news):
        extract_news.return_value = SimpleNamespace(entries=[SimpleNamespace(
            model_dump=lambda: {
                "date": "2026-09-01",
                "title": "Demo Model 发布",
                "url": "/news/demo-model",
                "summary": "发布新模型。",
            }
        )])

        run.process_news({
            "id": "demo",
            "name": "Demo",
            "news_url": "https://docs.example.com/changelog/index.html",
        })

        saved = save_news.call_args.args[1]
        self.assertEqual(
            saved["entries"][0]["url"],
            "https://docs.example.com/news/demo-model",
        )
        self.assertEqual(
            saved["news_hash"],
            run._news_fingerprint(
                "https://docs.example.com/changelog/index.html",
                "changed news page",
            ),
        )


class ProcessPlansTests(unittest.TestCase):
    @patch.object(run.history, "save_plans")
    @patch.object(run.extract, "extract_plans")
    @patch.object(run.extract, "has_api_key", return_value=True)
    @patch.object(run, "_fetch_plan_text",
                  return_value=("https://example.com/plans", "changed plans"))
    @patch.object(run.history, "load_plans")
    def test_empty_extraction_preserves_previous_official_plans_and_hash(
            self, load_plans, _fetch_plan_text, _has_api_key,
            extract_plans, save_plans):
        previous = [{
            "name": "Plus",
            "price": "¥49 / 月",
            "quotas": [{"label": "额度", "value": "1,500 次"}],
        }]
        load_plans.return_value = {
            "source": "official",
            "plans": previous,
            "plans_hash": "old-hash",
        }
        extract_plans.return_value = SimpleNamespace(
            plans=[],
            page_has_plans=False,
        )

        run.process_plans({
            "id": "example",
            "name": "Example",
            "plan_url": "https://example.com/plans",
        })

        saved = save_plans.call_args.args[1]
        self.assertEqual(saved["plans"], previous)
        self.assertEqual(saved["plans_hash"], run._sha("changed plans"))
        self.assertIn("保留上次", saved["status_note"])

    @patch.object(run.history, "save_plans")
    @patch.object(run.extract, "extract_plans")
    @patch.object(run.extract, "has_api_key", return_value=True)
    @patch.object(run, "_fetch_plan_text",
                  return_value=("https://example.com/plans", "same plans"))
    @patch.object(run.history, "load_plans")
    def test_unchanged_page_skips_plan_extraction(
            self, load_plans, _fetch_plan_text, _has_api_key,
            extract_plans, save_plans):
        load_plans.return_value = {
            "plans": [{"name": "Plus"}],
            "plans_hash": run._sha("same plans"),
        }

        run.process_plans({
            "id": "example",
            "name": "Example",
            "plan_url": "https://example.com/plans",
        })

        extract_plans.assert_not_called()
        self.assertEqual(save_plans.call_args.args[1]["status_note"], "页面无变化")


if __name__ == "__main__":
    unittest.main()
