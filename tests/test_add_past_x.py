"""過去の予定に公式 X の情報を足す ops/add_past_x.py のテスト。

X API・AI は呼ばない。期間の外の予定とふだんの公式 X の記録を変えないことを確かめる。
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
import add_past_x  # noqa: E402
import fetch_schedule as fs  # noqa: E402

FIELDS = ("done", "date", "weekday", "open", "start", "end", "time_note", "title", "category", "members", "venue",
          "url", "venue_url", "memo", "uid", "perform")
START, END = date(2026, 9, 13), date(2026, 9, 24)


def row(**values):
    return {**{k: "" for k in FIELDS}, "weekday": "水", "title": "MARQUEE Fes.", "category": "ライブ／イベント",
            "members": fs.GROUP, "done": "済", **values}


def event(**values):
    return {"date": "2026-09-18", "open": "", "start": "21:00", "end": "22:00", "time_note": "", "title": "カリメン",
            "members": fs.GROUP, "venue": "", "url": "https://x.com/takanenofficial/status/9", "names": [], "media": "ラジオ",
            "station": "RKBラジオ", "post_id": "9", "posted": "2026-09-15T12:00", **values}


class FetchPeriodTest(unittest.TestCase):
    def test_period_sets_start_and_end_time(self):
        seen = {}

        def fake_urlopen(req, timeout):
            seen["url"] = req.full_url
            return io.BytesIO(json.dumps({"data": [], "meta": {}}).encode())

        period = tuple(fs.datetime.combine(d, fs.datetime.min.time(), fs.JST) for d in (START, END))
        with mock.patch.object(fs.urllib.request, "urlopen", fake_urlopen):
            fs.fetch_x_posts("token", period=period)
        self.assertIn("start_time=2026-09-12T15%3A00%3A00Z", seen["url"])
        self.assertIn("end_time=2026-09-23T15%3A00%3A00Z", seen["url"])
        self.assertNotIn("since_id", seen["url"])


class ApplyPeriodTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name

    def apply(self, period, events, notices=()):
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(fs, "load_x_texts", return_value={}):
            return add_past_x.apply_period(period, events, list(notices), START, END, self.tmp)

    def test_adds_x_only_event_in_period(self):
        listed = row(date="2026-09-16", url="https://example.invalid/marquee", start="14:30", uid="u1")
        merged, added = self.apply([listed], [event(), event(date="2026-09-30", post_id="10")])  # 期間の外は足さない
        self.assertEqual([(r["date"], r["title"]) for r in added], [("2026-09-18", "カリメン（RKBラジオ）")])
        self.assertEqual(len(merged), 2)

    def test_fills_empty_venue_of_listed_event(self):
        listed = row(date="2026-09-16", url="https://example.invalid/marquee", venue="東京都内某所（詳細は後日発表）", uid="u1")
        x = event(date="2026-09-16", title="MARQUEE Fes.", venue="Spotify O-EAST", start="", end="", media="", station="")
        merged, added = self.apply([listed], [x])
        self.assertEqual((added, merged[0]["venue"]), ([], "Spotify O-EAST"))

    def test_cancel_notice_marks_event(self):
        listed = row(date="2026-09-20", title="IDOL RUNWAY COLLECTION", url="https://example.invalid/irc", uid="u1")
        notice = {"date": "2026-09-20", "status": "cancel", "label": "中止", "names": ["IDOL RUNWAY COLLECTION"],
                  "members": [], "substitutes": [], "url": "https://x.com/takanenofficial/status/5", "posted": "2026-09-19T10:00"}
        merged, _ = self.apply([listed], [], [notice])
        self.assertEqual(merged[0]["title"], "【中止】IDOL RUNWAY COLLECTION")


class ReadPeriodTest(unittest.TestCase):
    def test_does_not_touch_usual_records(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        usual = fs.X_TEXTS_PATH, fs.X_TEXTS_KEEP_DAYS, fs.fetch_x_posts
        with mock.patch.object(fs, "read_x_posts", return_value=[]) as read:
            add_past_x.read_period([{"id": "1"}], [], tmp.name)
        self.assertEqual(read.call_args.kwargs["recheck_days"], 1)  # 前回の続きの記録（x_state.json）は書かない
        self.assertEqual((fs.X_TEXTS_PATH, fs.X_TEXTS_KEEP_DAYS, fs.fetch_x_posts), usual)


if __name__ == "__main__":
    unittest.main()
