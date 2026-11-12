"""公式 X の変更・中止の告知の AI 読み取り（x_change_reject・x_change_read・apply_x_change）のテスト。

AI の返事は固定し、AI は呼ばない。投稿は実際の告知の書き方に合わせて短く作り直したもの（告知文は転載しない）。
実行: python3 -m unittest discover -s tests
"""

import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fetch_schedule as fs  # noqa: E402
import notify  # noqa: E402
from fetch_schedule import GROUP  # noqa: E402

# 7/04 13:20（日本時間）の投稿
TIME_CHANGE = ("【開場/開演時間変更のお知らせ】\n\n「星谷美来卒業公演〜ずっと恋の病でいてね❤️〜」につきまして\n"
               "開場/開演時間に変更がございます。\n\n🗓️8月25日(火)\n⏰OPEN 17:30 / START 18:30\n📍恵比寿ガーデンホール")
# 6/02 19:00（日本時間）の投稿
TIMETABLE = ("📢お知らせ📢\n\n明日6/3(水)出演『idoLove You♬ -On Stage QR-vol.1』\n台風の接近に伴い、タイムテーブルの変更がございます。\n"
             "#高嶺のなでしこ は、\n🎤19:55~20:20 出演\n🛒20:50~21:50 物販\nとなります")


def tweet(text, created_at="2026-07-04T04:20:00.000Z", tweet_id="200"):
    return {"id": tweet_id, "created_at": created_at, "text": text}


def row(**values):
    base = {"date": "2026-08-25", "open": "17:00", "start": "18:00", "end": "", "time_note": "",
            "title": "星谷美来卒業公演〜ずっと恋の病でいてね〜", "category": "ライブ／イベント", "members": GROUP,
            "venue": "恵比寿ガーデンホール", "url": "https://example.invalid/schedule/grad"}
    base.update(values)
    return base


def result(**values):
    """AI の返事（X_CHANGE_SCHEMA の形）。"""
    base = {"type": "change", "event": 0, "when": "date", "date_text": "8月25日(火)", "part": "",
            "changes": [{"field": "open", "after": "17:30"}, {"field": "start", "after": "18:30"}],
            "members": [], "evidence": "開場/開演時間に変更がございます。"}
    base.update(values)
    return base


def reject(res, text=TIME_CHANGE, events=None, created_at="2026-07-04T04:20:00.000Z"):
    t = tweet(text, created_at)
    return fs.x_change_reject(res, "\n".join(fs.x_lines(t)), fs.x_posted(t), events or [row()])


def read(res, rows, text=TIME_CHANGE, created_at="2026-07-04T04:20:00.000Z"):
    with mock.patch.object(fs, "x_llm_read", return_value=(res, (100, 10))), \
            contextlib.redirect_stdout(io.StringIO()):
        return fs.x_change_read(tweet(text, created_at), rows, "openai", "key", [0, 0])


class RejectTest(unittest.TestCase):
    def test_accepts_time_change(self):
        self.assertIsNone(reject(result()))

    def test_caution_and_other(self):
        self.assertEqual(reject(result(type="caution")), "注意書き")
        self.assertEqual(reject(result(type="other")), "変更・中止の告知ではない")

    def test_evidence_must_be_a_notice_sentence_in_text(self):
        self.assertEqual(reject(result(evidence="開演時間が 19:00 に変わりました。")), "根拠が本文にない")
        caution = "※ミニライブは天候次第で内容変更または中止となる場合があります。"
        self.assertEqual(reject(result(type="cancel", evidence=caution), text=TIME_CHANGE + "\n" + caution),
                         "根拠が告知の文ではない")
        self.assertEqual(reject(result(type="cancel")), "根拠が告知の文ではない")  # 根拠に「中止」がない

    def test_event_is_resolved_or_rejected(self):
        self.assertIsNone(reject(result(event=-1)))  # その日の予定が 1 件だけならそれ
        two = [row(), row(title="別の公演", url="https://example.invalid/schedule/2")]
        self.assertEqual(reject(result(event=-1), events=two), "予定が決まらない")
        self.assertEqual(reject(result(event=5), events=two), "予定が決まらない")  # 一覧にない番号も -1 と同じ扱い

    def test_date_must_match_event(self):
        self.assertEqual(reject(result(date_text="8月25日(火)"), events=[row(date="2026-08-26")]), "日付が予定と違う")

    def test_past_event(self):
        self.assertEqual(reject(result(), created_at="2026-08-26T03:00:00.000Z"), "終わった予定")

    def test_name_or_date_needed(self):
        self.assertEqual(reject(result(when="unknown", date_text=""), events=[row(title="別の公演", venue="Zepp")]),
                         "予定名・会場・日付が本文にない")
        self.assertIsNone(reject(result(when="unknown", date_text="")))  # 予定名が本文にある

    def test_new_values_must_be_in_text(self):
        self.assertEqual(reject(result(changes=[{"field": "start", "after": "19:00"}])), "変更後の時刻が本文にない")
        self.assertEqual(reject(result(changes=[{"field": "venue", "after": "Zepp Shinjuku"}])), "変更後の会場が本文にない")
        self.assertEqual(reject(result(changes=[{"field": "time_note", "after": "1部 13:00"}])), "変更後の時間が本文にない")
        self.assertEqual(reject(result(changes=[])), "変更の内容がない")

    def test_absent_and_partial(self):
        text = "📢お知らせ📢\n\n8月25日(火)の卒業公演につきまして、城月菜央は体調不良のため欠席となります。\nミニライブは中止とさせていただきます。"
        absent = result(type="absent", changes=[], members=["城月菜央"], evidence="城月菜央は体調不良のため欠席となります。")
        self.assertIsNone(reject(absent, text=text))
        self.assertEqual(reject(dict(absent, members=["松本ももな"]), text=text), "メンバーが本文にない")
        partial = result(type="partial_cancel", changes=[], part="", evidence="ミニライブは中止とさせていただきます。")
        self.assertEqual(reject(partial, text=text), "中止の内容が本文にない")
        self.assertIsNone(reject(dict(partial, part="ミニライブ"), text=text))


