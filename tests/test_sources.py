"""項目ごとの元の発表（note_source・write_sources）のテスト。

値を書き換えた場所が、その値の元の発表を覚えているかを確かめる。
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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import no_network  # noqa: E402,F401 — テストから外部に通信しない
import fetch_schedule as fs  # noqa: E402
from fetch_schedule import GROUP  # noqa: E402

OFFICIAL = "https://takanenonadeshiko.jp/events/event/1/"
POST = "https://x.com/takanenofficial/status/200"


def row(**values):
    base = {k: "" for k in ("done", "date", "weekday", "open", "start", "end", "time_note", "title", "category",
                            "members", "venue", "url", "venue_url", "memo", "uid", "perform")}
    base.update(date="2026-11-28", title="TOKYO IDOL WINTER", category="ライブ／イベント", members=GROUP,
                venue="東京都内某所（詳細は後日発表）", url=OFFICIAL, uid="u1")
    base.update(values)
    return base


def quiet(func, *args):
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args)


class NoteSourceTest(unittest.TestCase):
    def test_x_change_notes_changed_field_only(self):
        r = row(start="18:00")
        notice = {"label": "時刻変更", "url": POST, "changes": [{"field": "start", "before": "18:00", "after": "19:00"},
                                                              {"field": "venue", "before": "別の会場", "after": "Zepp"}]}
        quiet(fs.apply_x_change, r, notice)
        self.assertEqual((r["start"], r["sources"]), ("19:00", {"start": POST}))  # 会場は変更前と違うので書き換えない

    def test_cancel_and_absence_note_title_and_members(self):
        r = row(members="城月菜央、籾山ひめり")
        notice = {"date": "2026-11-28", "status": "absent", "label": "城月欠席", "names": ["TOKYO IDOL WINTER"],
                  "members": ["城月菜央"], "substitutes": [], "url": POST, "posted": "2026-11-27T10:00"}
        quiet(fs.apply_x_notices, [r], [notice])
        self.assertEqual(r["sources"], {"title": POST, "members": POST})

    def test_merge_notes_values_taken_from_post(self):
        r = row()
        pr = row(category=fs.X_CATEGORY, url=POST, venue="Zepp Haneda", start="14:20", media="")
        rows = quiet(fs.merge_post_rows, [r], [pr])
        self.assertEqual((len(rows), r["venue"], r["sources"]), (1, "Zepp Haneda", {"venue": POST}))

    def test_write_sources_skips_same_as_url_and_past(self):
        rows = [row(sources={"venue": POST, "start": OFFICIAL}), row(uid="u2", date="2026-11-01", sources={"venue": POST}),
                row(uid="u3")]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "sources.json")
        self.assertEqual(fs.write_sources(rows, date(2026, 11, 24), path), {"u1": {"venue": POST}})
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"u1": {"venue": POST}})


if __name__ == "__main__":
    unittest.main()
