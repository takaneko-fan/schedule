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
from datetime import datetime

from fetch_schedule import GROUP, JST, MEMBERS, load_env, media_prefix, normalize, shares_phrase, time_label

NTFY_URL = "https://ntfy.sh/"
SITE_URL = "https://takaneko-fan.github.io/schedule/"
TITLE = "高嶺のなでしこ 更新"
IMPORTANT_TITLE = "高嶺のなでしこ 予定の変更あり"  # 日時・会場・出演の変更や削除があるとき
IMPORTANT_PRIORITY = 4  # ntfy の優先度（3 が通常）
MAX_IMPORTANT = 5  # 本文に出す重要な変更の数
MAX_DECIDED = 3  # 本文に出す決定・追記の数
TIME_FIELDS = ("open", "start", "end", "time_note")
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
    """(追加, 変更の (前, 後) の組, 削除) を返す。
    ID は日付と情報元から作るので、日付や情報元が変わると削除と追加に見え、同じ記事・同じ日の予定が複数あると
    並びの変化で ID が入れ替わることもある。予定名が違う同じ ID は別の予定とし、削除と追加のうち
    同じ情報元か同じ日付で予定名の近い組を、同じ予定の変更として扱う。"""
    old = {r["uid"]: r for r in old_rows}
    new = {r["uid"]: r for r in new_rows}
    same = {uid for uid in old.keys() & new.keys() if shares_phrase(old[uid]["title"], new[uid]["title"])
            or old[uid]["title"] == new[uid]["title"]}
    added = [r for uid, r in new.items() if uid not in same and r["date"] >= today]
    updated = [(old[uid], r) for uid, r in new.items() if uid in same and content(r) != content(old[uid]) and r["date"] >= today]
    removed = [r for uid, r in old.items() if uid not in same and r["date"] >= today]
    for gone in list(removed):
        moved = next((r for r in added if (r["url"] == gone["url"] or r["date"] == gone["date"])
                      and shares_phrase(r["title"], gone["title"])), None)
        if moved:
            removed.remove(gone)
            added.remove(moved)
            updated.append((gone, moved))
    return added, updated, removed


def member_names(row):
    if row["members"] == GROUP:
        return "全員"
    return "・".join(last for full, last, _ in MEMBERS if full in row["members"]) or row["members"]


def classify(old, new):
    """変更を (区分, 種類, 変更前, 変更後) のリストにする。区分は important（見逃すと困る変更）か
    decided（未定だったものが決まった・表記が詳しくなった）。どちらでもない変更（メモ・URL など）は含めない。"""
    out = []
    if old["date"] != new["date"]:
        out.append(("important", "日付変更", month_day(old["date"]), month_day(new["date"])))
    changed = [f for f in TIME_FIELDS if old[f] != new[f]]
    if changed:
        # すでに書かれていた時刻が変わった・消えたなら変更、空だった欄が埋まっただけなら決定
        important = any(old[f] for f in changed)
        out.append(("important" if important else "decided", "時刻変更" if important else "時刻決定",
                    time_label(old) or "未定", time_label(new) or "未定"))
    a, b = normalize(old["venue"]), normalize(new["venue"])
    if a != b and b:
        if not a:
            out.append(("decided", "会場決定", "", new["venue"]))
        elif a in b or b in a:  # 「ららぽーと立川立飛」→「ららぽーと立川立飛2F イベント広場」のような追記
            out.append(("decided", "会場の追記", old["venue"], new["venue"]))
        elif shares_phrase(old["venue"], new["venue"], 6):  # 「Spotify O-EAST(Shibuya O-EAST)」→「渋谷Spotify O-EAST」
            out.append(("decided", "会場の表記", old["venue"], new["venue"]))
        else:
            out.append(("important", "会場変更", old["venue"], new["venue"]))
    if old["members"] != new["members"]:
        if old["members"] == GROUP:
            out.append(("decided", "出演決定", "", member_names(new)))
        else:
            out.append(("important", "出演変更", member_names(old), member_names(new)))
    return out


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


def name(row):
    return f"{media_prefix(row)}{short_title(row['title'])}"


def clip(text, limit=30):
    """部制の時間などの長い値を、Watch でも読める長さにする。"""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def capped(lines, limit):
    lines = list(dict.fromkeys(lines))
    return lines[:limit] + ([f"ほか{len(lines) - limit}件"] if len(lines) > limit else [])


def build_message(added, updated, removed, deadlines):
    """(通知の本文, 重要な変更があるか) を返す。通知するものがなければ (None, False)。
    重要な変更（日時・会場・出演の変更と削除）と決定・追記は 1 件ずつ書き、それ以外は件数と予定名だけにする。"""
    important, decided, others = [], [], []
    for old, new in updated:
        changes = classify(old, new)
        for level, kind, before, after in changes:
            when = "" if kind == "日付変更" else f"{month_day(new['date'])} "
            if level == "important":
                important.append(f"⚠️ {kind} {when}{name(new)} {clip(before)}→{clip(after)}")
            else:
                decided.append(f"✅ {kind} {when}{name(new)} {clip(after)}")
        if not changes:
            others.append(new)
    important += [f"⚠️ 削除 {month_day(r['date'])} {name(r)}" for r in removed]
    counts = [("新規", added), ("変更", others), ("申込", deadlines)]
    summary = "・".join(f"{label} {len(rows)}件" for label, rows in counts if rows)
    if not (important or decided or summary):
        return None, False
    labels = [f"{month_day(r['date'])} {name(r)}" for r in added + others]
    labels += [f"{month_day(r['date'])} {r['kind']} {short_title(r['event'])}" for r in deadlines]
    lines = capped(important, MAX_IMPORTANT) + capped(decided, MAX_DECIDED)
    if summary:
        lines += [summary, *capped(labels, MAX_LABELS)]
    return "\n".join(lines), bool(important)


def send(topic, message, title=TITLE, click=SITE_URL, priority=None):
    payload = {"topic": topic, "title": title, "message": message, "click": click}
    if priority:
        payload["priority"] = priority
    body = json.dumps(payload, ensure_ascii=False)
    # ntfy.sh は IPv6 を /64 単位で数えるので、同じネットワークの他の利用者と 1 日の上限を共有してしまう。IPv4 で送る
    result = subprocess.run(["curl", "-4", "-sS", "--fail-with-body", "--max-time", "15", "--data-binary", "@-", NTFY_URL],
                            input=body.encode(), capture_output=True)
    if result.returncode != 0:  # 本文に ntfy のエラーコードが入っている。トピック名は含まれない
        raise SystemExit(f"送信に失敗しました: {result.stdout.decode()} {result.stderr.decode()}".strip())


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
    message, important = build_message(*(diff_schedule(*schedule, args.today) if schedule else ([], [], [])),
                                       diff_deadlines(*deadlines, args.today) if deadlines else [])
    title = IMPORTANT_TITLE if important else TITLE
    if message is None:
        print("通知する変更なし")
    elif args.dry_run:
        print(f"[{title}]\n{message}")
    elif not os.environ.get("NTFY_TOPIC"):
        print("NTFY_TOPIC が未設定のため通知しません")
    else:
        send(os.environ["NTFY_TOPIC"], message, title, priority=IMPORTANT_PRIORITY if important else None)
        print("通知を送りました（重要な変更あり）" if important else "通知を送りました")


if __name__ == "__main__":
    main()
