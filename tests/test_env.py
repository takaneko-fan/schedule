"""fetch_schedule.load_env（API キーの .env の読み込み）のテスト。

実行: python3 -m unittest discover -s tests
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fetch_schedule as fs  # noqa: E402


class LoadEnvTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo_env = os.path.join(tmp.name, "repo.env")
        self.config_env = os.path.join(tmp.name, "config.env")
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ("TEST_KEY_A", "TEST_KEY_B"):
            os.environ.pop(key, None)

    def write(self, path, text):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_repo_env_is_used_first(self):
        self.write(self.repo_env, "TEST_KEY_A=repo\n")
        self.write(self.config_env, "TEST_KEY_A=config\nTEST_KEY_B=config\n")
        fs.load_env(self.repo_env, self.config_env)
        self.assertEqual(os.environ["TEST_KEY_A"], "repo")
        self.assertNotIn("TEST_KEY_B", os.environ)  # リポジトリの .env があれば外の .env は読まない

    def test_config_env_is_used_when_repo_env_is_missing(self):
        self.write(self.config_env, "# コメント\nTEST_KEY_A = config\n")
        fs.load_env(self.repo_env, self.config_env)
        self.assertEqual(os.environ["TEST_KEY_A"], "config")

    def test_environment_wins(self):
        os.environ["TEST_KEY_A"] = "env"
        self.write(self.config_env, "TEST_KEY_A=config\n")
        fs.load_env(self.repo_env, self.config_env)
        self.assertEqual(os.environ["TEST_KEY_A"], "env")

    def test_nothing_to_read(self):
        fs.load_env(self.repo_env, self.config_env)
        self.assertNotIn("TEST_KEY_A", os.environ)


if __name__ == "__main__":
    unittest.main()
