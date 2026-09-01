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

    @patch.object(run, "fetch", return_value="plain news")
    def test_uses_plain_fetch_by_default(self, _fetch):
        text = run._fetch_news_text({"news_url": "https://example.com/news"})

        self.assertEqual(text, "plain news")


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


if __name__ == "__main__":
    unittest.main()
