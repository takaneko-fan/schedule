#!/usr/bin/env python3
"""過去の期間の予定に、公式 X の情報を足す（運用で必要なときに手で動かす。X API と AI の料金がかかる）。

期間の公式 X の投稿を読み、ふだんの更新と同じ読み取り（本文・告知画像・中止や変更の告知）で、
公式 X だけの予定を足し、期間の予定の空いている時刻・会場と出演時間を埋める。
期間の外の予定、ふだんの公式 X の記録（x_events.json・x_notices.json・投稿の記録）は変えない。
読んだ投稿は VPS・手元の記録の場所（STATE_DIR、公開しない）に保存し、もう一度動かしても X API を呼ばない。

使い方: python3 ops/add_past_x.py 2026-09-13 2026-09-24 [--dry-run]   （終了日はこの日を含まない）
        --dry-run でも投稿は読む（料金がかかる）。書き込まずに、足す予定と変わる予定を表示する。
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import date, datetime, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_schedule as fs  # noqa: E402
from add_past import combine, restore_perform_urls  # noqa: E402

STATE_DIR = os.path.dirname(fs.X_STATE_PATH)
CHANGED_FIELDS = ("open", "start", "end", "time_note", "title", "members", "venue", "perform")


def tweets_path(start, end):
    return os.path.join(STATE_DIR, f"past_x_{start.isoformat()}_{end.isoformat()}.json")


def load_tweets(start, end, token):
    """期間の投稿。保存済みならそれを使い（X API を呼ばない）、なければ読んで保存する（本文を含むので公開しない）。"""
    path = tweets_path(start, end)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            tweets = json.load(f)
        print(f"保存済みの投稿を使う: {len(tweets)} 件（{path}）")
        return tweets
    if not token:
        raise SystemExit("X_BEARER_TOKEN がありません（.env）")
    period = tuple(datetime.combine(d, time(), fs.JST) for d in (start, end))
    tweets, _ = fs.fetch_x_posts(token, period=period)
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tweets, f, ensure_ascii=False)
    print(f"公式 X の投稿: {len(tweets)} 件を読んだ（{path} に保存）")
    fs.print_x_usage(token)
    return tweets


def read_period(tweets, rows, tmp):
    """ふだんの更新と同じ読み取りを、ふだんの記録を変えずに行う。(予定, 告知) を返す。
    投稿の本文と画像の読み取りは tmp の中の記録に書く（出演時間を読むのに使う）。"""
    saved = fs.X_TEXTS_PATH, fs.X_TEXTS_KEEP_DAYS, fs.fetch_x_posts
    fs.X_TEXTS_PATH = os.path.join(tmp, "x_texts.json")
    fs.X_TEXTS_KEEP_DAYS = 100000  # 過去の投稿を捨てない
    fs.fetch_x_posts = lambda *args, **kwargs: (tweets, None)
    notices = []
    try:
        # recheck_days を渡すと、ふだんの「前回の続き」（x_state.json）を読み書きしない
        events = fs.read_x_posts("（保存済み）", rows, [], notices, recheck_days=1)
    finally:
        fs.X_TEXTS_PATH, fs.X_TEXTS_KEEP_DAYS, fs.fetch_x_posts = saved
    return events, notices


def apply_period(period_rows, events, notices, start, end, tmp):
    """期間の予定に公式 X の予定・告知・出演時間を反映し、(期間の予定, 足した予定) を返す。"""
    x_rows = [fs.x_event_to_row(e) for e in events if start.isoformat() <= e["date"] < end.isoformat()]
    x_rows.sort(key=lambda r: (r["date"], int(r["url"].rsplit("/", 1)[1])))
    merged = fs.merge_post_rows(period_rows, x_rows)
    added = [r for r in merged if not any(r is p for p in period_rows)]
    fs.add_media(added)
    fs.apply_x_notices(merged, [n for n in notices if start.isoformat() <= n.get("date", "") < end.isoformat()])
    saved = fs.X_TEXTS_PATH
    fs.X_TEXTS_PATH = os.path.join(tmp, "x_texts.json")
    try:
        fs.add_perform(merged, start, path=os.path.join(tmp, "perform.json"))  # start より後の予定を「これから」として読む
    finally:
        fs.X_TEXTS_PATH = saved
    return merged, added


def main(argv=None):
    today = datetime.now(fs.JST).date()
    parser = argparse.ArgumentParser(description="過去の期間の予定に公式 X の情報を足す")
    parser.add_argument("start", type=date.fromisoformat, help="開始日 (YYYY-MM-DD)")
    parser.add_argument("end", type=date.fromisoformat, help="終了日・この日を含まない (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="書き込まずに表示する（投稿は読む）")
    args = parser.parse_args(argv)
    if not args.start < args.end <= today:
        raise SystemExit("開始日 < 終了日 <= 今日 にしてください（これからの予定は fetch_schedule.py が作る）")

    fs.load_env()
    prev = fs.load_prev_rows("schedule.csv")
    period = [r for r in prev if args.start.isoformat() <= r["date"] < args.end.isoformat()]
    before = [dict(r) for r in period]
    tweets = load_tweets(args.start, args.end, os.environ.get("X_BEARER_TOKEN"))
    with tempfile.TemporaryDirectory() as tmp:
        events, notices = read_period(tweets, prev, tmp)
        merged, added = apply_period(period, events, notices, args.start, args.end, tmp)

    for old, new in zip(before, period):
        changes = [f"{k}: {old.get(k) or '（空）'} → {new.get(k)}" for k in CHANGED_FIELDS if old.get(k, "") != new.get(k, "")]
        if changes:
            print(f"  ~ {fs.row_label(new)}  " + " ／ ".join(changes))
    for r in added:
        print(f"  + {fs.row_label(r)}  {fs.time_label(r)}  ＠{r['venue'] or '会場未記載'}  {r['url']}")
    changed = sum(1 for old, new in zip(before, period) if any(old.get(k, "") != new.get(k, "") for k in CHANGED_FIELDS))
    print(f"足す予定: {len(added)} 件、変わる予定: {changed} 件（期間の予定 {len(period)} 件、投稿 {len(tweets)} 件）")
    if args.dry_run or not (added or changed):
        return
    rows = combine(prev, added, today)
    restore_perform_urls(rows)
    fs.write_markdown(rows, "schedule.md")
    fs.write_csv(rows, "schedule.csv")
    fs.write_ics(rows, "schedule.ics")
    print(f"出力: schedule.md, schedule.csv, schedule.ics（合計 {len(rows)} 件）")


if __name__ == "__main__":
    main()
