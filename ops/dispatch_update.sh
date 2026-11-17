#!/usr/bin/env bash
# GitHub Actions の「予定の更新」（update.yml）を起動する。VPS の systemd タイマーから実行する。
# GitHub の定時実行（schedule）は混雑で数時間遅れたり抜けたりするため、時刻どおりに動かす目的で使う。
# 起動は GitHub App（takaneko-fan-updater）の名義で行い、個人のアカウントを使わない。
#
# 使い方: ops/dispatch_update.sh [公式 X を読み直す日数]（日数を渡すと、その日数分をさかのぼって読み直す。料金がかかる）
#
# 必要なもの: curl, openssl, python3
# 鍵: GitHub App の秘密鍵（.pem）。既定は ~/.config/takaneko-fan/app.pem（本人だけが読める権限にする）
set -euo pipefail

RECHECK="${1:-}"
if [ -n "$RECHECK" ] && ! [[ "$RECHECK" =~ ^[1-9][0-9]?$ ]]; then
    echo "読み直す日数は 1〜99 で指定してください: $RECHECK" >&2
    exit 1
fi

APP_ID="${APP_ID:-5513246}"
KEY="${APP_KEY:-$HOME/.config/takaneko-fan/app.pem}"
REPO="takaneko-fan/schedule"
WORKFLOW="update.yml"
API="https://api.github.com"

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

# 1. App の鍵で署名した JWT を作る（有効期限 9 分。時計のずれを見込んで 1 分前から有効にする）
now=$(date +%s)
header=$(printf '{"alg":"RS256","typ":"JWT"}' | b64url)
payload=$(printf '{"iat":%d,"exp":%d,"iss":"%s"}' $((now - 60)) $((now + 540)) "$APP_ID" | b64url)
signature=$(printf '%s.%s' "$header" "$payload" | openssl dgst -sha256 -sign "$KEY" -binary | b64url)
jwt="$header.$payload.$signature"

api() {  # api メソッド パス 認証 [本文]
    curl -fsS -X "$1" "$API$2" -H "Authorization: Bearer $3" \
        -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28" ${4:+-d "$4"}
}
json() { python3 -c "import json, sys; print(json.load(sys.stdin)['$1'])"; }

# 2. このリポジトリへのインストールから、1 時間だけ有効なトークンをもらう
installation=$(api GET "/repos/$REPO/installation" "$jwt" | json id)
token=$(api POST "/app/installations/$installation/access_tokens" "$jwt" | json token)

# 3. ワークフローを起動する（成功すると 204 で本文なし）
if [ -n "$RECHECK" ]; then
    api POST "/repos/$REPO/actions/workflows/$WORKFLOW/dispatches" "$token" \
        "{\"ref\":\"main\",\"inputs\":{\"x_recheck\":\"$RECHECK\"}}"
    echo "予定の更新を起動しました（公式 X を $RECHECK 日分読み直す）: $(TZ=Asia/Tokyo date '+%F %T')"
else
    api POST "/repos/$REPO/actions/workflows/$WORKFLOW/dispatches" "$token" '{"ref":"main"}'
    echo "予定の更新を起動しました: $(TZ=Asia/Tokyo date '+%F %T')"
fi
