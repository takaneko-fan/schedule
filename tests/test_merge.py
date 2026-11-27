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
sys.path.insert(0, os.path.dirname(__file__))

import no_network  # noqa: E402,F401 — テストから外部に通信しない

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
        """お知らせ記事 → 公式カレンダーのように情報元が変わっても、同じ日の同じ予定なら前回の ID を使う。"""
        prev = [dict(row(url="https://example.invalid/news/1"), uid="prevuid000000000")]
        self.assertEqual(self.assign([row()], prev), ["prevuid000000000"])

    def test_uid_is_inherited_from_x_post(self):
        prev = [dict(x_row(title="リリースイベント", venue="東京都内某所"), uid="xuid000000000000")]
        merged = row(title="個別握手会", venue="大崎ブライトコアホール", x_urls=["https://x.com/takanenofficial/status/1"])
        self.assertEqual(self.assign([merged], prev), ["xuid000000000000"])

    def test_uid_is_not_inherited_when_ambiguous(self):
        prev = [dict(row(url="https://example.invalid/news/1"), uid="prevuid000000000")]
        new = [row(title="ミニライブ&特典会 1部"), row(title="ミニライブ&特典会 2部")]
        self.assertNotIn("prevuid000000000", self.assign(new, prev))

    def test_uid_is_not_inherited_from_other_day(self):
        prev = [dict(row(date="2026-11-21", url="https://example.invalid/news/1"), uid="prevuid000000000")]
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

    def test_out_of_range_is_ignored(self):
        prev = [row(date="2026-10-01", url=f"https://example.invalid/past/{i}") for i in range(10)]
        self.assertIsNone(fs.check_drop(prev, [], self.START, self.END, 1))

    def test_event_found_by_x_url(self):
        prev = [x_row(date=f"2026-12-{i + 1:02d}", url=f"https://x.com/takanenofficial/status/{i}") for i in range(10)]
        rows = [row(date=p["date"], x_urls=[p["url"]]) for p in prev]
        self.assertIsNone(fs.check_drop(prev, rows, self.START, self.END, 10))


if __name__ == "__main__":
    unittest.main()


class XAddsInfoTest(unittest.TestCase):
    """公式 X の予定を x_events.json に保存するか（統合して出力が変わるときだけ保存する）。"""

    def cand(self, **values):
        from datetime import date as d
        base = {"date": d(2026, 11, 20), "title": "ミニライブ&特典会", "venue": "ららぽーと立川立飛", "times": {},
                "time_note": "", "members": GROUP, "names": [], "url": "https://x.com/takanenofficial/status/9",
                "post_id": "9", "posted": "2026-11-15T20:00+09:00", "media": "", "ai": False, "station": ""}
        base.update(values)
        return base

    def test_listed_with_venue_is_not_saved(self):
        self.assertFalse(fs.x_adds_info(self.cand(), [row()]))

    def test_new_event_is_saved(self):
        self.assertTrue(fs.x_adds_info(self.cand(), []))
        self.assertTrue(fs.x_adds_info(self.cand(), [row(date="2026-11-21")]))

    def test_fills_blank_venue_of_official_event(self):
        self.assertTrue(fs.x_adds_info(self.cand(), [row(venue="")]))

    def test_release_day_row_is_not_the_same_event(self):
        """発売日の行は別の予定なので、その会場が空でも「補える」とはみなさない。"""
        rows = [row(), row(title="ニューアルバム『A World in Our Colors』2026年11月20日(金)リリース", venue="",
                           category="お知らせ記事", url="https://example.invalid/news/1")]
        self.assertFalse(fs.x_adds_info(self.cand(names=["A World in Our Colors"]), rows))

    def test_reannouncement_of_saved_x_event(self):
        saved = {"date": "2026-11-20", "title": "ラジオ番組ゲスト", "venue": "", "open": "", "start": "19:00",
                 "end": "", "time_note": "", "members": GROUP, "names": [], "url": "https://x.com/takanenofficial/status/1",
                 "post_id": "1", "posted": "2026-11-10T20:00+09:00", "media": "ラジオ", "ai": False, "station": ""}
        same = self.cand(title="ラジオ番組ゲスト", venue="", times={"START": "19:00"}, media="ラジオ")
        self.assertFalse(fs.x_adds_info(same, [], [saved]))
        with_end = self.cand(title="ラジオ番組ゲスト", venue="", times={"START": "19:00", "END": "20:00"}, media="ラジオ")
        self.assertTrue(fs.x_adds_info(with_end, [], [saved]))  # 終了時刻を補える


