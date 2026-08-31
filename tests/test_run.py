import unittest
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


if __name__ == "__main__":
    unittest.main()
