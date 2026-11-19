#!/usr/bin/env python3
"""公式 X に新しい投稿があるかを確かめ、あれば「公式 X だけ」の更新を起動する（VPS の takaneko-xcheck.timer から 10 分ごと）。

新しい投稿がなければ何もしない（GitHub Actions も動かさない）。投稿の読み取り・反映は、起動した更新が行う。
確かめるのは、前回の更新で読んだ投稿（~/.local/state/takaneko-fan/x_state.json の since_id）より新しい投稿があるかだけ。
X API のトークンは ~/.config/takaneko-fan/.env の X_BEARER_TOKEN（runner だけが読める権限にする）。

使い方: python3 check_x.py [--dry-run]（--dry-run は起動せずに結果だけ出す）
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
X_API_URL = "https://api.x.com/2"
X_USER_ID = "1518855666177568769"  # @takanenofficial（fetch_schedule.py の X_USER_ID と同じ）
ENV_PATH = os.path.expanduser("~/.config/takaneko-fan/.env")
X_STATE_PATH = os.environ.get("X_STATE_PATH") or os.path.expanduser("~/.local/state/takaneko-fan/x_state.json")
CHECK_STATE_PATH = (os.environ.get("X_CHECK_STATE_PATH")
                    or os.path.expanduser("~/.local/state/takaneko-fan/x_check.json"))
DISPATCH = os.environ.get("DISPATCH_SCRIPT") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "dispatch_update.sh")
REDISPATCH_AFTER = timedelta(minutes=30)  # 起動した更新がまだ読み込んでいなければ、この時間がたってから起動し直す


def load_token(path=ENV_PATH):
    if os.environ.get("X_BEARER_TOKEN"):
        return os.environ["X_BEARER_TOKEN"]
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                key, _, value = line.strip().partition("=")
                if key == "X_BEARER_TOKEN":
                    return value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return ""


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def newest_post_id(token, since_id):
    """since_id より新しい公式アカウントの投稿（リポスト・返信を除く）のうち、いちばん新しいものの ID。なければ空。"""
    query = urllib.parse.urlencode({"since_id": since_id, "max_results": 5, "exclude": "retweets,replies"})
    req = urllib.request.Request(f"{X_API_URL}/users/{X_USER_ID}/tweets?{query}",
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as res:
        body = json.load(res)
    return body.get("meta", {}).get("newest_id", "")


def should_dispatch(newest, check, now):
    """同じ投稿で何度も起動しない。前回起動した投稿と同じなら、REDISPATCH_AFTER たってから起動し直す。"""
    if not newest:
        return False
    if check.get("dispatched") != newest:
        return True
    return now - datetime.fromisoformat(check["at"]) >= REDISPATCH_AFTER


def main(argv=sys.argv[1:], now=None):
    now = now or datetime.now(JST)
    dry_run = "--dry-run" in argv
    since_id = load_json(X_STATE_PATH).get("since_id")
    if not since_id:
        print("前回の更新で読んだ投稿の記録がないため、確かめません（全体の更新を待つ）")
        return 0
    token = load_token()
    if not token:
        print(f"X_BEARER_TOKEN がありません（{ENV_PATH}）")
        return 1
    try:
        newest = newest_post_id(token, since_id)
    except (urllib.error.URLError, OSError, ValueError) as e:  # 回数の上限（429）なども、この回は何もせず次の回に任せる
        print(f"公式 X を確かめられませんでした: {e}")
        return 0
    check = load_json(CHECK_STATE_PATH)
    if not should_dispatch(newest, check, now):
        print("新しい投稿なし" if not newest else f"新しい投稿 {newest} は起動済み（読み込み待ち）")
        return 0
    print(f"新しい投稿あり: {newest}")
    if dry_run:
        return 0
    subprocess.run([DISPATCH, "--x-only"], check=True)
    os.makedirs(os.path.dirname(CHECK_STATE_PATH), exist_ok=True)
    with open(CHECK_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"dispatched": newest, "at": now.isoformat(timespec="seconds")}, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
