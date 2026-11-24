#!/usr/bin/env python3
"""過去の期間の予定を公式サイトから取り、schedule.* に足す（運用で必要なときに手で動かす）。

すでに載っている予定は書き換えない。公式 X は読まない。参加メモは公式の案内から作る（--no-memo で作らない）。
使い方: python3 ops/add_past.py 2026-09-13 2026-09-24 [--dry-run] [--no-memo]   （終了日はこの日を含まない）
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetch_schedule as fs  # noqa: E402

POST_DAYS = 120  # 開始日の何日前までのお知らせ記事を見るか（参加メモの材料と、会場の補完に使う）


def new_rows(rows, prev):
    """まだ載っていない予定だけを返す（同じ日付・同じ情報元の予定は載っているとみなす）。"""
    known = {(r["date"], r["url"]) for r in prev}
    return [r for r in rows if not {(r["date"], r["url"]), (r["date"], r.get("event_url"))} & known]


def collect(start, end):
    """公式カレンダーとお知らせ記事（ルールで読めるもの）から、期間の予定を作る。"""
    events = [ev for ev in fs.fetch_events(start - timedelta(days=1), end)
              if start.isoformat() <= ev["start"][:10] < end.isoformat()]
    rows = fs.build_rows(events)
    posts = fs.fetch_posts(start - timedelta(days=POST_DAYS))
    items = [fs.post_item_to_row(it) for p in posts for it in fs.extract_post_items(p) if start <= it["date"] < end]
    rows = fs.merge_post_rows(rows, sorted(items, key=lambda r: r["date"]))
    fs.fill_tbd_venues(rows, posts)
    fs.link_detail_posts(rows, posts)
    fs.add_media(rows)
    return rows, posts


def add_past_memos(rows, posts, start):
    """過去の予定に参加メモを付ける。普段の memos.json・公式の版の記録は書き換えず、公式 X の投稿も使わない。"""
    saved = fs.X_TEXTS_PATH, fs.OFFICIAL_TEXTS_PATH
    with tempfile.TemporaryDirectory() as tmp:
        fs.X_TEXTS_PATH = os.path.join(tmp, "x_texts.json")
        fs.OFFICIAL_TEXTS_PATH = os.path.join(tmp, "official_texts.json")
        try:
            return fs.add_memos(rows, posts, start, cache_path=os.path.join(tmp, "memos.json"))
        finally:
            fs.X_TEXTS_PATH, fs.OFFICIAL_TEXTS_PATH = saved


def combine(prev, added, today):
    """前回の予定に足す。前回の予定の項目は変えず、足した予定だけに ID を付ける。"""
    rows = fs.sort_rows(prev + added)
    for r in added:
        r["done"] = "済" if r["date"] < today.isoformat() else ""
    fs.assign_uids(rows, prev)
    return rows


def restore_perform_urls(rows, path="perform.json"):
    """出演時間の根拠の URL（CSV にはない）を前回の結果から付け直す。schedule.md のリンクが消えないように。"""
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
    except FileNotFoundError:
        return
    for r in rows:
        entry = saved.get(fs.row_key(r))
        if entry and entry["perform"] == r.get("perform"):
            r["perform_url"], r["perform_posted"] = entry["url"], entry["posted"]


def main(argv=None):
    today = datetime.now(fs.JST).date()
    parser = argparse.ArgumentParser(description="過去の期間の予定を schedule.* に足す")
    parser.add_argument("start", type=date.fromisoformat, help="開始日 (YYYY-MM-DD)")
    parser.add_argument("end", type=date.fromisoformat, help="終了日・この日を含まない (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="足す予定を表示するだけ（AI を呼ばず、書き込まない）")
    parser.add_argument("--no-memo", action="store_true", help="参加メモを作らない")
    args = parser.parse_args(argv)
    if not args.start < args.end <= today:
        raise SystemExit("開始日 < 終了日 <= 今日 にしてください（これからの予定は fetch_schedule.py が作る）")

    prev = fs.load_prev_rows("schedule.csv")
    rows, posts = collect(args.start, args.end)
    added = new_rows(rows, prev)
    for r in added:
        print(f"  + {fs.row_label(r)}  {fs.time_label(r)}  ＠{r['venue'] or '会場未記載'}")
    print(f"足す予定: {len(added)} 件（期間の予定 {len(rows)} 件、記事 {len(posts)} 件を確認）")
    if args.dry_run or not added:
        return
    if not args.no_memo:
        fs.load_env()
        add_past_memos(added, posts, args.start)
    rows = combine(prev, added, today)
    restore_perform_urls(rows)
    fs.write_markdown(rows, "schedule.md")
    fs.write_csv(rows, "schedule.csv")
    fs.write_ics(rows, "schedule.ics")
    print(f"出力: schedule.md, schedule.csv, schedule.ics（合計 {len(rows)} 件）")


if __name__ == "__main__":
    main()
