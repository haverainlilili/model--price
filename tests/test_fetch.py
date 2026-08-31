import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scraper.fetch import FetchError, _find_chrome_executable


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


if __name__ == "__main__":
    unittest.main()
