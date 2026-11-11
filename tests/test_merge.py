"""fetch_schedule.py の予定の統合（merge_post_rows）・ID の引き継ぎ（assign_uids）・安全策（check_drop）のテスト。

実行: python3 -m unittest discover -s tests
"""

import contextlib
import io
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fetch_schedule as fs  # noqa: E402
from fetch_schedule import GROUP, X_CATEGORY  # noqa: E402


def row(**values):
    """取得した予定の 1 行（公式カレンダー）。"""
    base = {"date": "2026-11-20", "open": "", "start": "", "end": "", "time_note": "", "title": "ミニライブ&特典会",
            "category": "ライブ／イベント", "members": GROUP, "venue": "ららぽーと立川立飛",
            "url": "https://example.invalid/schedule/1"}
    base.update(values)
    return base


def x_row(**values):
    """公式 X から読み取った予定の 1 行。"""
    base = row(category=X_CATEGORY, media="", url="https://x.com/takanenofficial/status/1")
    base.update(values)
    return base


def quiet(func, *args):
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args)


class MergePostRowsTest(unittest.TestCase):
    def merge(self, rows, post_rows):
        return quiet(fs.merge_post_rows, rows, post_rows)

    def test_same_event_from_post_is_merged(self):
        rows = self.merge([row()], [row(title="ミニライブ&特典会 開催決定", url="https://example.invalid/news/1")])
        self.assertEqual([r["url"] for r in rows], ["https://example.invalid/schedule/1"])

    def test_other_event_is_added(self):
        rows = self.merge([row()], [row(title="ラジオ番組ゲスト出演", venue="", url="https://example.invalid/news/1")])
        self.assertEqual(len(rows), 2)

    def test_other_day_is_added(self):
        rows = self.merge([row()], [row(date="2026-11-21", url="https://example.invalid/news/1")])
        self.assertEqual(len(rows), 2)

    def test_release_day_is_kept_apart(self):
        """発売日の行とその日のリリースイベントは別の予定にする。"""
        release = row(title="ニューアルバム『A World in Our Colors』2026年11月20日(金)リリース", venue="",
                      url="https://example.invalid/news/1")
        rows = self.merge([row(title="ミニライブ&特典会 A World in Our Colors")], [release])
        self.assertEqual(len(rows), 2)

    def test_post_fills_members_and_venue(self):
        rows = self.merge([row(venue="")], [row(members="葉月紗蘭、東山恵里沙", url="https://example.invalid/news/1")])
        self.assertEqual((rows[0]["members"], rows[0]["venue"]), ("葉月紗蘭、東山恵里沙", "ららぽーと立川立飛"))

    def test_more_detailed_venue_wins(self):
        rows = self.merge([row()], [row(venue="ららぽーと立川立飛2F イベント広場", url="https://example.invalid/news/1")])
        self.assertEqual(rows[0]["venue"], "ららぽーと立川立飛2F イベント広場")

    def test_x_event_keeps_post_url_on_official_event(self):
        """公式の予定に置き換わっても X の予定の ID を引き継げるよう、投稿の URL を残す。"""
        rows = self.merge([row()], [x_row(title="リリースイベント", venue="ららぽーと立川立飛 イベント広場")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["x_urls"], ["https://x.com/takanenofficial/status/1"])

    def test_x_event_with_several_candidates_is_skipped(self):
        rows = [row(title="ミニライブ 1部"), row(title="ミニライブ 2部", url="https://example.invalid/schedule/2")]
        merged = self.merge(rows, [x_row(title="ミニライブ")])
        self.assertEqual(len(merged), 2)
        self.assertNotIn("x_urls", merged[0])

    def test_second_x_post_fills_blanks(self):
        first = x_row(venue="")
        second = x_row(start="13:00", venue="ららぽーと立川立飛", url="https://x.com/takanenofficial/status/2")
        rows = self.merge([], [first, second])
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["start"], rows[0]["venue"]), ("13:00", "ららぽーと立川立飛"))


