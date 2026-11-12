#!/usr/bin/env bash
# 予定を取り直し、変化があれば schedule.* と deadlines.* をコミットして push する。
# 手動実行・GitHub Actions（.github/workflows/update.yml）のどちらでも使える。引数は fetch_schedule.py にそのまま渡す。
set -euo pipefail

cd "$(dirname "$0")"

git pull --ff-only --quiet
python3 fetch_schedule.py "$@"

# 予定に変化がない回も、今日の申込は知らせる。失敗しても更新は続ける
python3 remind.py || echo "リマインドに失敗しました（更新は続けます）"
# 申込・締切の記載ミスの可能性も、予定の変化に関係なく知らせる（送ったものは送らない）
python3 notify.py --warnings || echo "記載ミスの可能性の通知に失敗しました（更新は続けます）"

git add schedule.md schedule.csv schedule.ics deadlines.md deadlines.csv deadlines.ics memos.json post_ai.json events x_events.json
if git diff --cached --quiet; then
    echo "予定に変化なし（コミットしません）"
    exit 0
fi

git commit --quiet -m "feat: $(TZ=Asia/Tokyo date '+%F %H:%M')の登録"
git push --quiet
echo "コミットして push しました: $(git log --oneline -1)"

# 通知に失敗しても、更新そのものは完了しているので失敗扱いにしない
python3 notify.py || echo "通知に失敗しました（更新は完了しています）"
