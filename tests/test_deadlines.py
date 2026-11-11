"""fetch_schedule.py の申込・締切の読み取り（extract_deadlines・collect_deadlines・表示用の列）のテスト。

記事はお知らせ記事（WordPress REST API）の形を、実際の記載の書き方に合わせて小さく作ったもの。
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


def post(*lines, title="ニューアルバム発売記念オンラインサイン会開催決定！", published="2026-11-01T12:00:00",
         link="https://example.invalid/news/1"):
    """本文の行を <p> で並べた記事。"""
    body = "".join(f"<p>{line}</p>" for line in lines)
    return {"date": published, "title": {"rendered": title}, "content": {"rendered": body}, "link": link}


def extract(*lines, **kwargs):
    return [(r["date"], r["time"], r["kind"], r["detail"]) for r in fs.extract_deadlines(post(*lines, **kwargs))]


class ExtractKindsTest(unittest.TestCase):
    """DEADLINE_LABELS の見出しごとの種別。"""

    def test_period_gives_start_and_end(self):
        self.assertEqual(extract("＜受付期間＞11/13(金)17:00〜11/23(月)23:59"), [
            ("2026-11-13", "17:00", "受付開始", "受付期間"),
            ("2026-11-23", "23:59", "受付締切", "受付期間"),
        ])

    def test_period_with_only_end(self):
        """「〜まで」だけの期間は終わりの日時にする。"""
        self.assertEqual(extract("応募期間：11月20日(金) 23:59まで"),
                         [("2026-11-20", "23:59", "受付締切", "応募期間")])

    def test_deadline_label(self):
        self.assertEqual(extract("受付締め切り：11月15日(日)23:59"),
                         [("2026-11-15", "23:59", "受付締切", "受付締め切り")])

    def test_lottery_and_payment_period(self):
        self.assertEqual(extract("＜当落・入金期間＞11/25(水)18:00〜11/27(金)21:00"), [
            ("2026-11-25", "18:00", "当落発表", "当落・入金期間"),
            ("2026-11-27", "21:00", "入金締切", "当落・入金期間"),
        ])

    def test_lottery_result(self):
        self.assertEqual(extract("抽選結果発表：11月25日(水)18時以降"),
                         [("2026-11-25", "18:00", "当落発表", "抽選結果発表")])

    def test_payment_deadline(self):
        self.assertEqual(extract("＜支払期日＞11月27日(金)"), [("2026-11-27", "", "入金締切", "支払期日")])

    def test_general_sale(self):
        self.assertEqual(extract("[チケット一般発売] 2026年12月5日(土)10:00〜"),
                         [("2026-12-05", "10:00", "一般発売", "チケット一般発売")])

    def test_online_sale_start(self):
        self.assertEqual(extract("販売開始：2026年11月14日(土)21:00~ 高嶺のなでしこ公式LINE上より販売。"),
                         [("2026-11-14", "21:00", "受付開始", "販売開始")])

    def test_sale_start_at_venue_is_skipped(self):
        self.assertEqual(extract("グッズ販売開始：11/14(土)10:00〜"), [])

    def test_date_on_next_line(self):
        self.assertEqual(extract("＜受付期間＞", "11/13(金)17:00〜11/23(月)23:59"), [
            ("2026-11-13", "17:00", "受付開始", "受付期間"),
            ("2026-11-23", "23:59", "受付締切", "受付期間"),
        ])


class ExtractRulesTest(unittest.TestCase):
    def test_heading_becomes_detail(self):
        rows = extract("【FC先行受付】", "＜受付期間＞11/13(金)17:00〜11/23(月)23:59")
        self.assertEqual({detail for _, _, _, detail in rows}, {"FC先行受付"})

    def test_unrelated_heading_is_not_used(self):
        rows = extract("【その他ご注意】", "＜受付期間＞11/13(金)17:00〜11/23(月)23:59")
        self.assertEqual({detail for _, _, _, detail in rows}, {"受付期間"})

    def test_shop_hours_and_same_day_sale_are_skipped(self):
        self.assertEqual(extract("店舗受付時間：11/14(土)10:00〜20:00", "当日販売期間：11/20(金)10:00〜"), [])

    def test_payment_method_deadline_is_skipped(self):
        self.assertEqual(extract("バーチャル銀行振込", "受付締切：11月22日(日)23:59"), [])

    def test_date_before_published_is_skipped(self):
        skipped = []
        rows = fs.extract_deadlines(post("◆1回目受付 10/01(木)12:00〜10/10(土)23:59",
                                         "◆2回目受付 11/13(金)17:00〜11/23(月)23:59"), skipped)
        self.assertEqual([(r["date"], r["kind"]) for r in rows],
                         [("2026-11-13", "受付開始"), ("2026-11-23", "受付締切")])
        self.assertEqual(len(skipped), 2)

    def test_year_is_inferred_across_new_year(self):
        rows = extract("＜受付期間＞12/20(日)12:00〜1/5(火)23:59", published="2026-12-01T12:00:00")
        self.assertEqual([d for d, _, _, _ in rows], ["2026-12-20", "2027-01-05"])

    def test_event_name_and_held_date(self):
        rows = fs.extract_deadlines(post("＜受付期間＞11/13(金)17:00〜11/23(月)23:59",
                                         title="【11月24日(火)】高嶺のなでしこ ミニライブat ららぽーと立川立飛 開催決定！"))
        self.assertEqual({r["event"] for r in rows}, {"11/24 ミニライブ at ららぽーと立川立飛"})


class CollectDeadlinesTest(unittest.TestCase):
    def collect(self, posts, end=date(2027, 1, 1)):
        with contextlib.redirect_stdout(io.StringIO()):
            return fs.collect_deadlines(posts, end, date(2026, 11, 11))

    def test_same_deadline_is_merged_and_newer_post_wins(self):
        old = post("＜受付期間＞11/13(金)17:00〜11/23(月)23:59", published="2026-11-01T12:00:00",
                   link="https://example.invalid/news/old")
        new = post("＜受付期間＞11/13(金)17:00〜11/23(月)23:59", published="2026-11-05T12:00:00",
                   link="https://example.invalid/news/new")
        rows = self.collect([old, new])
        self.assertEqual([(r["kind"], r["url"]) for r in rows],
                         [("受付開始", "https://example.invalid/news/new"),
                          ("受付締切", "https://example.invalid/news/new")])

    def test_sorted_and_cut_at_end(self):
        rows = self.collect([post("＜支払期日＞11月27日(金)", "受付締め切り：11月15日(日)23:59",
                                  "[チケット一般発売] 2027年1月5日(火)10:00〜")])
        self.assertEqual([(r["date"], r["kind"]) for r in rows],
                         [("2026-11-15", "受付締切"), ("2026-11-27", "入金締切")])


class LabelTest(unittest.TestCase):
    def label(self, kind, detail, time="23:59", note=""):
        r = {"kind": kind, "detail": detail, "time": time, "note": note}
        return fs.deadline_label(r), fs.deadline_time_text(r)

    def test_labels(self):
        self.assertEqual(self.label("受付締切", "抽選受付"), ("抽選の締切", "23:59まで"))
        self.assertEqual(self.label("受付開始", "先着販売", "12:00"), ("先着販売の開始", "12:00〜"))
        self.assertEqual(self.label("受付締切", "オフィシャル先行受付"), ("オフィシャル先行受付の締切", "23:59まで"))
        self.assertEqual(self.label("入金締切", "当落・入金期間", "21:00"), ("入金締切", "21:00まで"))
        self.assertEqual(self.label("受付開始", "販売期間", "10:00"), ("販売開始", "10:00〜"))

    def test_deadline_time_from_note(self):
        _, text = self.label("受付締切", "受付期間", time="", note="2026年11月10日(火) 20:00~2026年11月20日(金) 各部開始10分後まで")
        self.assertEqual(text, "各部開始10分後まで")

    def test_deadline_event(self):
        self.assertEqual(fs.deadline_event("11/20 ニューアルバム『A World in Our Colors』発売記念オンラインサイン会"),
                         "11/20 発売記念オンラインサイン会")
        self.assertEqual(fs.deadline_event("ミニライブ at 東京都内某所"), "ミニライブ（東京都内某所）")


if __name__ == "__main__":
    unittest.main()
