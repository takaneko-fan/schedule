"""ルールと AI の読み取りの比べ合わせ（x_compare・record_ai_compare・ops/ai_compare_report.py）のテスト。

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
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ops"))

import ai_compare_report  # noqa: E402
import fetch_schedule as fs  # noqa: E402
from fetch_schedule import GROUP  # noqa: E402


def cand(**values):
    """extract_x_candidates・x_llm_candidates と同じ形の予定。"""
    base = {"date": date(2026, 12, 7), "title": "MARQUEE祭 Vol.186", "venue": "渋谷Spotify O-EAST",
            "times": {"OPEN": "14:00", "START": "14:30"}, "time_note": "", "members": GROUP, "sure": True,
            "today": False}
    base.update(values)
    return base


TWEET = {"id": "500", "created_at": "2026-10-23T07:30:00.000Z", "text": "🎤出演情報🎤\n\n『 MARQUEE祭 Vol.186 』"}


class CompareTest(unittest.TestCase):
    def test_all_fields_match(self):
        pairs = fs.x_compare([cand()], [cand(title="MARQUEE祭 Vol.186", venue="Spotify O-EAST", times={"START": "14:30"})])
        self.assertEqual(pairs[0]["fields"], {"title": "一致", "venue": "一致", "start": "一致", "members": "一致"})

    def test_mismatch_and_one_side(self):
        pairs = fs.x_compare([cand()], [cand(venue="Zepp Shinjuku", times={"START": "15:00"}, members="城月菜央")])
        self.assertEqual(pairs[0]["fields"], {"title": "一致", "venue": "食い違い", "start": "食い違い", "members": "食い違い"})
        pairs = fs.x_compare([cand(venue="")], [cand()])
        self.assertEqual(pairs[0]["fields"]["venue"], "片方だけ")
        pairs = fs.x_compare([cand(venue="")], [cand(venue="")])
        self.assertEqual(pairs[0]["fields"]["venue"], "どちらもなし")

    def test_pairs_by_date_and_title(self):
        rule = [cand(title="ミニライブ", venue="A"), cand(title="個別握手会", venue="B")]
        ai = [cand(title="個別握手会", venue="B"), cand(title="ミニライブ", venue="A"), cand(date=date(2026, 12, 8))]
        pairs = fs.x_compare(rule, ai)
        self.assertEqual([(p["rule"] and p["rule"]["title"], p["ai"] and p["ai"]["title"]) for p in pairs],
                         [("ミニライブ", "ミニライブ"), ("個別握手会", "個別握手会"), (None, "MARQUEE祭 Vol.186")])
        self.assertEqual(fs.x_compare([cand()], [])[0]["ai"], None)

    def test_leading_zero_of_time(self):
        pairs = fs.x_compare([cand(times={"START": "9:30"})], [cand(times={"START": "09:30"})])
        self.assertEqual(pairs[0]["fields"]["start"], "一致")


class RecordTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "state", "ai_compare.jsonl")
        patcher = mock.patch.object(fs, "AI_COMPARE_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def record(self, rule, ai, kind="比較"):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            fs.record_ai_compare(TWEET, rule, ai, kind)
        return out.getvalue()

    def test_appends_one_line_per_post_without_text(self):
        self.assertIn("一致", self.record([cand()], [cand()]))
        log = self.record([cand()], [cand(venue="Zepp Shinjuku")])
        self.assertIn("差分あり: 2026-12-07 venue:食い違い", log)
        with open(self.path, encoding="utf-8") as f:
            entries = [json.loads(line) for line in f]
        self.assertEqual(len(entries), 2)
        self.assertEqual((entries[0]["kind"], entries[0]["post_id"], entries[0]["posted"]),
                         ("比較", "500", "2026-10-23T16:30+09:00"))
        self.assertNotIn("出演情報", json.dumps(entries, ensure_ascii=False))  # 投稿の本文は残さない

    def test_read_x_with_llm_records(self):
        with mock.patch.object(fs, "x_llm_candidates", return_value=[cand()]):
            with contextlib.redirect_stdout(io.StringIO()):
                fs.read_x_with_llm(TWEET, [cand()], "openai", "key", {}, [0, 0])  # ルールで読めた
                fs.read_x_with_llm(TWEET, [cand(sure=False)], "openai", "key", {}, [0, 0])  # AI だけ
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual([json.loads(line)["kind"] for line in f], ["比較", "AI のみ"])


class ReportTest(unittest.TestCase):
    def test_report(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "ai_compare.jsonl")
        with mock.patch.object(fs, "AI_COMPARE_PATH", path), contextlib.redirect_stdout(io.StringIO()):
            fs.record_ai_compare(TWEET, [cand()], [cand()], "比較")
            fs.record_ai_compare(dict(TWEET, id="501"), [cand()], [cand(venue="Zepp Shinjuku")], "比較")
            fs.record_ai_compare(dict(TWEET, id="502"), [], [cand(title="ラジオ番組")], "AI のみ")
        text = ai_compare_report.report(ai_compare_report.load(path, None))
        self.assertIn("投稿: 3 件（両方で読んだ 2 件、AI だけが読めた 1 件）", text)
        self.assertIn("会場: 一致 1 / 食い違い 1 / 片方だけ 0 / どちらもなし 0（一致率 50%）", text)
        self.assertIn("会場が食い違った・片方だけの例（1 件中 1 件）", text)
        self.assertIn("AI だけが読めた投稿", text)
        self.assertIn("ラジオ番組", text)
        self.assertEqual(ai_compare_report.load(path, "2026-10-24"), [])  # --since


class AiExtraTest(unittest.TestCase):
    """ルールで読めた投稿でも、ルールが取りこぼした予定（AI だけが読めた予定）は足す。"""

    TWEET = {"id": "600", "created_at": "2026-11-17T03:30:00.000Z",
             "text": "📻本日オンエア📻\n\n⏰16:00~18:54\nFM長崎\n『 Colors 』\n\n城月菜央 橋本桃呼が出演\n\n- - - -\n\n"
                     "⏰21:00~22:50\nRKBラジオ\n『 カリメン 』\n\n涼海すう 籾山ひめりが出演"}

    def program(self, **values):
        return cand(**{"date": date(2026, 11, 17), "title": "Colors", "venue": "", "media": "ラジオ", "today": True,
                       "times": {"START": "16:00", "END": "18:54"}, "members": "城月菜央、橋本桃呼", **values})

    def read(self, cands, ai):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(fs, "AI_COMPARE_PATH", os.path.join(tmp.name, "c.jsonl")), \
                mock.patch.object(fs, "x_llm_candidates", return_value=ai), contextlib.redirect_stdout(io.StringIO()):
            return fs.read_x_with_llm(self.TWEET, cands, "openai", "key", {}, [0, 0])

    def test_second_program_is_added(self):
        karimen = self.program(title="カリメン", times={"START": "21:00", "END": "22:50"}, members="涼海すう、籾山ひめり")
        out = self.read([self.program()], [self.program(), karimen])
        self.assertEqual([c["title"] for c in out], ["Colors", "カリメン"])

    def test_same_event_with_other_date_is_not_added(self):
        """AI が同じ予定を別の日付で読んでも、予定名が重なれば足さない（二重にしない）。"""
        out = self.read([self.program()], [self.program(date=date(2026, 11, 18))])
        self.assertEqual([c["title"] for c in out], ["Colors"])

    def test_nothing_extra(self):
        self.assertEqual(len(self.read([self.program()], [self.program()])), 1)
        self.assertEqual(len(self.read([self.program()], [])), 1)


class SaveAllEventsOfPostTest(unittest.TestCase):
    """1 つの投稿に同じ日の予定が複数あっても、すべて x_events.json に保存する（read_x_posts）。"""

    def read_posts(self, events, ai):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tweet = AiExtraTest.TWEET
        common = fs.x_common(tweet)
        patches = [mock.patch.object(fs, "X_STATE_PATH", os.path.join(tmp.name, "x_state.json")),
                   mock.patch.object(fs, "AI_COMPARE_PATH", os.path.join(tmp.name, "c.jsonl")),
                   mock.patch.object(fs, "X_TEXTS_PATH", os.path.join(tmp.name, "x_texts.json")),
                   mock.patch.object(fs, "fetch_x_posts", return_value=([tweet], tweet["id"])),
                   mock.patch.object(fs, "memo_provider", return_value=("openai", "key", None)),
                   mock.patch.object(fs, "x_llm_candidates", return_value=[dict(c, **common, ai=True) for c in ai])]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        with contextlib.redirect_stdout(io.StringIO()):
            return fs.read_x_posts("token", [], events, [], None)

    def programs(self):
        t = AiExtraTest()
        return [t.program(station="FM長崎"),
                t.program(title="カリメン", times={"START": "21:00", "END": "22:50"}, members="涼海すう、籾山ひめり",
                          station="RKBラジオ")]

    def test_two_programs_on_same_day_are_both_saved(self):
        saved = self.read_posts([], self.programs())
        self.assertEqual(sorted(e["title"] for e in saved), ["FM長崎 『Colors』", "カリメン"])

    def test_reread_replaces_saved_events_of_the_post_only(self):
        old = {"date": "2026-11-17", "title": "古い読み取り", "post_id": "600", "venue": "", "open": "", "start": "",
               "end": "", "time_note": "", "members": GROUP, "names": [], "url": "x", "posted": "", "media": "ラジオ"}
        saved = self.read_posts([old, dict(old, title="別の投稿", post_id="599")], self.programs())
        self.assertEqual(sorted(e["title"] for e in saved), ["FM長崎 『Colors』", "カリメン", "別の投稿"])


if __name__ == "__main__":
    unittest.main()
