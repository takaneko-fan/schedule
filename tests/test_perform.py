"""高嶺のなでしこの出演時間（perform）と告知画像の読み取りのテスト。

AI と X API は呼ばない。投稿は実際の告知の書き方に合わせて短く作り直したもの（告知文は転載しない）。
実行: python3 -m unittest discover -s tests
"""

import contextlib
import csv
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
import notify  # noqa: E402
from fetch_schedule import GROUP  # noqa: E402

TODAY = date(2026, 11, 18)


def row(**values):
    base = {"done": "", "date": "2026-12-07", "weekday": "月", "title": "MARQUEE祭 Vol.186", "category": "ライブ／イベント",
            "members": GROUP, "venue": "Spotify O-EAST", "url": "https://example.invalid/schedule/marquee",
            "open": "14:00", "start": "14:30", "end": "", "time_note": "", "venue_url": "", "memo": "", "uid": "u1"}
    base.update(values)
    return base


def tweet(tid, text, created="2026-10-23T07:30:00.000Z", photos=()):
    return {"id": tid, "text": text, "created_at": created, "photos": list(photos)}


class PerformTextTest(unittest.TestCase):
    def test_reads_group_slot(self):
        self.assertEqual(fs.perform_from_text("#高嶺のなでしこ 出演 19:50〜20:15\n物販 20:45〜"), "19:50-20:15")
        self.assertEqual(fs.perform_from_text("#高嶺のなでしこ は、\n🎤19:55~20:25 に変更"), "19:55-20:25")

    def test_ignores_ambiguous_or_other_times(self):
        self.assertEqual(fs.perform_from_text("⏰OPEN 14:00 / START 14:30"), "")
        self.assertEqual(fs.perform_from_text("DAY1 🎤13:00~13:20\nDAY2 🎤15:00~15:20"), "")  # 2 日分は決められない


