import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PUBLISH_SCRIPT = ROOT / "scripts" / "publish_updates.sh"


def git(*args, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()


class PublishUpdatesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.remote = base / "remote.git"
        self.repo = base / "repo"
        git("init", "--bare", str(self.remote), cwd=base)
        git("init", "-b", "main", str(self.repo), cwd=base)
        git("config", "user.name", "Test User", cwd=self.repo)
        git("config", "user.email", "test@example.com", cwd=self.repo)
        git("remote", "add", "origin", str(self.remote), cwd=self.repo)

        (self.repo / "data").mkdir()
        (self.repo / "site").mkdir()
        (self.repo / "data" / "prices.json").write_text(
            '{"price": 1}\n', encoding="utf-8")
        (self.repo / "site" / "index.html").write_text(
            "old site\n", encoding="utf-8")
        (self.repo / "README.md").write_text("original\n", encoding="utf-8")
        git("add", ".", cwd=self.repo)
        git("commit", "-m", "initial", cwd=self.repo)
        git("push", "-u", "origin", "main", cwd=self.repo)

    def tearDown(self):
        self.tmp.cleanup()

    def run_publish(self, check=True) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "MODEL_PRICE_REPO_DIR": str(self.repo),
            "MODEL_PRICE_PUBLISH_BRANCH": "main",
            "MODEL_PRICE_PYTHON": "/usr/bin/true",
        }
        return subprocess.run(
            [str(PUBLISH_SCRIPT)], env=env, check=check, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def test_commits_only_data_and_site_then_pushes_main(self):
        (self.repo / "data" / "prices.json").write_text(
            '{"price": 2}\n', encoding="utf-8")
        (self.repo / "site" / "index.html").write_text(
            "new site\n", encoding="utf-8")
        (self.repo / "README.md").write_text(
            "must stay local\n", encoding="utf-8")
        (self.repo / ".env").write_text(
            "OPENAI_API_KEY=must-not-leak\n", encoding="utf-8")

        self.run_publish()

        self.assertEqual(
            git("show", "origin/main:data/prices.json", cwd=self.repo),
            '{"price": 2}',
        )
        self.assertEqual(
            git("show", "origin/main:site/index.html", cwd=self.repo),
            "new site",
        )
        self.assertEqual(
            git("show", "origin/main:README.md", cwd=self.repo),
            "original",
        )
        remote_files = git("ls-tree", "-r", "--name-only", "origin/main",
                           cwd=self.repo).splitlines()
        self.assertNotIn(".env", remote_files)

    def test_skips_commit_when_data_and_site_are_unchanged(self):
        before = git("rev-parse", "origin/main", cwd=self.repo)

        result = self.run_publish()

        self.assertIn("没有 data/site 变化", result.stdout)
        self.assertEqual(git("rev-parse", "origin/main", cwd=self.repo), before)

    def test_retries_a_previously_failed_push_without_new_file_changes(self):
        (self.repo / "data" / "prices.json").write_text(
            '{"price": 2}\n', encoding="utf-8")
        git("remote", "set-url", "origin", str(self.remote) + ".missing",
            cwd=self.repo)

        failed = self.run_publish(check=False)
        self.assertNotEqual(failed.returncode, 0)

        git("remote", "set-url", "origin", str(self.remote), cwd=self.repo)
        self.run_publish()

        self.assertEqual(
            git("show", "origin/main:data/prices.json", cwd=self.repo),
            '{"price": 2}',
        )


if __name__ == "__main__":
    unittest.main()
