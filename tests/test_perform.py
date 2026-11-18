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
        found, _ = self.run_read(a, b)
        self.assertEqual(found["1"]["events"], [{"date": "2026-11-23", "start": "23:55", "end": "24:00", "station": "FM大阪",
                                                 "title": "HIT STREET"}])  # 読み違えた「ヒダノノ」は採らない
        self.assertEqual(found["1"]["unsure"], [["2026-11-30", "18:00", "岐阜放送"]])


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


class UnsureImageEventsTest(unittest.TestCase):
    """新しい投稿の画像で読み取りが食い違った予定は、古い投稿の画像から読んだ同じ予定も外す（read_x_posts）。"""

    def test_old_misread_is_dropped(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old = tweet("100", "Release campaign Schedule 📻", created="2026-11-08T12:00:00.000Z", photos=["https://example.invalid/a.jpg"])
        new = tweet("200", "Release campaign Schedule 📻", created="2026-11-16T13:30:00.000Z", photos=["https://example.invalid/b.jpg"])
        hidamino = {"date": "2026-11-30", "start": "18:00", "end": "18:25", "station": "岐阜放送", "title": "ヒダミノノ"}
        images = {"100": {"v": fs.X_IMAGE_VERSION, "perform": [], "notes": [], "events": [hidamino]},
                  "200": {"v": fs.X_IMAGE_VERSION, "perform": [], "notes": [], "events": [],
                          "unsure": [["2026-11-30", "18:00", "岐阜放送"]]}}
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
            events = fs.read_x_posts("token", [], [], [], 30)
        self.assertEqual([e for e in events if e.get("image")], [])
        del images["200"]["unsure"]  # 新しい画像で食い違いがなければ、古い画像の予定は残る
        with contextlib.redirect_stdout(io.StringIO()):
            events = fs.read_x_posts("token", [], [], [], 30)
        self.assertEqual([e["title"] for e in events if e.get("image")], ["ヒダミノノ"])


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
