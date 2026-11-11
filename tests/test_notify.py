"""notify.py の通知の判定（変更の分類・差分・本文）のテスト。

実行: python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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


if __name__ == "__main__":
    unittest.main()
