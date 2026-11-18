"""公式 X の申込・締切（x_deadline_rows・x_deadline_reject・merge_x_deadlines）のテスト。

AI は呼ばない。投稿は実際の告知の書き方に合わせて短く作り直したもの（告知文は転載しない）。
実行: python3 -m unittest discover -s tests
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fetch_schedule as fs  # noqa: E402

TODAY = date(2026, 11, 19)
POST = {"posted": "2026-11-08T21:20+09:00",
        "text": "🎤出演情報🎤\n『 ッスッゴイライブ-8th Anniversary SP- 』\n🗓️12月10日(木)\n🎫チケット受付:11/9(月)21:00~12/9(水)23:59"}


def item(**values):
    base = {"kind": "受付開始", "date": "2026-11-09", "time": "21:00", "event": "ッスッゴイライブ-8th Anniversary SP-",
            "held": "2026-12-10", "detail": "チケット受付", "note": "11/9(月)21:00~12/9(水)23:59"}
    base.update(values)
    return base


class RejectTest(unittest.TestCase):
    def reject(self, **values):
        return fs.x_deadline_reject(item(**values), POST["text"], datetime.fromisoformat(POST["posted"]))

    def test_dates_and_times_must_be_in_text(self):
        self.assertEqual(self.reject(), "")
        self.assertEqual(self.reject(kind="受付締切", date="2026-12-09", time="23:59"), "")
        self.assertEqual(self.reject(date="2026-11-10"), "日付が本文にない")
        self.assertEqual(self.reject(time="20:00"), "時刻が本文にない")  # 「各部開始10分後まで」を 23:59 などと補ったもの
        self.assertEqual(self.reject(event=" "), "イベント名がない")


class RowsTest(unittest.TestCase):
    def run_rows(self, texts, ai_items, saved=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "x_deadlines.json")
        if saved is not None:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(saved, f)
        read = mock.Mock(return_value=({"items": ai_items}, (10, 1)))
        with mock.patch.object(fs, "x_llm_read", read), contextlib.redirect_stdout(io.StringIO()):
            rows = fs.x_deadline_rows(TODAY, "openai", "key", path, texts)
        with open(path, encoding="utf-8") as f:
            return rows, read, json.load(f)

    def test_period_becomes_two_rows_with_held_date(self):
        rows, read, saved = self.run_rows({"1": POST}, [item(), item(kind="受付締切", date="2026-12-09", time="23:59"),
                                                         item(date="2026-11-10")])  # 本文にない日付は除く
        self.assertEqual([(r["date"], r["time"], r["kind"], r["event"]) for r in rows],
                         [("2026-11-09", "21:00", "受付開始", "12/10 ッスッゴイライブ-8th Anniversary SP-"),
                          ("2026-12-09", "23:59", "受付締切", "12/10 ッスッゴイライブ-8th Anniversary SP-")])
        self.assertEqual(rows[0]["url"], "https://x.com/takanenofficial/status/1")
        self.assertNotIn("text", json.dumps(saved, ensure_ascii=False).replace('"items"', ""))  # 本文は保存しない
        rows2, read2, _ = self.run_rows({"1": POST}, [], saved)  # 本文が変わらなければ読み直さない
        self.assertEqual((len(rows2), read2.call_count), (2, 0))

    def test_posts_without_sale_words_or_dates_are_not_read(self):
        texts = {"1": {"posted": "2026-11-08T21:20+09:00", "text": "📚本日発売📚 雑誌に掲載"},
                 "2": {"posted": "2026-11-08T21:20+09:00", "text": "チケット販売中!"}}
        _, read, _ = self.run_rows(texts, [])
        self.assertEqual(read.call_count, 0)

    def test_saved_results_are_used_without_texts(self):
        saved = {"1": {"hash": "h", "posted": POST["posted"], "items": [item()]}}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "x_deadlines.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(saved, f)
        with mock.patch.object(fs, "load_x_texts", return_value=None), mock.patch.object(fs, "x_llm_read") as read:
            rows = fs.x_deadline_rows(TODAY, "openai", "key", path)
        self.assertEqual((len(rows), read.call_count), (1, 0))


class MergeTest(unittest.TestCase):
    ARTICLE = {"date": "2026-11-09", "weekday": "月", "time": "21:00", "kind": "受付開始",
               "event": "11/20 ニューアルバム『A World in Our Colors』発売記念オンラインサイン会", "detail": "抽選予約販売期間",
               "note": "", "url": "https://example.invalid/news/1"}

    def x(self, event, **values):
        return {**self.ARTICLE, "event": event, "detail": "抽選", "url": "https://x.com/takanenofficial/status/1", **values}

    def test_same_deadline_in_article_is_not_added(self):
        merged = fs.merge_x_deadlines([self.ARTICLE], [self.x("11/20 オンラインサイン会")])  # 開催日が同じ
        self.assertEqual(merged, [self.ARTICLE])

    def test_x_only_deadline_is_added(self):
        x = self.x("12/10 ッスッゴイライブ-8th Anniversary SP-")
        self.assertEqual(fs.merge_x_deadlines([self.ARTICLE], [x]), [self.ARTICLE, x])

    def test_time_text_uses_only_time_words(self):
        row = {"kind": "受付締切", "time": "", "note": "~11/30(月)購入の方限定直筆サイン付きチェキ (抽選)"}
        self.assertEqual(fs.deadline_time_text(row), "")
        self.assertEqual(fs.deadline_time_text({**row, "note": "11月6日〜各部開始10分後まで"}), "各部開始10分後まで")


if __name__ == "__main__":
    unittest.main()
