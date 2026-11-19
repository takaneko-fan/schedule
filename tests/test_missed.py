"""会場を取り込めていない可能性（find_missed_venues とその通知）のテスト。

記事・投稿は実際の書き方に合わせて短く作り直したもの（告知文は転載しない）。
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
sys.path.insert(0, os.path.dirname(__file__))

import no_network  # noqa: E402,F401 — テストから外部に通信しない

import fetch_schedule as fs  # noqa: E402
import notify  # noqa: E402
from fetch_schedule import GROUP  # noqa: E402

TODAY = date(2026, 11, 16)


def row(**values):
    base = {"date": "2026-11-22", "title": "リリース記念個別特典イベント", "category": "ライブ／イベント",
            "members": GROUP, "venue": "東京都内某所", "url": "https://example.invalid/schedule/1"}
    base.update(values)
    return base


def post(*lines, title="【11月22日(日)】リリース記念個別特典イベント 当日販売&会場限定抽選会開催のお知らせ",
         link="https://example.invalid/news/1", published="2026-11-16T20:05:00"):
    return {"date": published, "link": link, "title": {"rendered": title},
            "content": {"rendered": "".join(f"<p>{ln}</p>" for ln in lines)}}


def tweet(text, tweet_id="100", created_at="2026-11-16T11:00:00.000Z"):
    return {"id": tweet_id, "created_at": created_at, "text": text}


def missed(rows, posts=(), hints=()):
    with contextlib.redirect_stdout(io.StringIO()):
        return fs.find_missed_venues(rows, list(posts), list(hints), TODAY)


class ArticleHintTest(unittest.TestCase):
    def test_heading_that_rules_cannot_read(self):
        m = missed([row()], [post("▼会場", "ベルサール飯田橋駅前")])
        self.assertEqual(len(m), 1)
        self.assertIn("「▼会場」の記載（ベルサール飯田橋駅前）", m[0]["label"])
        self.assertEqual(m[0]["url"], "https://example.invalid/news/1")

    def test_place_label(self):
        self.assertEqual(len(missed([row()], [post("開催場所：ベルサール飯田橋駅前")])), 1)

    def test_tbd_value_is_not_reported(self):
        self.assertEqual(missed([row()], [post("■会場", "東京都内某所（詳細は後日発表）")]), [])

    def test_other_headings_are_not_hints(self):
        self.assertEqual(missed([row()], [post("■会場限定抽選会", "対象商品をご購入の方…", "会場内は撮影禁止です。")]), [])

    def test_decided_venue_is_not_reported(self):
        self.assertEqual(missed([row(venue="ベルサール飯田橋駅前")], [post("■会場", "ベルサール飯田橋駅前")]), [])

    def test_past_and_media_rows_are_not_reported(self):
        self.assertEqual(missed([row(date="2026-11-15")], [post("■会場", "ベルサール飯田橋駅前",
                                                                 title="【11月15日(日)】リリース記念個別特典イベント")]), [])
        self.assertEqual(missed([row(category="公式X / 配信")], [post("■会場", "ベルサール飯田橋駅前")]), [])


class XHintTest(unittest.TestCase):
    TEXT = "°˖✿ リリイベ情報更新 ✿˖°\n\n🗓️11月22日(日) 個別握手会\n📍ベルサール飯田橋駅前\n\n当日販売が決定！"

    def test_pin_line_under_date(self):
        hints = fs.x_venue_hints(tweet(self.TEXT))
        self.assertEqual([(h["date"], h["value"]) for h in hints], [("2026-11-22", "ベルサール飯田橋駅前")])
        m = missed([row()], hints=hints)
        self.assertEqual(len(m), 1)
        self.assertIn("公式 X 11/16 20:00 の投稿の「📍ベルサール飯田橋駅前」", m[0]["label"])

    def test_tbd_pin_is_not_reported(self):
        hints = fs.x_venue_hints(tweet("🗓️11月22日(日) 個別握手会\n📍東京都内某所"))
        self.assertEqual(missed([row()], hints=hints), [])

    def test_pin_of_other_event_on_same_day(self):
        hints = fs.x_venue_hints(tweet("🗓️11月22日(日) 対バンライブ\n📍Zepp Shinjuku"))
        rows = [row(), row(title="対バンライブ", venue="Zepp Shinjuku(TOKYO)", url="https://example.invalid/schedule/2")]
        self.assertEqual(missed(rows, hints=hints), [])

    def test_pin_without_date_is_ignored(self):
        self.assertEqual(fs.x_venue_hints(tweet("このあと出演！\n📍ベルサール飯田橋駅前")), [])


class NotifyMissedTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.missed_path = os.path.join(tmp.name, "missed.json")
        for patcher in (mock.patch.object(notify, "MISSED_PATH", self.missed_path),
                        mock.patch.object(notify, "WARNINGS_PATH", os.path.join(tmp.name, "warnings.json")),
                        mock.patch.object(notify, "WARNED_PATH", os.path.join(tmp.name, "warned.json")),
                        mock.patch.dict(os.environ, {"NTFY_TOPIC": "test-topic"})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.send = mock.patch.object(notify, "send").start()
        self.addCleanup(mock.patch.stopall)

    def run_notify(self, items):
        with open(self.missed_path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        with contextlib.redirect_stdout(io.StringIO()):
            notify.notify_warnings(False, "2026-11-16")

    def test_sent_once_with_both_sources(self):
        hints = fs.x_venue_hints(tweet(XHintTest.TEXT))
        items = missed([row()], [post("▼会場", "ベルサール飯田橋駅前")], hints)
        self.run_notify(items)
        self.assertEqual(self.send.call_count, 1)
        message, title, click = self.send.call_args.args[1:4]
        self.assertEqual(title, notify.MISSED_TITLE)
        self.assertEqual(message.splitlines()[0], "11/22 個別特典イベント（会場: 東京都内某所 のまま）")  # 予定名は通知の短い表記
        self.assertEqual(len(message.splitlines()), 4)  # 予定・記事・X・確認のお願い
        self.assertEqual(click, notify.SITE_URL)  # 情報元が 2 つあるので Web 版を開く
        self.run_notify(items)
        self.assertEqual(self.send.call_count, 1)  # 同じものは送らない


if __name__ == "__main__":
    unittest.main()
