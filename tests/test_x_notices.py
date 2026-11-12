"""公式 X の中止・欠席の告知（x_notice・apply_x_notices）と、その通知・カレンダーでの扱いのテスト。

投稿は実際の告知の書き方に合わせて短く作り直したもの（告知文は転載しない）。
実行: python3 -m unittest discover -s tests
"""

import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fetch_schedule as fs  # noqa: E402
import notify  # noqa: E402
from fetch_schedule import GROUP  # noqa: E402


def tweet(text, created_at="2026-06-25T11:05:00.000Z", tweet_id="100"):
    """X API の投稿。created_at は UTC（日本時間の 9 時間前）。"""
    return {"id": tweet_id, "created_at": created_at, "text": text}


def row(**values):
    base = {"date": "2026-06-27", "open": "", "start": "", "end": "", "time_note": "",
            "title": "UP-T presents HERO SONIC 2026", "category": "ライブ／イベント", "members": GROUP,
            "venue": "", "url": "https://example.invalid/schedule/1"}
    base.update(values)
    return base


def apply(rows, *tweets):
    notices = [fs.x_notice(t) for t in tweets]
    with contextlib.redirect_stdout(io.StringIO()):
        fs.apply_x_notices(rows, notices)
    return rows


class XNoticeTest(unittest.TestCase):
    def test_cancel_with_date(self):
        n = fs.x_notice(tweet("【イベント中止のお知らせ】\n\n6/27(土) #高嶺のなでしこ 出演予定の\n『 UP-T presents HERO SONIC 2026 』\nにつきまして、台風の接近に伴い開催中止となりました。"))
        self.assertEqual((n["status"], n["label"], n["date"], n["names"]),
                         ("cancel", "中止", "2026-06-27", ["UP-T presents HERO SONIC 2026"]))
        self.assertEqual(n["url"], "https://x.com/takanenofficial/status/100")

    def test_cancel_today_with_nested_name(self):
        n = fs.x_notice(tweet("【公演中止のお知らせ】\n\n本日 #高嶺のなでしこ 出演予定の\n『「イベレコ×高嶺のなでしこ」のDREAMランタン by 関電工 』\nにつきまして、荒天が予想されるため開催中止となりました。",
                              created_at="2026-04-04T06:01:00.000Z"))
        self.assertEqual((n["status"], n["date"], n["names"]),
                         ("cancel", "2026-04-04", ["「イベレコ×高嶺のなでしこ」のDREAMランタン by 関電工"]))

    def test_partial_cancel(self):
        n = fs.x_notice(tweet("【ミニライブ中止のお知らせ】\n\n本日実施予定の\n「 #高嶺のなでしこ 『A World in Our Colors』リリースイベント 」\nにつきまして、ミニライブは中止とし、特典会のみの実施とさせていただきます。",
                              created_at="2026-09-06T01:12:00.000Z"))
        self.assertEqual((n["status"], n["label"], n["date"]), ("partial", "ミニライブ中止", "2026-09-06"))

    def test_absence_with_two_events(self):
        n = fs.x_notice(tweet("城月菜央欠席のお知らせ\n\n本日出演予定の「iLiVE!vol.22」「HANiKAMi vol.4」におきまして、城月菜央が体調不良によりお休みをさせて頂くことになりました。",
                              created_at="2026-07-17T00:00:00.000Z"))
        self.assertEqual((n["status"], n["label"], n["date"], n["names"], n["members"]),
                         ("absent", "城月欠席", "2026-07-17", ["iLiVE!vol.22", "HANiKAMi vol.4"], ["城月菜央"]))

    def test_absence_with_substitute(self):
        n = fs.x_notice(tweet("城月菜央欠席のお知らせ\n\n7/24(金)出演予定の「NATSLIVE たかねこ初恋キッチン。 」におきまして、城月菜央がお休みをいただくこととなりました。\n城月菜央に変わり、葉月紗蘭が出演いたします。",
                              created_at="2026-07-21T06:03:00.000Z"))
        self.assertEqual((n["date"], n["members"], n["substitutes"]), ("2026-07-24", ["城月菜央"], ["葉月紗蘭"]))

    def test_absence_posted_early_morning_uses_japan_date(self):
        n = fs.x_notice(tweet("橋本桃呼欠席のお知らせ\n\n本日出演予定の「GIRLS GIRLS FESTIVAL 2026」におきまして、橋本桃呼がお休みをさせて頂くことになりました。",
                              created_at="2026-05-22T23:30:00.000Z"))
        self.assertEqual(n["date"], "2026-05-23")

    def test_absence_of_former_member(self):
        n = fs.x_notice(tweet("【星谷美来欠席のお知らせ】\n\n本日出演の「LEGEND FES」につきまして、星谷美来がお休みをさせて頂くこととなりました。",
                              created_at="2026-01-09T05:00:00.000Z"))
        self.assertEqual((n["label"], n["members"]), ("星谷美来欠席", []))

    def test_notice_without_date(self):
        n = fs.x_notice(tweet("星谷美来のイベント欠席に関するお知らせ\n\n当日のお知らせとなり申し訳ございません。"))
        self.assertEqual((n["status"], n["date"]), ("absent", ""))

    def test_other_posts_are_not_notices(self):
        for text in ("【開場/開演時間変更のお知らせ】\n\n開場/開演時間に変更がございます。",
                     "【お詫びとお知らせ】\n\n販売を延期させていただきます。",
                     "🎤出演情報🎤\n\n『 MARQUEE祭 Vol.186 』\n\n🗓️12月7日(月)",
                     ""):
            self.assertIsNone(fs.x_notice(tweet(text)), text)