class TbdVenueTest(unittest.TestCase):
    """「東京都内某所」のような仮の会場を、記事や公式 X の実際の会場で置き換える。"""

    def merge(self, rows, post_rows):
        return quiet(fs.merge_post_rows, rows, post_rows)

    def test_post_replaces_tbd_venue_even_with_times(self):
        rows = self.merge([row(venue="東京都内某所（詳細は後日発表）", start="11:30")],
                          [row(venue="ベルサール飯田橋駅前", url="https://example.invalid/news/1")])
        self.assertEqual(rows[0]["venue"], "ベルサール飯田橋駅前")

    def test_x_replaces_tbd_venue(self):
        rows = self.merge([row(title="リリース記念個別特典イベント", venue="東京都内某所")],
                          [x_row(title="個別握手会", venue="ベルサール飯田橋駅前", names=["リリース記念個別特典イベント"])])
        self.assertEqual(rows[0]["venue"], "ベルサール飯田橋駅前")

    def test_tbd_does_not_replace_real_venue(self):
        rows = self.merge([row()], [row(venue="東京都内某所", url="https://example.invalid/news/1")])
        self.assertEqual(rows[0]["venue"], "ららぽーと立川立飛")

    def test_saved_x_event_with_tbd_venue_is_replaced(self):
        rows = self.merge([], [x_row(venue="東京都内某所"),
                               x_row(venue="ベルサール飯田橋駅前", url="https://x.com/takanenofficial/status/2")])
        self.assertEqual([r["venue"] for r in rows], ["ベルサール飯田橋駅前"])

    def test_x_post_that_decides_venue_is_saved(self):
        from datetime import date as d
        c = {"date": d(2026, 11, 20), "title": "個別握手会", "venue": "ベルサール飯田橋駅前", "times": {}, "time_note": "",
             "members": GROUP, "names": [], "url": "https://x.com/takanenofficial/status/9", "post_id": "9",
             "posted": "2026-11-16T20:00+09:00", "media": "", "ai": False, "station": ""}
        self.assertTrue(fs.x_adds_info(c, [row(title="個別握手会", venue="東京都内某所")]))


class VenueFromPostTest(unittest.TestCase):
    def post(self, *lines, title="【11月22日(日)】リリース記念イベント 当日販売&会場限定抽選会開催のお知らせ"):
        return {"title": {"rendered": title}, "content": {"rendered": "".join(f"<p>{ln}</p>" for ln in lines)}}

    def test_heading_line_then_venue(self):
        self.assertEqual(fs.venue_from_post(self.post("■会場", "ベルサール飯田橋駅前", "〒102-0072 東京都千代田区…")),
                         "ベルサール飯田橋駅前")

    def test_other_headings_are_not_venue(self):
        self.assertEqual(fs.venue_from_post(self.post("■会場限定抽選会", "対象商品をご購入いただいた方…",
                                                      "会場内は撮影禁止です。")), "")

    def test_existing_forms(self):
        self.assertEqual(fs.venue_from_post(self.post("【会場】ヒューリックホール東京")), "ヒューリックホール東京")
        self.assertEqual(fs.venue_from_post(self.post("会場：Zepp Shinjuku")), "Zepp Shinjuku")


