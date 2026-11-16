#!/usr/bin/env bash
# 手元の開発環境のリポジトリに、このリポジトリ用の設定を入れる（何回実行しても同じ結果になる）。
# 開発用コンテナでは作成時に自動で実行される（.devcontainer/devcontainer.json の postCreateCommand）。
# コンテナを使わない環境では、clone の後に 1 回だけ実行する: bash ops/setup_local.sh
#
# - コミットの名義を takaneko-fan にする（個人のグローバル設定より優先される）
# - うっかり git push しないよう、push 先を存在しない場所にする（push は ops/push_as_app.sh で行う）
# - API キーの .env は ~/.config/takaneko-fan/.env に置く。プログラムはリポジトリに .env がなければそれを直接読む
#   （load_env）。リポジトリに .env が残っていれば案内し、リンクの .env があれば消す
# - ~/.config/takaneko-fan の権限が緩ければ警告する（フォルダは 700、鍵と .env は 600 にする）
#
# VPS のランナー（定期の更新）では実行しない。run_update.sh が git push するため。
set -euo pipefail

cd "$(dirname "$0")/.."
CONFIG_DIR="$HOME/.config/takaneko-fan"
SHOWN="~/.config/takaneko-fan"  # 表示用（コンテナの中でも PC 側と同じ書き方で案内する）

git config --local user.name "takaneko-fan"
git config --local user.email "bot@example.invalid"
git config --local remote.origin.pushurl "git push は ops/push_as_app.sh で行う"
echo "git の設定を入れました: 名義 $(git config --local user.name) <$(git config --local user.email)>、git push は使えない状態"

# .env（プログラムはリポジトリに .env がなければ ~/.config/takaneko-fan/.env を読む）
if [ -L .env ]; then
    rm .env  # リンクは PC とコンテナで指す場所が違って壊れるので使わない
    echo ".env: リポジトリのリンクを消しました（$SHOWN/.env を直接読みます）"
fi
if [ -f .env ]; then
    echo "! .env: リポジトリの中にあります。PC 側の $SHOWN/.env に移すと、clone のたびに作り直さなくて済みます（docs/operations.md「API キーと App の鍵の置き場所」）"
elif [ -f "$CONFIG_DIR/.env" ]; then
    echo ".env: $SHOWN/.env を使います"
else
    echo ".env: ありません（AI と公式 X の読み取りを使わない作業なら不要です）"
fi

# 権限の確認（ほかのユーザーから読めるなら警告する）
perm() { python3 -c 'import os, stat, sys; print(oct(stat.S_IMODE(os.stat(sys.argv[1]).st_mode))[2:])' "$1"; }
check() {  # check パス 期待する権限 表示名
    [ -e "$1" ] || return 0
    local p
    p=$(perm "$1")
    if [ $((8#$p & 8#077)) -ne 0 ]; then
        echo "! 権限: $3 が $p です。ほかのユーザーから読めないよう、PC 側で chmod $2 $3 を実行してください"
    fi
}
check "$CONFIG_DIR" 700 "$SHOWN"
check "$CONFIG_DIR/app.pem" 600 "$SHOWN/app.pem"
check "$CONFIG_DIR/.env" 600 "$SHOWN/.env"
if [ -f .env ]; then
    check "$PWD/.env" 600 ".env"
fi