class ApplyXNoticesTest(unittest.TestCase):
    CANCEL = "【イベント中止のお知らせ】\n\n6/27(土) 出演予定の\n『 UP-T presents HERO SONIC 2026 』\nにつきまして、開催中止となりました。"

    def test_cancel_marks_matching_event_only(self):
        rows = apply([row(), row(title="SMILE PLUS 2026", url="https://example.invalid/schedule/2"),
                      row(title="✿橋本桃呼誕生日✿", category="誕生日")], tweet(self.CANCEL))
        self.assertEqual([r["title"] for r in rows],
                         ["【中止】UP-T presents HERO SONIC 2026", "SMILE PLUS 2026", "✿橋本桃呼誕生日✿"])

    def test_single_event_of_the_day_without_name_match(self):
        rows = apply([row(title="リリースイベント at 有明ガーデン", date="2026-09-06")],
                     tweet("【ミニライブ中止のお知らせ】\n\n本日実施予定のイベントにつきまして、ミニライブは中止とさせていただきます。",
                           created_at="2026-09-06T01:12:00.000Z"))
        self.assertEqual(rows[0]["title"], "【ミニライブ中止】リリースイベント at 有明ガーデン")

    def test_no_match_when_ambiguous(self):
        rows = apply([row(title="A公演"), row(title="B公演")], tweet("【イベント中止のお知らせ】\n\n6/27(土) 出演予定の『 C公演 』は中止となりました。"))
        self.assertEqual([r["title"] for r in rows], ["A公演", "B公演"])

    def test_absence_updates_individual_members(self):
        text = ("城月菜央欠席のお知らせ\n\n7/24(金)出演予定の「NATSLIVE たかねこ初恋キッチン。」におきまして、城月菜央がお休みをいただきます。\n"
                "城月菜央に変わり、葉月紗蘭が出演いたします。")
        rows = apply([row(date="2026-07-24", title="たかねこ初恋キッチン。", members="城月菜央、籾山ひめり"),
                      row(date="2026-07-24", title="NATSLIVE たかねこ初恋キッチン。 2部", members=GROUP,
                          url="https://example.invalid/schedule/2")],
                     tweet(text, created_at="2026-07-21T06:03:00.000Z"))
        self.assertEqual([(r["title"], r["members"]) for r in rows], [
            ("【城月欠席】たかねこ初恋キッチン。", "葉月紗蘭、籾山ひめり"),
            ("【城月欠席】NATSLIVE たかねこ初恋キッチン。 2部", GROUP),
        ])

    def test_same_notice_twice_is_applied_once(self):
        rows = apply([row()], tweet(self.CANCEL, tweet_id="1"), tweet(self.CANCEL, tweet_id="2"))
        self.assertEqual(rows[0]["title"], "【中止】UP-T presents HERO SONIC 2026")


class OutputTest(unittest.TestCase):
    def test_ics_summary_and_status(self):
        r = row(title="【中止】UP-T presents HERO SONIC 2026", members="城月菜央", uid="u1")
        self.assertEqual(fs.ics_summary(r), "【中止】【城月】UP-T presents HERO SONIC 2026")
        self.assertIn("STATUS:CANCELLED", fs.ics_event(r))
        self.assertIn("STATUS:CONFIRMED", fs.ics_event(row(title="【城月欠席】HERO SONIC", uid="u2")))

    def test_notify_classify_and_message(self):
        base = dict(row(), weekday="土", venue_url="", memo="", uid="u1", done="")
        old, new = base, dict(base, title="【中止】UP-T presents HERO SONIC 2026")
        self.assertEqual(notify.classify(old, new), [("important", "中止", "", "")])
        self.assertEqual(notify.classify(new, new), [])
        _, updated, _ = notify.diff_schedule([old], [new], "2026-06-25")
        message, important = notify.build_message([], updated, [], [])
        self.assertTrue(important)
        self.assertEqual(message, "⚠️ 中止 6/27 UP-T presents HERO SONIC 20…")  # 予定名は 28 文字まで


if __name__ == "__main__":
    unittest.main()
