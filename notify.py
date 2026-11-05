#!/usr/bin/env python3
"""予定の更新を ntfy で通知する（run_update.sh が push の後に実行する）。

2 つのコミットの schedule.csv / deadlines.csv を比べ、これからの予定の追加・内容の変更・削除と、
申込・締切の追加を 1 件の通知にまとめる。「済」が付いただけの変更は通知しない。
トピック名は環境変数か .env の NTFY_TOPIC。未設定なら何も送らない。

使い方: python3 notify.py [--dry-run] [--today YYYY-MM-DD] [前のコミット] [後のコミット]
"""

import argparse
import csv
import io
import json
import os
import re
import subprocess
import urllib.request
from datetime import datetime

from fetch_schedule import JST, load_env, media_prefix

NTFY_URL = "https://ntfy.sh/"
SITE_URL = "https://takaneko-fan.github.io/schedule/"
TITLE = "高嶺のなでしこ 更新"
NOTIFY_IGNORE_COLUMNS = {"done"}  # ここに入れた列だけの変更は通知しない
MAX_LABELS = 3  # 本文に出す予定名の数。残りは「ほか○件」にまとめる
MAX_LABEL_LENGTH = 28


def read_csv(rev, path):
    """コミット時点の CSV を (見出し, 行のリスト) で返す。ファイルがなければ None。"""
    result = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True)
    if result.returncode != 0:
        return None
    reader = csv.DictReader(io.StringIO(result.stdout.decode("utf-8-sig"), newline=""))
    rows = list(reader)
    return reader.fieldnames, rows


def read_pair(old_rev, new_rev, path):
    """前後の行を返す。比べられない（ファイルがない・列が変わった）ときは None。"""
    old, new = read_csv(old_rev, path), read_csv(new_rev, path)
    if old is None or new is None or old[0] != new[0]:
        print(f"{path}: 列が変わったかファイルがないため比較しません")
        return None
    return old[1], new[1]


def content(row):
    """通知の判定に使う列の値。"""
    return tuple(v for k, v in row.items() if k not in NOTIFY_IGNORE_COLUMNS)


def diff_schedule(old_rows, new_rows, today):
    """(追加, 変更, 削除) を返す。ID が変わった予定は追加と削除の両方に出る。"""
    old = {r["uid"]: r for r in old_rows}
    new = {r["uid"]: r for r in new_rows}
    added = [r for uid, r in new.items() if uid not in old and r["date"] >= today]
    updated = [r for uid, r in new.items() if uid in old and content(r) != content(old[uid]) and r["date"] >= today]
    removed = [r for uid, r in old.items() if uid not in new and r["date"] >= today]
    return added, updated, removed


def diff_deadlines(old_rows, new_rows, today):
    """追加された行を返す。ID がないので内容が変わった行も追加になる。
    行が消えるのは古い記事が取得範囲から外れたときもあるので、削除は見ない。"""
    old = {content(r) for r in old_rows}
    return [r for r in new_rows if content(r) not in old and r["date"] >= today]


def short_title(title):
    """Watch でも読めるよう、グループ名・先頭の日付・アルバムの告知部分を省く。"""
    full = " ".join(title.split())  # 予定名に入っている改行や U+2028 を空白にする
    text = re.sub(r"^(高嶺のなでしこ\s*)?(\d{1,2}/\d{1,2}\s*)?", "", full)
    text = text.rsplit(" at ", 1)[1] if " at " in text else re.sub(r"^.*?(リリース|発売)記念", "", text)
    text = text.strip() or full
    return text if len(text) <= MAX_LABEL_LENGTH else text[:MAX_LABEL_LENGTH - 1] + "…"


def month_day(iso_date):
    _, month, day = iso_date.split("-")
    return f"{int(month)}/{int(day)}"


def build_message(added, updated, removed, deadlines):
    """通知の本文を返す。通知するものがなければ None。"""
    counts = [("新規", added), ("変更", updated), ("削除", removed), ("申込", deadlines)]
    summary = "・".join(f"{name} {len(rows)}件" for name, rows in counts if rows)
    if not summary:
        return None
    labels = [f"{month_day(r['date'])} {media_prefix(r)}{short_title(r['title'])}" for r in added + updated]
    labels += [f"{month_day(r['date'])} {media_prefix(r)}{short_title(r['title'])}（削除）" for r in removed]
    labels += [f"{month_day(r['date'])} {r['kind']} {short_title(r['event'])}" for r in deadlines]
    labels = list(dict.fromkeys(labels))  # 同じイベントの部ごとの行を 1 つにする
    shown = labels[:MAX_LABELS]
    if len(labels) > MAX_LABELS:
        shown[-1] += f" ほか{len(labels) - MAX_LABELS}件"
    return "\n".join([summary, *shown])


def send(topic, message):
    body = json.dumps({"topic": topic, "title": TITLE, "message": message, "click": SITE_URL}, ensure_ascii=False)
    request = urllib.request.Request(NTFY_URL, data=body.encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=15):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("old", nargs="?", default="HEAD~1", help="前のコミット（既定: HEAD~1）")
    parser.add_argument("new", nargs="?", default="HEAD", help="後のコミット（既定: HEAD）")
    parser.add_argument("--dry-run", action="store_true", help="送らずに本文を表示する")
    parser.add_argument("--today", default=datetime.now(JST).date().isoformat(), help="これより前の日付を除く（既定: 今日）")
    args = parser.parse_args()

    load_env()
    schedule = read_pair(args.old, args.new, "schedule.csv")
    deadlines = read_pair(args.old, args.new, "deadlines.csv")
    message = build_message(*(diff_schedule(*schedule, args.today) if schedule else ([], [], [])),
                            diff_deadlines(*deadlines, args.today) if deadlines else [])
    if message is None:
        print("通知する変更なし")
    elif args.dry_run:
        print(message)
    elif not os.environ.get("NTFY_TOPIC"):
        print("NTFY_TOPIC が未設定のため通知しません")
    else:
        send(os.environ["NTFY_TOPIC"], message)
        print("通知を送りました")


if __name__ == "__main__":
    main()
