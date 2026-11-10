#!/usr/bin/env python3
"""今日締切の申込と、今日始まる先着販売を ntfy で知らせる「たかねこリマインド」（run_update.sh が毎回実行する）。

日本時間 REMIND_HOUR 時以降の最初の実行で、deadlines.csv の今日の分をまとめて送る。同じ日にあとから増えた分は、
次の実行でその分だけ送る（送った項目を REMIND_STATE_PATH に記録し、二重に送らない）。
トピック名は環境変数か .env の NTFY_REMIND_TOPIC。未設定なら何も送らない。

使い方: python3 remind.py [--dry-run] [--now YYYY-MM-DDTHH:MM]
"""

import argparse
import csv
import json
import os
import re
from datetime import datetime

from fetch_schedule import JST, load_env, time_key
from notify import SITE_URL, send

TITLE = "たかねこリマインド"
REMIND_HOUR = 9  # この時刻（日本時間）より前は送らない（0:17 の更新で深夜に鳴らさないため）
REMIND_KINDS = ("受付締切", "入金締切")
MAX_ITEMS = 4  # これより多い日は「ほか○件」にする（Watch で読める長さにするため）
REMIND_STATE_PATH = os.environ.get("REMIND_STATE_PATH") or os.path.expanduser("~/.local/state/takaneko-fan/remind_state.json")


def is_target(row):
    """知らせる項目か。締切と、先着販売の受付開始（開始直後に売り切れることがあるため）。"""
    return row["kind"] in REMIND_KINDS or row["kind"] == "受付開始" and "先着" in row["detail"]


def item_key(row):
    return "|".join(row[k] for k in ("date", "time", "kind", "event", "detail"))


def event_name(event):
    """「11/20 ニューアルバム『…』発売記念オンラインサイン会」→「11/20 発売記念オンラインサイン会」。
    アルバム名などの共通部分だけを外し、何のイベントか分かる部分と会場は残す。"""
    text = re.sub(r"^(\d{1,2}/\d{1,2})?\s*(高嶺のなでしこ\s*)?(ニューアルバム\s*)?(『[^』]*』\s*)?", r"\1 ", event).strip()
    text = re.sub(r"\s+at\s+(.+)$", r"（\1）", text)  # 「… at 東京都内某所」→「…（東京都内某所）」
    return text.strip("「」") or event


def what(row):
    """何の期限か。「入金締切」「先着販売の締切」「受付締切（オフィシャル先行受付）」など。"""
    detail = row["detail"].strip("『』")
    if row["kind"] == "受付開始":
        return "先着販売の開始"
    if "先着" in detail:
        return "先着販売の締切"
    if row["kind"] == "入金締切" or detail in ("支払期日", "販売期間"):
        return row["kind"]
    if "抽選" in detail:
        return "抽選の締切"
    if "先行" in detail:
        return f"{detail}の締切"  # 「オフィシャル先行受付の締切」など、どの先行か分かるように
    if "応募" in detail:
        return "応募の締切"
    return f"{row['kind']}（{detail}）"


def when(row):
    """時刻。記事に時刻がなければ「各部開始10分後まで」のような締切の表記を使う。"""
    if row["time"]:
        return row["time"] + ("〜" if row["kind"] == "受付開始" else "まで")
    m = re.search(r"[~〜～]\s*([^~〜～]+)$", row["note"])
    tail = re.sub(r"^.*?\d{1,2}日\s*[(（][^)）]*[)）]\s*", "", m.group(1)).strip() if m else ""
    return tail or "時刻の記載なし"


def build_message(rows, day):
    """1 件を 2 行（イベント名と、何の期限か・時刻）で書く。"""
    items = list(dict.fromkeys((event_name(r["event"]), f"{what(r)} {when(r)}")
                               for r in sorted(rows, key=lambda r: (time_key(r["time"]), r["event"]))))
    lines = [f"📅 今日（{int(day[5:7])}/{int(day[8:])}）の申込"]
    for event, deadline in items[:MAX_ITEMS]:
        lines += [f"▶ {event}", f"　{deadline}"]
    if len(items) > MAX_ITEMS:
        lines.append(f"ほか {len(items) - MAX_ITEMS} 件は Web 版で確認してください")
    return "\n".join(lines)


def click_url(rows):
    """タップで開くページ。すべて同じ記事ならその記事、そうでなければ Web 版。"""
    urls = {r["url"] for r in rows}
    return urls.pop() if len(urls) == 1 and all(urls) else SITE_URL


def load_state():
    try:
        with open(REMIND_STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="送らずに本文を表示する（記録も変えない）")
    parser.add_argument("--now", help="現在時刻の代わり（日本時間 YYYY-MM-DDTHH:MM、確認用）")
    args = parser.parse_args()

    now = datetime.fromisoformat(args.now).replace(tzinfo=JST) if args.now else datetime.now(JST)
    day = now.date().isoformat()
    if now.hour < REMIND_HOUR:
        print(f"リマインド: {REMIND_HOUR} 時より前のため送りません")
        return
    load_env()
    with open("deadlines.csv", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if r["date"] == day and is_target(r)]
    state = load_state()
    sent = set(state.get("sent", [])) if state.get("date") == day else set()
    new = [r for r in rows if item_key(r) not in sent]
    if not new:
        print("リマインド: 今日の分はありません（送信済みを含む）")
        return
    message = build_message(new, day)
    if args.dry_run:
        print(message)
        return
    topic = os.environ.get("NTFY_REMIND_TOPIC")
    if not topic:
        print("リマインド: NTFY_REMIND_TOPIC が未設定のため送りません")
        return
    send(topic, message, TITLE, click_url(new))
    os.makedirs(os.path.dirname(REMIND_STATE_PATH), exist_ok=True)
    with open(REMIND_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"date": day, "sent": sorted(sent | {item_key(r) for r in new})}, f, ensure_ascii=False)
    print(f"リマインドを送りました（{len(new)} 件）")


if __name__ == "__main__":
    main()
