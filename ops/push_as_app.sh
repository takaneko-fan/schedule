#!/usr/bin/env bash
# 手元のコミットを GitHub App（takaneko-fan-updater）の名義で push する。
# 個人のアカウントで push すると、公開リポジトリの push の記録や Pages のビルドに個人のアカウント名が残るため、
# このリポジトリへの push は必ずこのスクリプトで行う。
#
# 使い方: ops/push_as_app.sh [git push の引数…]   （省略時は今のブランチを同じ名前で push）
#   例: ops/push_as_app.sh main
#
# 必要なもの: curl, openssl, python3
# 鍵: GitHub App の秘密鍵（.pem）。既定は ~/.config/takaneko-fan/app.pem（本人だけが読める権限にする）
# App の権限: Contents と Workflows の読み書き（.github/workflows/ を変える push に Workflows が要る）
set -euo pipefail

APP_ID="${APP_ID:-5513246}"
KEY="${APP_KEY:-$HOME/.config/takaneko-fan/app.pem}"
REPO="takaneko-fan/schedule"
API="https://api.github.com"

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

# 1. App の鍵で署名した JWT を作る（有効期限 9 分。時計のずれを見込んで 1 分前から有効にする）
now=$(date +%s)
header=$(printf '{"alg":"RS256","typ":"JWT"}' | b64url)
payload=$(printf '{"iat":%d,"exp":%d,"iss":"%s"}' $((now - 60)) $((now + 540)) "$APP_ID" | b64url)
signature=$(printf '%s.%s' "$header" "$payload" | openssl dgst -sha256 -sign "$KEY" -binary | b64url)
jwt="$header.$payload.$signature"

api() {  # api メソッド パス
    curl -fsS -X "$1" "$API$2" -H "Authorization: Bearer $jwt" \
        -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28"
}
json() { python3 -c "import json, sys; print(json.load(sys.stdin)['$1'])"; }

# 2. このリポジトリへのインストールから、1 時間だけ有効なトークンをもらう
installation=$(api GET "/repos/$REPO/installation" | json id)
token=$(api POST "/app/installations/$installation/access_tokens" | json token)

# 3. トークンで push する。個人の認証情報（credential.helper）は使わせず、聞き返しもさせない
[ $# -gt 0 ] || set -- "HEAD:refs/heads/$(git symbolic-ref --short HEAD)"
GIT_TERMINAL_PROMPT=0 git -c credential.helper= push "https://x-access-token:$token@github.com/$REPO.git" "$@"

# URL を直接指定した push では origin/… が進まないので取り直す（公開リポジトリなので認証は要らない）
git fetch --quiet origin
echo "App の名義で push しました"