class SameEventMatchTest(unittest.TestCase):
    """別の催しを、どの予定にも付く語（作品名・「リリース記念」など）だけで同じ予定とみなさない。"""

    RELEASE = "高嶺のなでしこ ニューアルバム『A World in Our Colors』 リリース記念イベント at ららぽーと立川立飛"
    CAMPAIGN = ("高嶺のなでしこ ニューアルバム『A World in Our Colors』リリース記念 タワーレコードキャンペーン："
                "：涼海すう プリントサイン入りスペシャルレシート")

    def merge(self, rows, post_rows):
        return quiet(fs.merge_post_rows, rows, post_rows)

    def test_event_phrase(self):
        self.assertEqual(" ".join(fs.event_phrase(self.RELEASE).split()), "イベント at ららぽーと立川立飛")
        self.assertEqual(" ".join(fs.event_phrase("MARQUEE祭 Vol.186").split()), "MARQUEE祭 Vol.186")

    def test_campaign_is_not_merged_and_members_are_kept(self):
        rows = self.merge([row(date="2026-11-18", title=self.RELEASE)],
                          [row(date="2026-11-18", title=self.CAMPAIGN, members="涼海すう", venue="",
                               category="お知らせ記事", url="https://example.invalid/news/tower")])
        self.assertEqual([(r["title"][-12:], r["members"]) for r in rows],
                         [(self.RELEASE[-12:], GROUP), (self.CAMPAIGN[-12:], "涼海すう")])

    def test_reworded_event_still_merges_when_unique(self):
        """「個別特典会＠東京都内某所」（月ごとのスケジュール）と「リリース記念個別イベント」は同じ予定。"""
        news = row(date="2026-10-17", title="アルバム『 A World in Our Colors 』2026年11月18日(水)リリース：個別特典会＠東京都内某所",
                   venue="", url="https://example.invalid/news/1")
        rows = self.merge([row(date="2026-10-17", title="ニューアルバム『 A World in Our Colors 』リリース記念個別イベント",
                               venue="大崎ブライトコアホール")], [news])
        self.assertEqual(len(rows), 1)

    def test_ambiguous_loose_match_is_kept_separate(self):
        news = row(title="ニューアルバム『A World in Our Colors』リリース：特典会", venue="", url="https://example.invalid/news/1")
        rows = self.merge([row(title="ニューアルバム『A World in Our Colors』リリース記念ミニライブ", venue="A"),
                           row(title="ニューアルバム『A World in Our Colors』リリース記念個別イベント", venue="B",
                               url="https://example.invalid/schedule/2")], [news])
        self.assertEqual(len(rows), 3)

    def test_members_are_taken_only_from_the_same_event(self):
        rows = self.merge([row(title=self.RELEASE)],
                          [row(title="【11月18日(水)】リリース記念イベント at ららぽーと立川立飛", members="涼海すう、松本ももな",
                               url="https://example.invalid/news/1")])
        self.assertEqual(rows[0]["members"], "涼海すう、松本ももな")  # 同じ会場の詳細記事の担当は使う