class ReadTest(unittest.TestCase):
    def test_time_change_records_before_values(self):
        n = read(result(changes=[{"field": "open", "after": "17:30"}, {"field": "start", "after": "18:30"},
                                 {"field": "venue", "after": "恵比寿ガーデンホール"}]), [row()])
        self.assertEqual((n["status"], n["label"], n["date"], n["ai"]), ("change", "時刻変更", "2026-08-25", True))
        # 変わっていない会場は捨て、変更前の値は AI ではなく読んだ時点の予定の値
        self.assertEqual(n["changes"], [{"field": "open", "before": "17:00", "after": "17:30"},
                                        {"field": "start", "before": "18:00", "after": "18:30"}])
        self.assertEqual(n["target"], {"date": "2026-08-25", "title": row()["title"], "url": row()["url"]})

    def test_same_field_changes_are_joined(self):
        res = result(when="tomorrow", date_text="", evidence="台風の接近に伴い、タイムテーブルの変更がございます。",
                     changes=[{"field": "time_note", "after": "19:55~20:20 出演"},
                              {"field": "time_note", "after": "20:50~21:50 物販"}])
        rows = [row(date="2026-06-03", title="文化放送 presents「idoLove You♫-On Stage QR-vol.1」", open="10:00",
                    start="10:30", venue="Zepp Shinjuku(TOKYO)")]
        n = read(res, rows, text=TIMETABLE, created_at="2026-06-02T10:00:00.000Z")
        self.assertEqual(n["changes"], [{"field": "time_note", "before": "", "after": "19:55~20:20 出演 ／ 20:50~21:50 物販"}])

    def test_already_up_to_date(self):
        self.assertIsNone(read(result(), [row(open="17:30", start="18:30")]))

    def test_rejected(self):
        self.assertIsNone(read(result(type="caution"), [row()]))

    def test_cancel_and_absent_labels(self):
        text = "📢お知らせ📢\n\n8月25日(火)の卒業公演につきまして、城月菜央は体調不良のため欠席となります。"
        n = read(result(type="absent", changes=[], members=["城月菜央"], evidence="城月菜央は体調不良のため欠席となります。"),
                 [row()], text=text)
        self.assertEqual((n["status"], n["label"], n["members"]), ("absent", "城月欠席", ["城月菜央"]))
        text = "📢お知らせ📢\n\n8月25日(火)の卒業公演は延期とさせていただきます。"
        n = read(result(type="postpone", changes=[], evidence="8月25日(火)の卒業公演は延期とさせていただきます。"),
                 [row()], text=text)
        self.assertEqual((n["status"], n["label"]), ("postpone", "延期"))


class ApplyTest(unittest.TestCase):
    def notice(self, **values):
        n = read(result(), [row()])
        n.update(values)
        return n

    def apply(self, rows, *notices):
        with contextlib.redirect_stdout(io.StringIO()):
            fs.apply_x_notices(rows, list(notices))
        return rows

    def test_official_value_still_old(self):
        r = self.apply([row()], self.notice())[0]
        self.assertEqual((r["open"], r["start"], r["title"]), ("17:30", "18:30", row()["title"]))

    def test_official_caught_up_or_changed_later(self):
        self.assertEqual(self.apply([row(open="17:30", start="18:30")], self.notice())[0]["start"], "18:30")
        self.assertEqual(self.apply([row(open="16:30", start="17:30")], self.notice())[0]["start"], "17:30")

    def test_target_is_found_by_url_or_title(self):
        rows = self.apply([row(title="【城月欠席】星谷美来卒業公演〜ずっと恋の病でいてね〜"),
                           row(title="別の公演", url="https://example.invalid/schedule/2")], self.notice())
        self.assertEqual([r["start"] for r in rows], ["18:30", "18:00"])

    def test_ai_cancel_marks_target_only(self):
        n = self.notice(status="cancel", label="中止", changes=[])
        rows = self.apply([row(), row(title="別の公演", url="https://example.invalid/schedule/2")], n)
        self.assertEqual([r["title"] for r in rows], ["【中止】" + row()["title"], "別の公演"])

    def test_target_gone(self):
        rows = self.apply([row(title="別の公演", url="https://example.invalid/schedule/2")], self.notice())
        self.assertEqual(rows[0]["start"], "18:00")


class OutputTest(unittest.TestCase):
    def test_postponed_is_cancelled_in_calendar(self):
        self.assertIn("STATUS:CANCELLED", fs.ics_event(row(title="【延期】卒業公演", uid="u1")))

    def test_tbd_venue_is_decided_not_changed(self):
        base = dict(row(), weekday="土", venue_url="", memo="", uid="u1", done="")
        old = dict(base, venue="東京都内某所（詳細は後日発表）")
        self.assertEqual([k for _, k, _, _ in notify.classify(old, base)], ["会場決定"])

    def test_time_change_is_important(self):
        base = dict(row(), weekday="火", venue_url="", memo="", uid="u1", done="")
        self.assertEqual(notify.classify(base, dict(base, open="17:30", start="18:30"))[0][:2], ("important", "時刻変更"))


if __name__ == "__main__":
    unittest.main()
