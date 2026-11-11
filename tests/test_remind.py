"""remind.py（たかねこリマインド）の対象・本文・二重送信の防止のテスト。

main() は一時ディレクトリの deadlines.csv と記録ファイルを使い、ntfy には送らない。
実行: python3 -m unittest discover -s tests
"""

import contextlib
import csv
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import remind  # noqa: E402

FIELDS = ["done", "date", "weekday", "time", "kind", "event", "detail", "note", "url", "label", "time_text", "event_short"]


def deadline(**values):
    base = {k: "" for k in FIELDS}
    base.update(date="2026-11-15", time="23:59", kind="受付締切", event="11/20 オンラインサイン会", detail="受付期間",
                url="https://example.invalid/news/1")
    base.update(values)
    return base


class IsTargetTest(unittest.TestCase):
    def test_targets(self):
        self.assertTrue(remind.is_target(deadline(kind="受付締切")))
        self.assertTrue(remind.is_target(deadline(kind="入金締切")))
        self.assertTrue(remind.is_target(deadline(kind="受付開始", detail="先着販売期間")))

    def test_not_targets(self):
        self.assertFalse(remind.is_target(deadline(kind="受付開始", detail="抽選受付")))
        self.assertFalse(remind.is_target(deadline(kind="当落発表")))
        self.assertFalse(remind.is_target(deadline(kind="一般発売")))


class BuildMessageTest(unittest.TestCase):
    def test_sorted_by_time_and_capped(self):
        rows = [deadline(event=f"イベント{i}", event_short=f"イベント{i}", label="抽選の締切", time=f"{10 + i}:00",
                         time_text=f"{10 + i}:00まで") for i in reversed(range(6))]
        rows.append(deadline(event="時刻なし", event_short="時刻なし", label="入金締切", time=""))
        lines = remind.build_message(rows, "2026-11-15").splitlines()
        self.assertEqual(lines[0], "📅 今日（11/15）の申込")
        self.assertEqual(lines[1:5], ["▶ イベント0", "　抽選の締切 10:00まで", "▶ イベント1", "　抽選の締切 11:00まで"])
        self.assertEqual(len(lines), 1 + remind.MAX_ITEMS * 2 + 1)
        self.assertEqual(lines[-1], "ほか 3 件は Web 版で確認してください")

    def test_click_url(self):
        self.assertEqual(remind.click_url([deadline(), deadline(kind="入金締切")]), "https://example.invalid/news/1")
        self.assertEqual(remind.click_url([deadline(), deadline(url="https://example.invalid/news/2")]), remind.SITE_URL)


class MainTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        cwd = os.getcwd()
        os.chdir(self.dir.name)
        self.addCleanup(os.chdir, cwd)
        self.state = os.path.join(self.dir.name, "state", "remind_state.json")
        for patcher in (mock.patch.object(remind, "REMIND_STATE_PATH", self.state),
                        mock.patch.dict(os.environ, {"NTFY_REMIND_TOPIC": "test-topic"})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.send = mock.patch.object(remind, "send").start()
        self.addCleanup(mock.patch.stopall)

    def write_deadlines(self, rows):
        with open("deadlines.csv", "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def run_main(self, now):
        with mock.patch.object(sys, "argv", ["remind.py", "--now", now]), contextlib.redirect_stdout(io.StringIO()):
            remind.main()

    def test_not_sent_before_remind_hour(self):
        self.write_deadlines([deadline()])
        self.run_main("2026-11-15T00:17")
        self.send.assert_not_called()

    def test_sent_once_and_only_new_items_later(self):
        self.write_deadlines([deadline()])
        self.run_main("2026-11-15T09:17")
        self.assertEqual(self.send.call_count, 1)
        self.run_main("2026-11-15T12:17")
        self.assertEqual(self.send.call_count, 1)  # 同じ項目は送らない

        self.write_deadlines([deadline(), deadline(kind="入金締切", time="21:00")])
        self.run_main("2026-11-15T16:17")
        self.assertEqual(self.send.call_count, 2)
        message = self.send.call_args.args[1]
        self.assertIn("入金締切 21:00まで", message)
        self.assertNotIn("受付締切", message)
        with open(self.state, encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["sent"]), 2)

    def test_other_day_and_non_targets_are_not_sent(self):
        self.write_deadlines([deadline(date="2026-11-16"), deadline(kind="当落発表")])
        self.run_main("2026-11-15T09:17")
        self.send.assert_not_called()

    def test_record_of_previous_day_is_reset(self):
        self.write_deadlines([deadline(), deadline(date="2026-11-16")])
        self.run_main("2026-11-15T09:17")
        self.run_main("2026-11-16T09:17")
        self.assertEqual(self.send.call_count, 2)


if __name__ == "__main__":
    unittest.main()