class SameSlotTest(unittest.TestCase):
    def merge(self, rows, post_rows):
        return quiet(fs.merge_post_rows, rows, post_rows)

    def radio(self, **values):
        return row(title="ヒダミノ", venue="", start="18:00", end="18:25", media="ラジオ", station="岐阜放送",
                   category="お知らせ記事", members="葉月紗蘭、東山恵里沙", url="https://example.invalid/news/1", **values)

    def test_misread_program_name_is_merged(self):
        """告知画像で番組名を読み違えても、同じ局・同じ時刻なら記事の予定にまとめる。"""
        image = x_row(title="ヒダノノ", venue="", start="18:00", end="18:25", media="ラジオ", station="岐阜放送")
        rows = self.merge([self.radio()], [image])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["members"], "葉月紗蘭、東山恵里沙")
        self.assertEqual(rows[0]["x_urls"], [image["url"]])

    def test_other_time_or_station_is_added(self):
        for values in ({"start": "19:00"}, {"title": "カリメン", "station": "RKBラジオ"}, {"title": "ヒダシマ", "station": ""},
                       {"start": ""}):
            with self.subTest(values=values):
                other = x_row(title="ヒダノノ", venue="", start="18:00", media="ラジオ", station="岐阜放送")
                other.update(values)
                self.assertEqual(len(self.merge([self.radio()], [other])), 2)

    def test_misread_name_without_station_is_merged(self):
        """局がなくても、同じ時刻で予定名が近ければまとめる。局が同じなら名前が離れていてもまとめる。"""
        for values in ({"title": "ヒダノノ", "station": ""}, {"title": "ヒダシマ"}):
            with self.subTest(values=values):
                image = x_row(venue="", start="18:00", media="ラジオ", station="岐阜放送")
                image.update(values)
                self.assertEqual(len(self.merge([self.radio()], [image])), 1)

    def test_same_time_other_events_are_added(self):
        """同じ時刻でも、別の局の番組、収録の放送とイベントはまとめない（2026-11-18 の実例）。"""
        event = row(title="リリース記念イベント", start="17:00", venue="ららぽーと立川立飛2F イベント広場")
        radio = row(title="Oita Sunset Groove", venue="", start="17:00", media="ラジオ", station="FM大分",
                    members="城月菜央、日向端ひな", url="https://example.invalid/news/1")
        self.assertEqual(len(self.merge([event], [radio])), 2)
        a = self.radio(); a.update(title="ドラ魂キング", start="16:00", station="CBCラジオ")
        b = self.radio(); b.update(title="江本一真のゴッジ", start="16:00", station="広島FM", url="https://example.invalid/news/2")
        self.assertEqual(len(self.merge([a], [b])), 2)
        a.update(title="らじお女子の「ラジオに恋して」", start="21:00")
        c = x_row(title="カリメン", venue="", start="21:00", media="ラジオ", station="RKBラジオ")
        self.assertEqual(len(self.merge([a], [c])), 2)  # 局の名前の「ラジオ」だけで近いとしない

    def test_x_rows_are_left_to_newest_read(self):
        """X どうしは、同じ局・同じ時刻でもここではまとめない（画像の読み直しは新しい読み取りを残すため）。"""
        old = x_row(title="ヒダミノノ", venue="", start="18:00", media="ラジオ", station="岐阜放送")
        new = x_row(title="ヒダカノ", venue="", start="18:00", media="ラジオ", station="岐阜放送",
                    url="https://x.com/takanenofficial/status/2")
        self.assertEqual(len(self.merge([old], [new])), 2)


class SameEventStrictlyTest(unittest.TestCase):
    def test_calendar_name_in_article_body(self):
        """記事のタイトルは違っても、本文に公式の予定名がそのまま出てくれば同じ催し（SPACE MAKE の担当メンバー）。"""
        news = row(date="2026-10-26", title="高嶺のなでしこメンバーとオンライン個別お話し会", members="城月菜央、橋本桃呼",
                   venue="", url="https://example.invalid/news/1",
                   post_text="10月26日(月) ✿SPACE MAKE✿ 城月菜央、橋本桃呼")
        rows = quiet(fs.merge_post_rows, [row(date="2026-10-26", title="✿SPACE MAKE✿", venue="")], [news])
        self.assertEqual((len(rows), rows[0]["members"]), (1, "城月菜央、橋本桃呼"))

    def test_calendar_name_with_member_names_in_article_body(self):
        """カレンダーの予定名にメンバーの名前が付いても、名前を除いた予定名が本文にあれば同じ催し（11/25・11/26 の SPACE MAKE）。"""
        members = "橋本桃呼、東山恵里沙、日向端ひな、松本ももな、籾山ひめり"
        parts = "①18:00-18:30 ／ … ／ ⑥21:00-21:30"
        news = row(date="2026-11-26", title="高嶺のなでしこメンバーとオンライン個別お話し会", members=members, venue="",
                   time_note=parts, url="https://example.invalid/news/1",
                   post_text="11月26日(木) SPACE MAKEにて、オンライン個別お話し会を開催いたします。")
        calendar = row(date="2026-11-26", title="SPACE MAKE✿橋本・東山・日向端・松本・籾山", members=members, venue="",
                       time_note=parts)
        rows = quiet(fs.merge_post_rows, [calendar], [news])
        self.assertEqual([r["url"] for r in rows], [calendar["url"]])

    def test_title_of_member_names_only_is_not_enough(self):
        """名前を除くと短くなる予定名（6 文字未満）は、本文に出てきてもまとめない。"""
        news = row(title="ファンクラブイベント", venue="", url="https://example.invalid/news/1",
                   post_text="城月菜央 生誕祭の物販についてのお知らせ")
        self.assertEqual(len(quiet(fs.merge_post_rows, [row(title="城月菜央 生誕祭", venue="")], [news])), 2)

    def test_without_members(self):
        self.assertEqual(fs.without_members("SPACE MAKE✿城月・涼海・葉月"), "SPACE MAKE✿・・")
        self.assertEqual(fs.without_members("日向端ひな 生誕祭 ひなまつり"), " 生誕祭 ひなまつり")

    def test_generic_words_in_body_are_not_enough(self):
        news = row(title="タワーレコードキャンペーン：：涼海すう レシート", members="涼海すう", venue="",
                   post_text="ニューアルバム『A World in Our Colors』リリース記念 タワーレコードキャンペーン")
        self.assertFalse(fs.same_event_strictly(news, row(title="ニューアルバム『A World in Our Colors』リリース記念イベント")))