class LabelsTest(unittest.TestCase):
    def test_time_label(self):
        self.assertEqual(fs.time_label(row(perform="19:50-20:15")), "OPEN 14:00 / START 14:30 ／ 🎤 出演 19:50〜20:15")
        self.assertEqual(fs.time_label(row(open="", start="", perform="19:50-20:15")), "🎤 出演 19:50〜20:15")
        self.assertEqual(fs.time_label(row()), "OPEN 14:00 / START 14:30")

    def test_csv_has_perform_as_last_column(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "s.csv")
        fs.write_csv([row(perform="19:50-20:15"), row(uid="u2")], path)
        with open(path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        self.assertEqual(reader.fieldnames[-2:], ["uid", "perform"])
        self.assertEqual([r["perform"] for r in rows], ["19:50-20:15", ""])

    def test_notify(self):
        old, new = row(), row(perform="19:50-20:15")
        self.assertIn(("decided", "出演時間決定", "", "19:50〜20:15"), notify.classify(old, new))
        self.assertIn(("important", "出演時間変更", "19:50〜20:15", "20:00〜20:25"),
                      notify.classify(new, row(perform="20:00-20:25")))
        self.assertEqual(notify.classify({k: v for k, v in old.items()}, old), [])  # 古い CSV（列なし）と同じなら何もない


class ImageReadTest(unittest.TestCase):
    TIMETABLE = tweet("1", "⏰タイムテーブル公開⏰\n#高嶺のなでしこ 出演『 MARQUEE祭 Vol.186 』", photos=["https://example.invalid/a.jpg"])

    def run_read(self, *results, tweets=None, texts=None):
        with mock.patch.object(fs, "x_image_read", side_effect=[(r, (100, 10)) for r in results]), \
                contextlib.redirect_stdout(io.StringIO()):
            return fs.read_x_images(tweets or [self.TIMETABLE], texts or {}, "openai", "key")

    def test_perform_needs_two_matching_reads(self):
        a = {"perform": [{"date": "2026-12-07", "start": "19:50", "end": "20:15"}], "notes": ["物販 20:45〜 2F ブース AB"]}
        found, tokens = self.run_read(a, a)
        self.assertEqual(found["1"], {"v": fs.X_IMAGE_VERSION, "perform": [("2026-12-07", "19:50", "20:15")], "events": [],
                                      "notes": ["物販 20:45〜 2F ブース AB"]})
        self.assertEqual(tokens, [200, 20])
        b = {"perform": [{"date": "2026-12-07", "start": "19:55", "end": "20:15"}], "notes": []}
        found, _ = self.run_read(a, b)
        self.assertEqual(found["1"]["perform"], [])  # 食い違えば採らない

    def test_only_announcement_posts_with_photos_not_read_before(self):
        photo = tweet("2", "✞ TAKANEKO HALLOWEEN ✞", photos=["https://example.invalid/b.jpg"])
        no_photo = tweet("3", "⏰タイムテーブル公開⏰")
        found, _ = self.run_read(tweets=[photo, no_photo, self.TIMETABLE], texts={"1": {"image": {"v": fs.X_IMAGE_VERSION}}})
        self.assertEqual(found, {})

    def test_old_version_is_read_again(self):
        a = {"perform": [], "events": [], "notes": []}
        found, _ = self.run_read(a, a, texts={"1": {"image": {"perform": [], "notes": []}}})  # 版のない前の読み取り
        self.assertEqual(list(found), ["1"])

    def test_image_events_need_two_matching_reads(self):
        a = {"perform": [], "notes": [], "events": [
            {"date": "2026-11-23", "start": "23:55", "end": "24:00", "station": "FM大阪", "title": "「HIT STREET」"},
            {"date": "2026-11-30", "start": "18:00", "end": "18:25", "station": "岐阜放送", "title": "ヒダミノ"}]}
        b = {"perform": [], "notes": [], "events": [
            {"date": "2026-11-23", "start": "23:55", "end": "24:00", "station": "FM大阪", "title": "HIT STREET"},
            {"date": "2026-11-30", "start": "18:00", "end": "18:25", "station": "岐阜放送", "title": "ヒダノノ"}]}
        b["events"].append({"date": "2026-12-01", "start": "18:00", "end": "", "station": "岐阜放送", "title": "別の日"})
        found, _ = self.run_read(a, b)
        # 番組名が食い違っても同じイベントと判断できれば採り、後（2 回目）の読み取りを使う。片方にしかない予定は採らない
        self.assertEqual([e["title"] for e in found["1"]["events"]], ["HIT STREET", "ヒダノノ"])


    def test_attach_photos(self):
        body = {"data": [{"id": "1", "attachments": {"media_keys": ["3_a", "7_b"]}}, {"id": "2"}],
                "includes": {"media": [{"media_key": "3_a", "type": "photo", "url": "https://pbs.example/a.jpg"},
                                       {"media_key": "7_b", "type": "video"}]}}
        self.assertEqual([t["photos"] for t in fs.attach_photos(body)], [["https://pbs.example/a.jpg"], []])

    def test_saved_image_is_kept_when_post_is_read_again(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "x_texts.json")
        image = {"perform": [["2026-12-07", "19:50", "20:15"]], "notes": []}
        fs.save_x_texts([self.TIMETABLE], TODAY, path, images={"1": image})
        fs.save_x_texts([self.TIMETABLE], TODAY, path)
        self.assertEqual(fs.load_x_texts(path)["1"]["image"], image)


class ImageEventsTest(unittest.TestCase):
    POST = tweet("5", "°˖✿ Release campaign Schedule ✿˖°\nメディア出演スケジュールを更新！🐈‍⬛📻", created="2026-11-16T13:30:00.000Z")

    def test_live_in_media_post_is_not_program(self):
        """本文に「📻」があっても、会場のある予定はライブ・イベント。局だけで種別が分からない番組は本文で決める。"""
        image = {"events": [{"date": "2026-11-21", "start": "12:00", "end": "", "station": "", "venue": "ステラタウン大宮",
                             "title": "ミニライブ"},
                            {"date": "2026-11-30", "start": "18:00", "end": "18:25", "station": "岐阜放送", "venue": "",
                             "title": "ヒダミノ"}]}
        with contextlib.redirect_stdout(io.StringIO()):
            live, radio = fs.image_candidates(self.POST, image)
        self.assertEqual((live["media"], live["venue"], live["station"]), ("", "ステラタウン大宮", ""))
        self.assertEqual((radio["media"], radio["station"], radio["venue"]), ("ラジオ", "岐阜放送", ""))

    def test_candidates(self):
        image = {"events": [{"date": "2026-11-23", "start": "23:55", "end": "24:00", "station": "FM大阪", "title": "HIT STREET"},
                            {"date": "2026-10-01", "start": "", "end": "", "station": "", "title": "過去の予定"}]}
        with contextlib.redirect_stdout(io.StringIO()):
            cands = fs.image_candidates(self.POST, image)
        self.assertEqual(len(cands), 1)
        c = cands[0]
        self.assertEqual((c["date"], c["title"], c["members"], c["media"], c["station"], c["times"], c["image"]),
                         (date(2026, 11, 23), "HIT STREET", GROUP, "ラジオ", "FM大阪", {"START": "23:55", "END": "24:00"}, True))
        e = fs.x_event(c)
        self.assertTrue(e["image"])
        r = fs.x_event_to_row(e)
        r["category"] = fs.X_CATEGORY
        fs.add_media([r])
        self.assertEqual((r["title"], r["category"]), ("HIT STREET（FM大阪）", "公式X / ラジオ / 画像読み取り"))
        self.assertEqual(fs.members_label(r), "メンバー未記載")
        self.assertEqual(notify.member_names(r), "メンバー未記載")
        self.assertIn("出演: メンバー未記載", fs.ics_description({**row(), **r}))


class SameImageEventTest(unittest.TestCase):
    def ev(self, title, start="18:00", station="岐阜放送", day="2026-11-30"):
        return {"date": day, "start": start, "end": "", "station": station, "title": title}

    def test_same_or_different(self):
        same = fs.same_image_event
        self.assertTrue(same(self.ev("ヒダミノノ"), self.ev("ヒダミノ")))  # 番組名の読み違い
        self.assertTrue(same(self.ev("ヒダカノ"), self.ev("全然違う名前")))  # 同じ局・同じ時刻
        self.assertTrue(same(self.ev("ヒダミノ", station="岐阜放送"), self.ev("ヒダミノ", station="ぎふチャン")))  # 局の書き方の違い
        self.assertFalse(same(self.ev("ヒダミノ"), self.ev("ヒダミノ", day="2026-12-01")))  # 日付が違う
        self.assertFalse(same(self.ev("ヒダミノ"), self.ev("ヒダミノ", start="19:00")))  # 開始時刻が違う
        self.assertFalse(same(self.ev("ドラ魂キング", "16:00", "CBCラジオ"), self.ev("江本一真のゴッジ", "16:00", "広島FM")))

    def test_live_events_use_venue(self):
        same = fs.same_image_event

        def live(title, venue, start="12:00"):
            return {"date": "2026-11-21", "start": start, "end": "", "station": "", "venue": venue, "title": title}
        # 名前が似ていても会場が違えば別（同じ日のリリースイベント）
        self.assertFalse(same(live("リリースイベント at ららぽーと立川立飛", "ららぽーと立川立飛"),
                              live("リリースイベント at ステラタウン大宮", "ステラタウン大宮")))
        self.assertTrue(same(live("ミニライブ&握手会", "ステラタウン大宮 メローペ広場"), live("ミニライブ＆グループ握手会", "ステラタウン大宮")))
        self.assertTrue(same(live("ミニライブ", "Zepp Haneda"), live("全然違う名前", "Zepp Haneda")))  # 同じ会場・同じ時刻
        self.assertFalse(same(live("ミニライブ", ""), live("トークショー", "")))  # 会場がなければ名前で判断


class LatestImageEventsTest(unittest.TestCase):
    """同じイベントがほかの投稿の画像にもあれば、新しい投稿の読み取りを使う（read_x_posts）。"""

    def run_posts(self, images):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old = tweet("100", "Release campaign Schedule 📻", created="2026-11-08T12:00:00.000Z", photos=["https://example.invalid/a.jpg"])
        new = tweet("200", "Release campaign Schedule 📻", created="2026-11-16T13:30:00.000Z", photos=["https://example.invalid/b.jpg"])
        patches = [mock.patch.object(fs, "X_STATE_PATH", os.path.join(tmp.name, "s.json")),
                   mock.patch.object(fs, "X_TEXTS_PATH", os.path.join(tmp.name, "t.json")),
                   mock.patch.object(fs, "AI_COMPARE_PATH", os.path.join(tmp.name, "c.jsonl")),
                   mock.patch.object(fs, "memo_provider", return_value=("openai", "", None)),
                   mock.patch.object(fs, "fetch_x_posts", return_value=([old, new], "200")),
                   mock.patch.object(fs, "read_x_images", return_value=(images, [0, 0]))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        with contextlib.redirect_stdout(io.StringIO()):
            return [e for e in fs.read_x_posts("token", [], [], [], 30) if e.get("image")]

    def image(self, *events):
        return {"v": fs.X_IMAGE_VERSION, "perform": [], "notes": [], "events": list(events)}

    def test_newest_read_wins(self):
        old = {"date": "2026-11-30", "start": "18:00", "end": "18:25", "station": "岐阜放送", "title": "ヒダミノノ"}
        new = {**old, "title": "ヒダカノ"}
        events = self.run_posts({"100": self.image(old), "200": self.image(new)})
        self.assertEqual([(e["title"], e["post_id"]) for e in events], [("ヒダカノ", "200")])

    def test_different_events_are_kept(self):
        a = {"date": "2026-11-30", "start": "18:00", "end": "18:25", "station": "岐阜放送", "title": "ヒダミノ"}
        b = {"date": "2026-11-23", "start": "23:55", "end": "24:00", "station": "FM大阪", "title": "HIT STREET"}
        events = self.run_posts({"100": self.image(a), "200": self.image(b)})
        self.assertEqual(sorted(e["title"] for e in events), ["HIT STREET", "ヒダミノ"])


class LinkXPostsTest(unittest.TestCase):
    """候補の予定が 2 件以上ある投稿は、どの予定についてかを AI で判断する（link_x_posts）。"""

    SANNO = row(date="2026-11-07", title="産業能率大学 第61回産能祭", venue="産業能率大学", url="https://example.invalid/sanno")
    LARME = row(date="2026-11-07", title="LARME FES’26", venue="東京ガーデンシアター", url="https://example.invalid/larme")
    POST = {"posted": "2026-11-06T20:30+09:00",
            "text": "#あしたのたかねこ\n『 産業能率大学 第61回産能祭 』\n⏰OPEN 12:00\n- - -\n『 LARME FES'26 』\n🎫見切れ席チケット販売中"}

    def run_link(self, texts, ai_links, cache=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "links.json")
        if cache is not None:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cache, f)
        read = mock.Mock(return_value=({"links": ai_links}, (10, 1)))
        with mock.patch.object(fs, "x_llm_read", read), contextlib.redirect_stdout(io.StringIO()):
            judged = fs.link_x_posts([self.SANNO, self.LARME], date(2026, 11, 6), "openai", "key", path, texts)
        with open(path, encoding="utf-8") as f:
            return judged, read, json.load(f)

    def test_multi_event_post_is_split(self):
        judged, read, cache = self.run_link({"1": self.POST}, [{"event": 0, "part": "産能祭 OPEN 12:00"},
                                                               {"event": 1, "part": "見切れ席チケット販売中"},
                                                               {"event": 9, "part": "候補にない番号"}])
        self.assertEqual(judged, {"1": {fs.row_key(self.SANNO): "産能祭 OPEN 12:00", fs.row_key(self.LARME): "見切れ席チケット販売中"}})
        related = fs.x_related_posts(self.LARME, {"1": self.POST}, judged)
        self.assertTrue(related[0][2].endswith("［この予定について書かれた部分］見切れ席チケット販売中"))
        # 投稿と候補が変わらなければ AI を呼ばない
        judged2, read2, _ = self.run_link({"1": self.POST}, [], cache)
        self.assertEqual((judged2, read2.call_count), (judged, 0))

    def test_single_candidate_uses_rules(self):
        post = {"posted": "2026-11-06T20:30+09:00", "text": "#あしたのたかねこ\n『 LARME FES'26 』"}
        texts = {"1": post}
        with mock.patch.object(fs, "x_llm_read") as read, contextlib.redirect_stdout(io.StringIO()):
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            judged = fs.link_x_posts([self.LARME], date(2026, 11, 6), "openai", "key", os.path.join(tmp.name, "l.json"), texts)
        self.assertEqual((judged, read.call_count), ({}, 0))
        self.assertEqual(len(fs.x_related_posts(self.LARME, texts, judged)), 1)

    def test_ai_says_not_related(self):
        judged, _, _ = self.run_link({"1": self.POST}, [{"event": 0, "part": "産能祭"}])
        self.assertEqual(fs.x_related_posts(self.LARME, {"1": self.POST}, judged), [])  # 名前が本文にあっても AI の判断に従う

    def test_no_texts_no_judgement(self):
        """投稿の記録がない環境（手元など）では判断しない（AI も呼ばない）。"""
        with mock.patch.object(fs, "load_x_texts", return_value=None), mock.patch.object(fs, "x_llm_read") as read:
            self.assertEqual(fs.link_x_posts([self.SANNO, self.LARME], date(2026, 11, 6), "openai", "key"), {})
        self.assertEqual(read.call_count, 0)


class AddPerformTest(unittest.TestCase):
    def run_add(self, rows, texts, saved=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "perform.json")
        if saved is not None:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(saved, f)
        with mock.patch.object(fs, "load_x_texts", return_value=texts), contextlib.redirect_stdout(io.StringIO()):
            fs.add_perform(rows, TODAY, path)
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_from_text_and_image_newest_wins(self):
        r = row()
        texts = {"10": {"posted": "2026-10-23T16:30+09:00", "text": "🗓️12月7日(月)\n『 MARQUEE祭 Vol.186 』\n#高嶺のなでしこ 出演 19:50〜20:15"},
                 "11": {"posted": "2026-12-06T20:00+09:00", "text": "⏰タイムテーブル公開⏰\n『 MARQUEE祭 Vol.186 』",
                        "image": {"perform": [["2026-12-07", "20:00", "20:25"]], "notes": []}}}
        saved = self.run_add([r], texts)
        self.assertEqual(r["perform"], "20:00-20:25")
        self.assertEqual(saved[f"{r['date']}|{r['url']}"]["url"], "https://x.com/takanenofficial/status/11")

    def test_image_without_name_needs_single_event_that_day(self):
        image = {"perform": [["2026-12-07", "19:50", "20:15"]], "notes": []}
        texts = {"11": {"posted": "2026-12-06T20:00+09:00", "text": "⏰タイムテーブル公開⏰", "image": image}}
        r = row()
        self.run_add([r], texts)
        self.assertEqual(r["perform"], "19:50-20:15")
        rows = [row(), row(title="別のフェス", url="https://example.invalid/2", venue="Zepp")]
        self.run_add(rows, texts)
        self.assertEqual([x.get("perform") for x in rows], [None, None])  # 同じ日に 2 つあれば決めない

    def test_image_slot_needs_end(self):
        texts = {"11": {"posted": "2026-11-02T20:00+09:00", "text": "リリイベ情報更新 MARQUEE祭 Vol.186",
                        "image": {"perform": [["2026-12-07", "17:00", ""]], "notes": []}}}
        r = row()
        self.run_add([r], texts)
        self.assertNotIn("perform", r)

    def test_saved_result_is_used_without_texts(self):
        r = row()
        cid = f"{r['date']}|{r['url']}"
        saved = self.run_add([r], None, {cid: {"perform": "19:50-20:15", "posted": "2026-10-23T16:30+09:00", "url": "u"}})
        self.assertEqual((r["perform"], list(saved)), ("19:50-20:15", [cid]))

    def test_programs_are_skipped(self):
        r = row(category="公式X / ラジオ")
        self.run_add([r], {"1": {"posted": "2026-12-06T20:00+09:00", "text": "12月7日 MARQUEE祭 Vol.186 #高嶺のなでしこ 出演 19:50〜20:15"}})
        self.assertNotIn("perform", r)


class RelatedImageNotesTest(unittest.TestCase):
    def test_image_notes_go_to_memo_material(self):
        texts = {"1": {"posted": "2026-12-06T20:00+09:00", "text": "⏰タイムテーブル公開⏰\n『 MARQUEE祭 Vol.186 』",
                       "image": {"perform": [["2026-12-07", "19:50", "20:15"]], "notes": ["物販は 2F ブース AB で 20:45〜"]}}}
        related = fs.x_related_posts(row(), texts)  # 本文に日付がなくても、画像の出演時間の日付で結び付ける
        self.assertEqual(len(related), 1)
        self.assertIn("［画像から読み取り］\n物販は 2F ブース AB で 20:45〜", related[0][2])


if __name__ == "__main__":
    unittest.main()
