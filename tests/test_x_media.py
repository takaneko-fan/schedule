"""公式 X の投稿の番組の種別（x_media）と予定名（x_title）のテスト。

投稿は実際の告知の書き方に合わせて短く作り直したもの（告知文は転載しない）。
実行: python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import no_network  # noqa: E402,F401 — テストから外部に通信しない

import fetch_schedule as fs  # noqa: E402

# 2026-11-12 の放送の告知（ラジオの絵文字で始まるが、テレビの放送）
TV_ON_AIR = ("📻本日オンエア📻\n\n#高嶺のなでしこ 出演\n『 推し寺アイドルまつり2026 』\n\n"
             "メ〜テレにて放送決定\n\n⏰24:57~25:42\n\n当日のライブの様子がオンエア")


class MediaTest(unittest.TestCase):
    def test_tv_station_wins_over_radio_words(self):
        self.assertEqual(fs.x_media("推し寺アイドルまつり2026", TV_ON_AIR, ""), "テレビ")
        self.assertEqual(fs.x_media("番組", "📻 BS10 にて放送", ""), "テレビ")

    def test_radio_and_tv_words(self):
        self.assertEqual(fs.x_media("カリメン", "📻 RKBラジオ『 カリメン 』", ""), "ラジオ")
        self.assertEqual(fs.x_media("番組", "📺 テレビ出演", ""), "テレビ")
        self.assertEqual(fs.x_media("ライブ", "メ〜テレ主催", "Zepp Nagoya"), "")  # 会場のある予定は番組としない


class TitleTest(unittest.TestCase):
    def test_leading_appearance_word_is_removed(self):
        self.assertEqual(fs.x_title(["#高嶺のなでしこ 出演", "『 推し寺アイドルまつり2026 』"]), "推し寺アイドルまつり2026")
        self.assertEqual(fs.x_title(["出演決定 『 MARQUEE Fes. 』"]), "MARQUEE Fes.")

    def test_other_titles_are_kept(self):
        self.assertEqual(fs.x_title(["出演者発表"]), "出演者発表")
        self.assertEqual(fs.x_title(["『 カリメン 』"]), "カリメン")


if __name__ == "__main__":
    unittest.main()