class WarnDuplicatesTest(unittest.TestCase):
    def test_same_members_and_times_are_reported(self):
        parts = "①18:00-18:30 ／ … ／ ⑥21:00-21:30"
        rows = [row(title="SPACE MAKE✿城月・涼海・葉月", members="城月菜央、涼海すう、葉月紗蘭", time_note=parts),
                row(title="メンバーとオンライン個別お話し会", members="城月菜央、涼海すう、葉月紗蘭", time_note=parts),
                row(title="全員のミニライブ", start="18:00"), row(title="全員の特典会", start="18:00"),
                row(title="時刻のない予定", members="城月菜央"), row(title="時刻のない別の予定", members="城月菜央")]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            fs.warn_duplicates(rows)
        self.assertEqual(out.getvalue().splitlines(),
                         ["  ! 同じ予定が分かれている可能性: 2026-11-20 SPACE MAKE✿城月・涼海・葉月 ／ メンバーとオンライン個別お話し会"])


class FindTimesTest(unittest.TestCase):
    def test_part_without_label_is_kept(self):
        """1 部にだけ部の表記がない（9/19 ららぽーとTOKYO-BAY）ときも、書かれた順に両方拾う。"""
        lines = ["2026年9月19日（土）", "12:00 START（優先エリア入場11:40）", "内容：ミニライブ＆グループ特典会",
                 "2部\u300015:30 START（優先エリア入場15:10）", "2026年9月19日(土)\u300010:00～"]
        self.assertEqual(fs.find_part_times(lines), "12:00 START（優先エリア入場11:40） ／ 2部 15:30 START（優先エリア入場15:10）")

    def test_parts_with_labels(self):
        lines = ["1部 12:00 START（優先エリア入場11:40）", "2部 15:30 START（優先エリア入場15:10）"]
        self.assertEqual(fs.find_part_times(lines), " ／ ".join(lines))

    def test_time_line(self):
        """「時間：15:00/ 優先エリアご入場開始：14:40」は開始（9/22 イオンレイクタウン）。"""
        lines = ["■CD販売開始時間", "2026年9月22日(火/祝)\u300012:00～", "時間：15:00/ 優先エリアご入場開始：14:40"]
        self.assertEqual(fs.find_time_line(lines), {"START": "15:00"})
        self.assertEqual(fs.find_time_line(["◆開催時間：18:00〜20:00"]), {"START": "18:00", "END": "20:00"})

    def test_other_time_lines_are_not_read(self):
        lines = ["■CD販売開始時間", "受付時間：10:00〜17:00", "[受付時間] 月～金 (除く休日) 10:00～13:00", "集合時間になりましたら"]
        self.assertEqual(fs.find_time_line(lines), {})