class AssignUidsTest(unittest.TestCase):
    def assign(self, rows, prev=()):
        quiet(fs.assign_uids, rows, prev)
        return [r["uid"] for r in rows]

    def test_uid_is_stable(self):
        self.assertEqual(self.assign([row()]), self.assign([row()]))
        self.assertEqual(len(self.assign([row()])[0]), 16)

    def test_same_day_same_source_gets_different_uids(self):
        a, b = self.assign([row(title="1部"), row(title="2部")])
        self.assertNotEqual(a, b)

    def test_past_uid_is_kept(self):
        self.assertEqual(self.assign([row(uid="keepthisuid00000")]), ["keepthisuid00000"])

    def test_uid_is_inherited_when_source_changes(self):
        """Eventernote → 公式カレンダーのように情報元が変わっても、同じ日の同じ予定なら前回の ID を使う。"""
        prev = [dict(row(url="https://www.eventernote.com/events/1"), uid="prevuid000000000")]
        self.assertEqual(self.assign([row()], prev), ["prevuid000000000"])

    def test_uid_is_inherited_from_x_post(self):
        prev = [dict(x_row(title="リリースイベント", venue="東京都内某所"), uid="xuid000000000000")]
        merged = row(title="個別握手会", venue="大崎ブライトコアホール", x_urls=["https://x.com/takanenofficial/status/1"])
        self.assertEqual(self.assign([merged], prev), ["xuid000000000000"])

    def test_uid_is_not_inherited_when_ambiguous(self):
        prev = [dict(row(url="https://www.eventernote.com/events/1"), uid="prevuid000000000")]
        new = [row(title="ミニライブ&特典会 1部"), row(title="ミニライブ&特典会 2部")]
        self.assertNotIn("prevuid000000000", self.assign(new, prev))

    def test_uid_is_not_inherited_from_other_day(self):
        prev = [dict(row(date="2026-11-21", url="https://www.eventernote.com/events/1"), uid="prevuid000000000")]
        self.assertNotEqual(self.assign([row()], prev), ["prevuid000000000"])


class CheckDropTest(unittest.TestCase):
    START, END = date(2026, 11, 11), date(2027, 11, 12)

    def prev(self, n, category="ライブ／イベント"):
        return [row(date=f"2026-12-{i + 1:02d}", url=f"https://example.invalid/schedule/{i}", category=category)
                for i in range(n)]

    def test_no_events_from_calendar(self):
        self.assertIsNotNone(fs.check_drop([], [row()], self.START, self.END, 0))

    def test_stops_when_many_events_disappear(self):
        prev = self.prev(10)
        self.assertIsNotNone(fs.check_drop(prev, prev[:5], self.START, self.END, 5))

    def test_passes_below_ratio_or_count(self):
        prev = self.prev(20)
        self.assertIsNone(fs.check_drop(prev, prev[:15], self.START, self.END, 15))  # 25% 消えた
        small = self.prev(4)
        self.assertIsNone(fs.check_drop(small, [], self.START, self.END, 1))  # 4 件しか消えていない

    def test_eventernote_and_out_of_range_are_ignored(self):
        prev = self.prev(10, category="Eventernote（非公式）")
        prev += [row(date="2026-10-01", url=f"https://example.invalid/past/{i}") for i in range(10)]
        self.assertIsNone(fs.check_drop(prev, [], self.START, self.END, 1))

    def test_event_found_by_x_url(self):
        prev = [x_row(date=f"2026-12-{i + 1:02d}", url=f"https://x.com/takanenofficial/status/{i}") for i in range(10)]
        rows = [row(date=p["date"], x_urls=[p["url"]]) for p in prev]
        self.assertIsNone(fs.check_drop(prev, rows, self.START, self.END, 10))


if __name__ == "__main__":
    unittest.main()
