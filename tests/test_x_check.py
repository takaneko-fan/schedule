"""公式 X の確認（ops/check_x.py）と、公式サイトの内容の保存（「公式 X だけ」の更新）のテスト。

X API・GitHub への起動・公式サイトには接続しない。
実行: python3 -m unittest discover -s tests
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import no_network  # noqa: E402,F401 — テストから外部に通信しない

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ops"))

import check_x  # noqa: E402
import fetch_schedule as fs  # noqa: E402

NOW = datetime.fromisoformat("2026-11-20T21:30:00+09:00")


class SameSettingsTest(unittest.TestCase):
    def test_check_x_uses_same_settings_as_fetch_schedule(self):
        """check_x.py は VPS でリポジトリなしに動くので値を別に持つ。fetch_schedule.py と食い違わないようにする。"""
        self.assertEqual(check_x.X_USER_ID, fs.X_USER_ID)
        self.assertEqual(check_x.X_API_URL, fs.X_API_URL)
        self.assertEqual(check_x.X_STATE_PATH, fs.X_STATE_PATH)
        self.assertEqual(check_x.ENV_PATH, fs.CONFIG_ENV_PATH)


class ShouldDispatchTest(unittest.TestCase):
    def test_rules(self):
        self.assertFalse(check_x.should_dispatch("", {}, NOW))  # 新しい投稿なし
        self.assertTrue(check_x.should_dispatch("200", {}, NOW))
        self.assertTrue(check_x.should_dispatch("201", {"dispatched": "200", "at": "2026-11-20T21:25:00+09:00"}, NOW))
        # 同じ投稿で起動済みなら、30 分たつまでは起動し直さない（起動した更新の読み込み待ち）
        self.assertFalse(check_x.should_dispatch("200", {"dispatched": "200", "at": "2026-11-20T21:10:00+09:00"}, NOW))
        self.assertTrue(check_x.should_dispatch("200", {"dispatched": "200", "at": "2026-11-20T20:55:00+09:00"}, NOW))


class MainTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = os.path.join(tmp.name, "x_state.json")
        self.check = os.path.join(tmp.name, "x_check.json")
        with open(self.state, "w", encoding="utf-8") as f:
            json.dump({"since_id": "100"}, f)
        for p in (mock.patch.object(check_x, "X_STATE_PATH", self.state), mock.patch.object(check_x, "CHECK_STATE_PATH", self.check),
                  mock.patch.object(check_x, "load_token", return_value="token")):
            p.start()
            self.addCleanup(p.stop)

    def run_main(self, newest=None, error=None):
        with mock.patch.object(check_x, "newest_post_id", return_value=newest, side_effect=error) as read, \
                mock.patch.object(check_x.subprocess, "run") as run, contextlib.redirect_stdout(io.StringIO()) as out:
            code = check_x.main([], NOW)
        return code, read, run, out.getvalue()

    def test_dispatch_once_for_new_post(self):
        code, read, run, _ = self.run_main("200")
        self.assertEqual((code, read.call_args[0][1], run.call_count), (0, "100", 1))
        self.assertEqual(run.call_args[0][0][1:], ["--x-only"])
        code, _, run, out = self.run_main("200")  # 次の回（10 分後）はまだ読み込み待ち
        self.assertEqual((code, run.call_count), (0, 0))
        self.assertIn("起動済み", out)

    def test_no_new_post(self):
        code, _, run, out = self.run_main("")
        self.assertEqual((code, run.call_count), (0, 0))
        self.assertIn("新しい投稿なし", out)

    def test_api_error_waits_for_next(self):
        code, _, run, out = self.run_main(error=OSError("HTTP Error 429: Too Many Requests"))
        self.assertEqual((code, run.call_count), (0, 0))
        self.assertIn("確かめられませんでした", out)

    def test_no_state_yet(self):
        os.remove(self.state)
        code, read, run, _ = self.run_main("200")
        self.assertEqual((code, read.call_count, run.call_count), (0, 0, 0))


class LoadTokenTest(unittest.TestCase):
    def test_reads_env_file(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, ".env")
        with open(path, "w", encoding="utf-8") as f:
            f.write("OPENAI_API_KEY=x\nX_BEARER_TOKEN=\"abc\"\n")
        with mock.patch.dict(os.environ, {"X_BEARER_TOKEN": ""}):
            self.assertEqual(check_x.load_token(path), "abc")


class SetupVpsTest(unittest.TestCase):
    def test_setup_vps_installs_existing_files(self):
        """ops/setup_vps.sh が取ってくるファイルが ops/ にあり、タイマーとサービスの組がそろっている。"""
        import re
        ops = os.path.join(os.path.dirname(__file__), "..", "ops")
        with open(os.path.join(ops, "setup_vps.sh"), encoding="utf-8") as f:
            script = f.read()
        names = re.findall(r"for f in ([^;]+);", script)[0].split() + re.search(r'UNITS="([^"]+)"', script).group(1).split()
        for name in names:
            self.assertTrue(os.path.exists(os.path.join(ops, name)), name)
        with open(os.path.join(ops, "takaneko-xcheck.service"), encoding="utf-8") as f:
            self.assertIn("/home/runner/bin/check_x.py", f.read())


class SnapshotTest(unittest.TestCase):
    def test_save_and_load_with_age(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "snapshot.json")
        self.assertIsNone(fs.load_official_snapshot(NOW, path))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            fs.save_official_snapshot(20, [{"date": "2026-11-21", "title": "A"}], [{"link": "u"}], [], path)
        self.assertIn("公式サイトの内容を保存（公式 X だけの更新で使う）: イベント 20 件・お知らせ記事 1 件・Eventernote 0 件", out.getvalue())
        snapshot = fs.load_official_snapshot(datetime.now(fs.JST), path)
        self.assertEqual((snapshot["events_count"], snapshot["rows"][0]["title"]), (20, "A"))
        self.assertIsNone(fs.load_official_snapshot(datetime.now(fs.JST) + timedelta(hours=25), path))  # 古ければ使わない


if __name__ == "__main__":
    unittest.main()
