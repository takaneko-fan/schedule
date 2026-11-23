"""notify.py の通知の判定（変更の分類・差分・本文）のテスト。

実行: python3 -m unittest discover -s tests
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import no_network  # noqa: E402,F401 — テストから外部に通信しない

import notify  # noqa: E402
from fetch_schedule import GROUP  # noqa: E402

TODAY = "2026-11-11"


def row(**values):
    """schedule.csv の 1 行。指定しない列は空、日付は今日より後にする。"""
    base = {k: "" for k in ("done", "date", "weekday", "open", "start", "end", "time_note", "title", "category",
                            "members", "venue", "url", "venue_url", "memo", "uid")}
    base.update(date="2026-11-20", title="ミニライブ&特典会", category="ライブ／イベント", members=GROUP,
                venue="ららぽーと立川立飛", url="https://example.invalid/event/1", uid="u1")
    base.update(values)
    return base


def deadline(**values):
    """deadlines.csv の 1 行。"""
    base = {k: "" for k in ("done", "date", "weekday", "time", "kind", "event", "detail", "note", "url", "label",
                            "time_text", "event_short")}
    base.update(date="2026-11-15", time="23:59", kind="受付締切", event="11/20 オンラインサイン会",
                url="https://example.invalid/news/1")
    base.update(values)
    return base


def kinds(old, new):
    return [(level, kind) for level, kind, _, _ in notify.classify(old, new)]


class ClassifyTest(unittest.TestCase):
    def test_no_change(self):
        self.assertEqual(kinds(row(), row()), [])

    def test_memo_or_url_only_is_not_classified(self):
        self.assertEqual(kinds(row(), row(memo="新しいメモ", url="https://example.invalid/event/2")), [])

    def test_date_change_is_important(self):
        changes = notify.classify(row(), row(date="2026-11-21"))
        self.assertEqual(changes, [("important", "日付変更", "11/20", "11/21")])

    def test_time_filled_is_decided(self):
        self.assertEqual(kinds(row(), row(start="13:00")), [("decided", "時刻決定")])

    def test_written_time_changed_is_important(self):
        changes = notify.classify(row(start="13:00"), row(start="14:00"))
        self.assertEqual(changes, [("important", "時刻変更", "13:00", "14:00")])

    def test_written_time_removed_is_important(self):
        changes = notify.classify(row(start="13:00"), row())
        self.assertEqual(changes, [("important", "時刻変更", "13:00", "未定")])

    def test_time_note_change_is_important(self):
        old = row(time_note="1部 12:30 START ／ 2部 16:00 START")
        new = row(time_note="1部 13:00 START ／ 2部 16:00 START")
        self.assertEqual(kinds(old, new), [("important", "時刻変更")])

    def test_venue_filled_is_decided(self):
        self.assertEqual(kinds(row(venue=""), row()), [("decided", "会場決定")])

    def test_venue_detail_added_is_decided(self):
        new = row(venue="ららぽーと立川立飛2F イベント広場")
        self.assertEqual(kinds(row(), new), [("decided", "会場の追記")])

    def test_venue_notation_changed_is_decided(self):
        old = row(venue="Spotify O-EAST(Shibuya O-EAST)")
        new = row(venue="渋谷Spotify O-EAST")
        self.assertEqual(kinds(old, new), [("decided", "会場の表記")])

    def test_other_venue_is_important(self):
        self.assertEqual(kinds(row(), row(venue="イオンモール津田沼")), [("important", "会場変更")])

    def test_venue_removed_is_not_classified(self):
        self.assertEqual(kinds(row(), row(venue="")), [])

    def test_group_to_members_is_decided(self):
        changes = notify.classify(row(), row(members="葉月紗蘭、東山恵里沙"))
        self.assertEqual(changes, [("decided", "出演決定", "", "葉月・東山")])

    def test_members_changed_is_important(self):
        changes = notify.classify(row(members="葉月紗蘭"), row(members="東山恵里沙"))
        self.assertEqual(changes, [("important", "出演変更", "葉月", "東山")])

    def test_several_changes_at_once(self):
        old = row(start="13:00")
        new = row(date="2026-11-21", start="14:00", venue="イオンモール津田沼")
        self.assertEqual(kinds(old, new), [("important", "日付変更"), ("important", "時刻変更"),
                                           ("important", "会場変更")])


class DiffScheduleTest(unittest.TestCase):
    def test_added_updated_removed(self):
        old = [row(uid="a", title="A公演"), row(uid="b", title="B公演")]
        new = [row(uid="a", title="A公演", start="13:00"), row(uid="c", title="C公演", url="https://example.invalid/c")]
        added, updated, removed = notify.diff_schedule(old, new, TODAY)
        self.assertEqual([r["uid"] for r in added], ["c"])
        self.assertEqual([(o["uid"], n["uid"]) for o, n in updated], [("a", "a")])
        self.assertEqual([r["uid"] for r in removed], ["b"])

    def test_done_only_is_not_updated(self):
        _, updated, _ = notify.diff_schedule([row()], [row(done="済")], TODAY)
        self.assertEqual(updated, [])

    def test_past_rows_are_ignored(self):
        old = [row(uid="a", date="2026-11-01"), row(uid="b", date="2026-11-02")]
        new = [row(uid="a", date="2026-11-01", start="13:00"), row(uid="c", date="2026-11-03")]
        self.assertEqual(notify.diff_schedule(old, new, TODAY), ([], [], []))

    def test_today_is_included(self):
        added, _, _ = notify.diff_schedule([], [row(date=TODAY)], TODAY)
        self.assertEqual(len(added), 1)

    def test_date_moved_with_new_uid_is_update(self):
        """日付が変わって ID が変わっても、同じ情報元で予定名が近ければ同じ予定の変更にする。"""
        old = [row(uid="old")]
        new = [row(uid="new", date="2026-11-21")]
        added, updated, removed = notify.diff_schedule(old, new, TODAY)
        self.assertEqual((added, removed), ([], []))
        self.assertEqual([(o["uid"], n["uid"]) for o, n in updated], [("old", "new")])

    def test_same_uid_with_different_title_is_another_event(self):
        """同じ日・同じ記事の予定の並びが変わって ID が入れ替わっても、別の予定の変更にしない。"""
        old = [row(uid="x", title="ラジオ番組", url="https://example.invalid/a", date="2026-11-20")]
        new = [row(uid="x", title="握手会イベント", url="https://example.invalid/b", date="2026-11-22")]
        added, updated, removed = notify.diff_schedule(old, new, TODAY)
        self.assertEqual((len(added), updated, len(removed)), (1, [], 1))


class DiffDeadlinesTest(unittest.TestCase):
    def test_new_and_changed_rows_are_added(self):
        old = [deadline()]
        new = [deadline(), deadline(time="18:00"), deadline(kind="入金締切", date="2026-11-18")]
        self.assertEqual([(r["kind"], r["time"]) for r in notify.diff_deadlines(old, new, TODAY)],
                         [("受付締切", "18:00"), ("入金締切", "23:59")])

    def test_done_only_and_past_rows_are_ignored(self):
        old = [deadline()]
        new = [deadline(done="済"), deadline(date="2026-11-01")]
        self.assertEqual(notify.diff_deadlines(old, new, TODAY), [])


class RecordUpdatesTest(unittest.TestCase):
    def test_items_use_same_levels_as_message(self):
        old, new = row(start="18:00"), row(start="19:00", venue="ららぽーと立川立飛 2F")
        items = notify.update_items([row(uid="u2", title="新しいライブ", start="17:00")], [(old, new)],
                                    [row(uid="u3", title="消えた予定")], [deadline(label="抽選の締切", time_text="23:59まで")])
        self.assertEqual([(i["level"], i["kind"]) for i in items],
                         [("important", "時刻変更"), ("decided", "会場の追記"), ("important", "削除"), ("added", "新規"), ("deadline", "申込")])
        self.assertEqual(items[0]["change"], "18:00 → 19:00")
        self.assertEqual((items[2]["uid"], items[2]["change"]), ("", "一覧から消えました"))
        self.assertEqual((items[4]["title"], items[4]["change"]), ("オンラインサイン会", "抽選の締切 11/15(日) 23:59まで"))

    def test_memo_only_change_is_not_recorded(self):
        self.assertEqual(notify.update_items([], [(row(), row(memo="📍 会場: …"))], [], []), [])

    def test_same_deadline(self):
        a = deadline()
        self.assertTrue(notify.same_deadline(a, deadline(note="書き写しが変わった")))
        self.assertTrue(notify.same_deadline(a, deadline(kind="当落発表")))  # 元が同じで日時が同じ（AI の読み直しで種別が変わった）
        self.assertTrue(notify.same_deadline(deadline(kind="受付開始"), deadline(kind="一般発売", url="https://x.com/1")))
        self.assertFalse(notify.same_deadline(a, deadline(time="18:00")))

    def test_record_keeps_seven_days_and_skips_known_deadlines(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "updates.json")
        known = {"level": "deadline", "kind": "申込", "date": "2026-11-15", "title": "オンラインサイン会", "change": "",
                 "deadline": {k: deadline()[k] for k in ("date", "time", "kind", "event", "url")}}
        with open(path, "w", encoding="utf-8") as f:
            json.dump([{"at": "2026-11-10T09:17", "items": [known]}, {"at": "2026-11-01T09:17", "items": [known]}], f)
        pairs = {"schedule.csv": ([row()], [row(), row(uid="u2", title="新しいライブ")]),
                 "deadlines.csv": ([], [deadline(), deadline(event="12/1 ライブ", url="https://example.invalid/news/2")])}
        with mock.patch.object(notify, "read_pair", side_effect=lambda a, b, p: pairs[p]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(notify.record_updates(TODAY, "2026-11-11T09:17", path), 2)
        with open(path, encoding="utf-8") as f:
            records = json.load(f)
        self.assertEqual([r["at"] for r in records], ["2026-11-11T09:17", "2026-11-10T09:17"])  # 7 日より古い記録を捨てる
        self.assertEqual([i["title"] for i in records[0]["items"]], ["新しいライブ", "ライブ"])  # 記録済みの申込は足さない


class BuildMessageTest(unittest.TestCase):
    def test_nothing_to_notify(self):
        self.assertEqual(notify.build_message([], [], [], []), (None, False))

    def test_important_and_decided_lines(self):
        updated = [(row(start="13:00"), row(start="14:00")), (row(uid="b", venue=""), row(uid="b"))]
        message, important = notify.build_message([], updated, [row(uid="c", title="握手会")], [])
        self.assertTrue(important)
        self.assertEqual(message.splitlines(), [
            "⚠️ 時刻変更 11/20 ミニライブ&特典会 13:00→14:00",
            "⚠️ 削除 11/20 握手会",
            "✅ 会場決定 11/20 ミニライブ&特典会 ららぽーと立川立飛",
        ])

    def test_counts_and_labels(self):
        added = [row(title=f"公演{i}") for i in range(4)]
        message, important = notify.build_message(added, [(row(), row(memo="メモ"))], [], [deadline()])
        self.assertFalse(important)
        self.assertEqual(message.splitlines(), [
            "新規 4件・変更 1件・申込 1件",
            "11/20 公演0",
            "11/20 公演1",
            "11/20 公演2",
            "ほか3件",
        ])

    def test_short_title_and_media_prefix(self):
        r = row(title="高嶺のなでしこ 11/20 1stアルバム発売記念 ミニライブ at ららぽーと立川立飛",
                category="お知らせ記事 / ラジオ")
        self.assertEqual(notify.name(r), "【ラジオ】ららぽーと立川立飛")
        self.assertEqual(notify.short_title("あ" * 40), "あ" * 27 + "…")


def warning(**values):
    """fetch_schedule.py が書き出す記載ミスの可能性の 1 件。"""
    base = {"published": "2026-10-14", "date": "2026-10-12", "time": "23:59", "kind": "入金締切",
            "event": "10/23 ニューアルバム『A World in Our Colors』発売記念オンラインサイン会", "note": "",
            "url": "https://example.invalid/news/1"}
    base.update(values)
    return base


class WarningsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.warnings_path = os.path.join(tmp.name, "deadline_warnings.json")
        self.warned_path = os.path.join(tmp.name, "state", "warned.json")
        for patcher in (mock.patch.object(notify, "WARNINGS_PATH", self.warnings_path),
                        mock.patch.object(notify, "WARNED_PATH", self.warned_path),
                        mock.patch.object(notify, "MISSED_PATH", os.path.join(tmp.name, "missed.json")),
                        mock.patch.dict(os.environ, {"NTFY_TOPIC": "test-topic"})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.send = mock.patch.object(notify, "send").start()
        self.addCleanup(mock.patch.stopall)

    def run_warnings(self, warnings, dry_run=False, today=TODAY):
        with open(self.warnings_path, "w", encoding="utf-8") as f:
            json.dump(warnings, f, ensure_ascii=False)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            notify.notify_warnings(dry_run, today)
        return out.getvalue()

    def test_message(self):
        message = notify.build_warning_message([warning(), warning(kind="当落発表", date="2026-10-13", time="")])
        self.assertEqual(message.splitlines(), [
            "10/23 発売記念オンラインサイン会",
            "・入金締切 10/12(月) 23:59 ← 記事の公開日（10/14）より前",
            "・当落発表 10/13(火) ← 記事の公開日（10/14）より前",
            "公式の記事・受付ページで日付を確かめてください",
        ])

    def test_sent_once_and_new_items_later(self):
        self.run_warnings([warning()])
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(self.send.call_args.args[2:], (notify.WARNINGS_TITLE, "https://example.invalid/news/1"))
        self.run_warnings([warning()])
        self.assertEqual(self.send.call_count, 1)  # 同じ項目は送らない

        other = warning(url="https://example.invalid/news/2", event="11/6 オンラインサイン会")
        self.run_warnings([warning(), other])
        self.assertEqual(self.send.call_count, 2)
        self.assertNotIn("10/23", self.send.call_args.args[1])
        self.assertEqual(self.send.call_args.args[3], "https://example.invalid/news/2")

    def test_several_articles_open_site(self):
        self.run_warnings([warning(), warning(url="https://example.invalid/news/2")])
        self.assertEqual(self.send.call_args.args[3], notify.SITE_URL)

    def test_record_survives_empty_warnings(self):
        """記事の取得に失敗して警告が空になった回のあとも、同じものは送り直さない。"""
        self.run_warnings([warning()])
        self.run_warnings([warning(url="https://example.invalid/news/2")])
        self.run_warnings([warning()])
        self.assertEqual(self.send.call_count, 2)

    def test_old_records_are_dropped(self):
        self.run_warnings([warning()])
        self.run_warnings([warning(url="https://example.invalid/news/2", published="2026-11-20")], today="2026-12-01")
        with open(self.warned_path, encoding="utf-8") as f:
            self.assertEqual(list(json.load(f).values()), ["2026-11-20"])

    def test_nothing_and_dry_run(self):
        self.run_warnings([])
        self.assertIn("[高嶺のなでしこ 記載ミスの可能性]", self.run_warnings([warning()], dry_run=True))
        self.send.assert_not_called()
        self.assertFalse(os.path.exists(self.warned_path))


if __name__ == "__main__":
    unittest.main()
