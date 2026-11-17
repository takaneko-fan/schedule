"""参加メモの「📣 公式 X の最新情報」（投稿の本文の保存、予定との結び付け、AI の結果の整え方、add_memos）のテスト。

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
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fetch_schedule as fs  # noqa: E402
from fetch_schedule import GROUP  # noqa: E402

TODAY = date(2026, 11, 17)
MARQUEE = ("🎤出演情報🎤\n\n『 MARQUEE祭 Vol.186 』\n\n#高嶺のなでしこ の出演が決定\n\n🗓️12月7日(月)\n⏰OPEN 14:00 / START 14:30\n"
           "📍渋谷Spotify O-EAST\n\n#高嶺のなでしこ 出演 19:50〜20:15\n物販 20:45〜 ブースAB（2F）")


def row(**values):
    base = {"date": "2026-12-07", "weekday": "月", "title": "MARQUEE祭 Vol.186", "category": "ライブ／イベント",
            "members": GROUP, "venue": "Spotify O-EAST", "url": "https://example.invalid/schedule/marquee",
            "open": "14:00", "start": "14:30", "end": "", "time_note": ""}
    base.update(values)
    return base


def texts(*posts):
    """x_texts.json の形（投稿 ID → {posted, text}）。"""
    return {str(i): {"posted": posted, "text": text} for i, (posted, text) in enumerate(posts, 1)}


class MemoTextTest(unittest.TestCase):
    def test_x_items_and_footer(self):
        memo = {"venue": "Spotify O-EAST"}
        self.assertEqual(fs.memo_text(memo).splitlines(), ["📍 会場: Spotify O-EAST", fs.MEMO_FOOTER])
        self.assertEqual(fs.memo_text(memo, ["出演は19:50〜20:15（10/23 の投稿）", "物販は20:45〜（10/23 の投稿）"]).splitlines(),
                         ["📍 会場: Spotify O-EAST",
                          "📣 公式 X の最新情報: 出演は19:50〜20:15（10/23 の投稿）／物販は20:45〜（10/23 の投稿）",
                          fs.MEMO_FOOTER_X])


class XTextsTest(unittest.TestCase):
    def test_save_and_prune(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "state", "x_texts.json")
        self.assertIsNone(fs.load_x_texts(path))
        fs.save_x_texts([{"id": "1", "created_at": "2025-01-01T00:00:00.000Z", "text": "古い投稿"},
                         {"id": "2", "created_at": "2026-10-23T07:30:00.000Z", "text": "本文 https://t.co/abc"}], TODAY, path)
        saved = fs.load_x_texts(path)
        self.assertEqual(list(saved), ["2"])  # 400 日を過ぎた投稿は捨てる
        self.assertEqual(saved["2"], {"posted": "2026-10-23T16:30+09:00", "text": "本文"})  # t.co の URL は除く


class RelatedPostsTest(unittest.TestCase):
    def test_date_and_name(self):
        related = fs.x_related_posts(row(), texts(
            ("2026-10-23T16:30+09:00", MARQUEE),
            ("2026-10-24T10:00+09:00", "🗓️12月7日(月) 別のフェス 出演決定"),  # 予定名・会場が違う
            ("2026-10-25T10:00+09:00", "MARQUEE祭 Vol.186 のチケット発売中"),  # 日付がない
            ("2026-12-08T10:00+09:00", "12月7日 MARQUEE祭 Vol.186 ありがとうございました")))  # 予定の後
        self.assertEqual([pid for _, pid, _ in related], ["1"])

    def test_today_and_tomorrow_posts(self):
        r = row(title="リリース記念イベント at ららぽーと立川立飛", venue="ららぽーと立川立飛2F イベント広場", date="2026-11-18")
        related = fs.x_related_posts(r, texts(
            ("2026-11-18T09:40+09:00", "🌸 #きょうのたかねこ 🌸 リリースイベント 📍ららぽーと立川立飛"),
            ("2026-11-17T20:30+09:00", "#あしたのたかねこ リリースイベント at ららぽーと立川立飛"),
            ("2026-11-16T20:30+09:00", "#あしたのたかねこ ららぽーと立川立飛")))  # 前々日の「あした」は別の日
        self.assertEqual([pid for _, pid, _ in related], ["2", "1"])  # 投稿日の古い順

    def test_store_campaign_post_is_not_linked_to_event(self):
        """同じ店で開くリリースイベントに、店舗のキャンペーンだけの投稿を結び付けない。"""
        r = row(title="リリース記念イベント at ららぽーと立川立飛", venue="ららぽーと立川立飛2F イベント広場", date="2026-11-18")
        related = fs.x_related_posts(r, texts(
            ("2026-11-16T12:00+09:00", "11月18日 タワーレコードららぽーと立川立飛店 サイン入りレシート配布・パネル展"),
            ("2026-11-16T13:00+09:00", "11月18日 ららぽーと立川立飛 ミニライブ&特典会 パネル展も開催")))
        self.assertEqual([pid for _, pid, _ in related], ["2"])

    def test_tbd_venue_is_not_used_for_matching(self):
        r = row(title="リリース記念個別特典イベント", venue="東京都内某所", date="2026-11-22")
        self.assertEqual(fs.x_related_posts(r, texts(("2026-11-16T20:00+09:00", "11月22日(日) 東京都内某所 握手会"))), [])


class XMemoItemsTest(unittest.TestCase):
    def test_items_get_post_date_and_bad_index_is_dropped(self):
        related = [("2026-10-23T16:30+09:00", "1", MARQUEE)]
        result = {"items": [{"text": "出演時刻は19:50〜20:15。", "post": 0}, {"text": "存在しない投稿", "post": 3},
                            {"text": "  ", "post": 0}]}
        with mock.patch.object(fs, "x_llm_read", return_value=(result, (100, 10))):
            items, usage = fs.x_memo_items(row(), "📍 会場: Spotify O-EAST", related, TODAY, "openai", "key")
        self.assertEqual((items, usage), (["出演時刻は19:50〜20:15（10/23 の投稿）"], (100, 10)))

    def test_row_times_are_given_to_ai(self):
        """予定の行にある時刻・会場を渡す（AI がそれを繰り返さないように）。"""
        related = [("2026-10-23T16:30+09:00", "1", MARQUEE)]
        with mock.patch.object(fs, "x_llm_read", return_value=({"items": []}, (1, 1))) as read:
            fs.x_memo_items(row(), "", related, TODAY, "openai", "key")
        self.assertIn("時刻: 開場 14:00／開演 14:30／会場: Spotify O-EAST", read.call_args[0][0])


class AddMemosTest(unittest.TestCase):
    """add_memos の 📣 の扱い（公式の参加メモは材料がなく作らない形で確かめる）。"""

    def run_add(self, rows, cache, x_texts, ai_items=()):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cache_path = os.path.join(tmp.name, "memos.json")
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        calls = mock.Mock(return_value=({"items": [{"text": t, "post": 0} for t in ai_items]}, (10, 1)))
        with mock.patch.object(fs, "load_x_texts", return_value=x_texts), \
                mock.patch.object(fs, "memo_provider", return_value=("openai", "key", None)), \
                mock.patch.object(fs, "x_llm_read", calls), contextlib.redirect_stdout(io.StringIO()):
            fs.add_memos(rows, [], TODAY, cache_path)
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f), calls

    def test_x_only_event_gets_x_memo(self):
        r = row(category="公式X", url="https://x.com/takanenofficial/status/1")
        cache, calls = self.run_add([r], {}, texts(("2026-10-23T16:30+09:00", MARQUEE)), ["出演時刻は19:50〜20:15"])
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(r["memo"].splitlines(),
                         ["📣 公式 X の最新情報: 出演時刻は19:50〜20:15（10/23 の投稿）", fs.MEMO_FOOTER_X])
        self.assertEqual(cache[f"{r['date']}|{r['url']}"]["x_items"], ["出演時刻は19:50〜20:15（10/23 の投稿）"])

    def test_unchanged_posts_are_not_read_again(self):
        r = row(category="公式X", url="https://x.com/takanenofficial/status/1")
        x_texts = texts(("2026-10-23T16:30+09:00", MARQUEE))
        cache, _ = self.run_add([dict(r)], {}, x_texts, ["出演時刻は19:50〜20:15"])
        cache, calls = self.run_add([r], cache, x_texts, ["呼ばれないはず"])
        self.assertEqual(calls.call_count, 0)
        self.assertIn("出演時刻は19:50〜20:15", r["memo"])

    def test_items_are_kept_where_posts_are_not_saved(self):
        """投稿の本文がない環境（手元など）では、保存済みの 📣 の項目をそのまま使う。"""
        r = row(category="公式X", url="https://x.com/takanenofficial/status/1")
        cid = f"{r['date']}|{r['url']}"
        cache = {cid: {"hash": "", "model": "m", "generated": "2026-11-16", "memo": {}, "x_hash": "h",
                       "x_items": ["出演時刻は19:50〜20:15（10/23 の投稿）"]}}
        cache, calls = self.run_add([r], cache, None)
        self.assertEqual(calls.call_count, 0)
        self.assertEqual(cache[cid]["x_items"], ["出演時刻は19:50〜20:15（10/23 の投稿）"])

    def test_campaign_rows_get_no_x_items(self):
        r = row(title="リリース記念 タワーレコードキャンペーン", category="公式X", url="https://x.com/takanenofficial/status/3")
        cache, calls = self.run_add([r], {}, texts(("2026-10-23T16:30+09:00", "12月7日 リリース記念 タワーレコードキャンペーン 開催")),
                                    ["何か"])
        self.assertEqual((calls.call_count, cache), (0, {}))

    def test_programs_and_past_events_are_skipped(self):
        rows = [row(category="公式X / ラジオ", url="https://x.com/1"), row(date="2026-11-01", category="公式X", url="https://x.com/2")]
        cache, calls = self.run_add(rows, {}, texts(("2026-10-23T16:30+09:00", MARQUEE)), ["何か"])
        self.assertEqual((calls.call_count, cache), (0, {}))


if __name__ == "__main__":
    unittest.main()
