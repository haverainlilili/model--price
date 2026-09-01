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
            "https://example.com/news", preserve_links=True)

    @patch.object(run, "fetch", return_value="plain news")
    def test_uses_plain_fetch_by_default(self, _fetch):
        text = run._fetch_news_text({"news_url": "https://example.com/news"})

        self.assertEqual(text, "plain news")


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


if __name__ == "__main__":
    unittest.main()
