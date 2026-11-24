"""過去の予定を足す ops/add_past.py のテスト。

すでに載っている予定を書き換えず、普段の記録（memos.json など）も変えないことを確かめる。
実行: python3 -m unittest discover -s tests
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ops"))
sys.path.insert(0, os.path.dirname(__file__))

import no_network  # noqa: E402,F401 — テストから外部に通信しない
import add_past  # noqa: E402
import fetch_schedule as fs  # noqa: E402

FIELDS = ("done", "date", "weekday", "open", "start", "end", "time_note", "title", "category", "members", "venue",
          "url", "venue_url", "memo", "uid", "perform")


def row(**values):
    return {**{k: "" for k in FIELDS}, "weekday": "日", "title": "ライブ", "category": "ライブ／イベント",
            "members": fs.GROUP, **values}


OLD = row(done="済", date="2026-09-24", url="https://example.invalid/old", memo="📍 会場: あり", uid="old1")
X_OLD = row(done="済", date="2026-10-01", url="https://x.com/takanenofficial/status/1", uid="old2")


class AddPastTest(unittest.TestCase):
    def test_new_rows_skips_listed(self):
        rows = [row(date="2026-09-13", url="https://example.invalid/a"),
                row(date="2026-09-24", url="https://example.invalid/old"),
                row(date="2026-10-01", url="https://example.invalid/b", event_url="https://x.com/takanenofficial/status/1")]
        self.assertEqual([r["url"] for r in add_past.new_rows(rows, [OLD, X_OLD])], ["https://example.invalid/a"])

    def test_combine_keeps_previous_rows(self):
        prev = [dict(OLD), dict(X_OLD)]
        rows = add_past.combine(prev, [row(date="2026-09-13", url="https://example.invalid/a")], date(2026, 11, 24))
        self.assertEqual(rows[1:], [OLD, X_OLD])
        self.assertEqual((rows[0]["date"], rows[0]["done"]), ("2026-09-13", "済"))
        self.assertNotIn(rows[0]["uid"], ("", "old1", "old2"))

    def test_memos_leave_usual_records(self):
        paths = fs.X_TEXTS_PATH, fs.OFFICIAL_TEXTS_PATH
        used = []

        def fake(rows, posts, today, cache_path):
            used.extend([fs.X_TEXTS_PATH, fs.OFFICIAL_TEXTS_PATH, cache_path, today])
            raise RuntimeError("失敗しても元に戻す")

        with mock.patch.object(fs, "add_memos", fake), self.assertRaises(RuntimeError):
            add_past.add_past_memos([], [], date(2026, 9, 13))
        self.assertEqual((fs.X_TEXTS_PATH, fs.OFFICIAL_TEXTS_PATH), paths)
        self.assertEqual(used[3], date(2026, 9, 13))
        self.assertFalse({*paths, "memos.json"} & set(used[:3]))

    def test_dry_run_writes_nothing(self):
        added = row(date="2026-09-13", url="https://example.invalid/a")
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                with mock.patch.object(add_past, "collect", lambda s, e: ([added], [])), \
                        mock.patch.object(add_past, "add_past_memos") as memos, contextlib.redirect_stdout(io.StringIO()):
                    add_past.main(["2026-09-13", "2026-09-24", "--dry-run"])
                self.assertFalse(memos.called)
                self.assertEqual(os.listdir(tmp), [])
            finally:
                os.chdir(cwd)

    def test_restore_perform_urls(self):
        rows = [row(date="2026-12-07", url="https://example.invalid/m", perform="19:50-20:15"),
                row(date="2026-12-08", url="https://example.invalid/n", perform="18:00-18:20")]
        saved = {"2026-12-07|https://example.invalid/m": {"perform": "19:50-20:15", "posted": "p", "url": "https://x.com/1"},
                 "2026-12-08|https://example.invalid/n": {"perform": "17:00-17:20", "posted": "p", "url": "https://x.com/2"}}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "perform.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(saved, f)
            add_past.restore_perform_urls(rows, path)
        self.assertEqual(fs.md_time_label(rows[0]), "[🎤 出演 19:50〜20:15](https://x.com/1)")
        self.assertNotIn("perform_url", rows[1])

    def test_future_is_refused(self):
        with self.assertRaises(SystemExit):
            add_past.main(["2026-09-13", "9999-01-01"])


if __name__ == "__main__":
    unittest.main()
