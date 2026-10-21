#!/usr/bin/env bash
# 予定を取り直し、変化があれば schedule.* と deadlines.* をコミットして push する。
# 手動実行・cron のどちらでも使える。引数は fetch_schedule.py にそのまま渡す。
set -euo pipefail

cd "$(dirname "$0")"

git pull --ff-only --quiet
python3 fetch_schedule.py "$@"

git add schedule.md schedule.csv schedule.ics deadlines.md deadlines.csv deadlines.ics
if git diff --cached --quiet; then
    echo "予定に変化なし（コミットしません）"
    exit 0
fi

git commit --quiet -m "feat: $(TZ=Asia/Tokyo date +%F)の登録"
git push --quiet
echo "コミットして push しました: $(git log --oneline -1)"
