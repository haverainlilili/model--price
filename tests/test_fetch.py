import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scraper.fetch import (
    FetchError,
    _chrome_launch_options,
    _find_chrome_executable,
    _render_once,
    fetch_rendered,
    html_to_text,
)


class FindChromeExecutableTests(unittest.TestCase):
    def test_prefers_configured_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "chrome"
            executable.touch(mode=0o700)
            with patch.dict(os.environ, {"CHROME_EXECUTABLE": str(executable)}):
                self.assertEqual(_find_chrome_executable(), str(executable))

    def test_rejects_invalid_configured_executable(self):
        with patch.dict(os.environ, {"CHROME_EXECUTABLE": "/missing/chrome"}):
            with self.assertRaisesRegex(FetchError, "CHROME_EXECUTABLE 不可执行"):
                _find_chrome_executable()

    def test_disables_quic_for_rendered_pages(self):
        with patch("scraper.fetch._find_chrome_executable", return_value=None):
            options = _chrome_launch_options()

        self.assertIn("--disable-quic", options["args"])


class FetchRenderedTests(unittest.TestCase):
    @patch("scraper.fetch.html_to_text", return_value="公告正文")
    @patch("playwright.sync_api.sync_playwright")
    def test_parses_rendered_body_html_to_keep_links(
            self, sync_playwright, html_to_text):
        playwright = sync_playwright.return_value.__enter__.return_value
        browser = playwright.chromium.launch.return_value
        page = browser.new_context.return_value.new_page.return_value
        page.locator.return_value.inner_html.return_value = (
            '<a href="/news/model">模型发布</a>'
        )

        text = _render_once("https://example.com/news", wait_ms=0)

        self.assertEqual(text, "公告正文")
        page.locator.assert_called_once_with("body")
        html_to_text.assert_called_once_with(
            '<a href="/news/model">模型发布</a>'
        )
        browser.close.assert_called_once()

    @patch("scraper.fetch.time.sleep")
    @patch("scraper.fetch._render_once")
    def test_retries_transient_navigation_failure(self, render_once, sleep):
        render_once.side_effect = [FetchError("ERR_CONNECTION_CLOSED"), "价格正文"]

        self.assertEqual(fetch_rendered("https://example.com", wait_ms=0, retries=1),
                         "价格正文")
        self.assertEqual(render_once.call_count, 2)
        sleep.assert_called_once_with(2)

    @patch("scraper.fetch.time.sleep")
    @patch("scraper.fetch._render_once")
    def test_raises_last_error_after_retries(self, render_once, sleep):
        render_once.side_effect = FetchError("still closed")

        with self.assertRaisesRegex(FetchError, "still closed"):
            fetch_rendered("https://example.com", wait_ms=0, retries=2)
        self.assertEqual(render_once.call_count, 3)
        self.assertEqual(sleep.call_count, 2)


class HtmlToTextTests(unittest.TestCase):
    def test_preserves_absolute_and_relative_anchor_targets(self):
        text = html_to_text(
            '<p><a href="https://example.com/news/one">第一条</a></p>'
            '<p><a href="/news/two">第二条</a></p>'
        )

        self.assertIn('[第一条](https://example.com/news/one)', text)
        self.assertIn('[第二条](/news/two)', text)


if __name__ == "__main__":
    unittest.main()
