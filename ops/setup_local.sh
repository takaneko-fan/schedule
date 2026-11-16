#!/usr/bin/env bash
# 手元の開発環境のリポジトリに、このリポジトリ用の git の設定を入れる（何回実行しても同じ結果になる）。
# 開発用コンテナでは作成時に自動で実行される（.devcontainer/devcontainer.json の postCreateCommand）。
# コンテナを使わない環境では、clone の後に 1 回だけ実行する: bash ops/setup_local.sh
#
# - コミットの名義を takaneko-fan にする（個人のグローバル設定より優先される）
# - うっかり git push しないよう、push 先を存在しない場所にする（push は ops/push_as_app.sh で行う）
#
# VPS のランナー（定期の更新）では実行しない。run_update.sh が git push するため。
set -euo pipefail

cd "$(dirname "$0")/.."

git config --local user.name "takaneko-fan"
git config --local user.email "bot@example.invalid"
git config --local remote.origin.pushurl "git push は ops/push_as_app.sh で行う"

echo "git の設定を入れました: 名義 $(git config --local user.name) <$(git config --local user.email)>、git push は使えない状態"
