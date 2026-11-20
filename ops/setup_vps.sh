#!/usr/bin/env bash
# VPS に、更新を起動するスクリプトとタイマーを入れる（入れ直す）。root で実行する。
#
# リポジトリの main（REF で変えられる）から次のファイルを取ってきて置き、タイマーを有効にする。
#   ops/dispatch_update.sh・ops/check_x.py     → /home/runner/bin/（runner、755）
#   ops/takaneko-dispatch.*・ops/takaneko-xcheck.* → /etc/systemd/system/
# 何度実行してもよい（dispatch_update.sh や check_x.py を直したときの入れ直しにも使う）。
# 秘密の情報（app.pem・.env）は置かない。あるかどうかと権限だけを確かめる。
#
# 使い方（VPS の root で）:
#   curl -fsSL -o /root/setup_vps.sh https://raw.githubusercontent.com/takaneko-fan/schedule/main/ops/setup_vps.sh
#   bash /root/setup_vps.sh
set -euo pipefail

REF="${REF:-main}"
RAW="https://raw.githubusercontent.com/takaneko-fan/schedule/$REF/ops"
RUNNER_HOME=/home/runner
BIN="$RUNNER_HOME/bin"
CONFIG="$RUNNER_HOME/.config/takaneko-fan"
STATE="$RUNNER_HOME/.local/state/takaneko-fan"
UNITS="takaneko-dispatch.service takaneko-dispatch.timer takaneko-xcheck.service takaneko-xcheck.timer"

if [ "$(id -u)" -ne 0 ]; then
    echo "root で実行してください" >&2
    exit 1
fi
if ! id runner >/dev/null 2>&1; then
    echo "ユーザー runner がいません（docs/vps-setup.md の手順 1 を先に行う）" >&2
    exit 1
fi

# フォルダ（runner の持ち物。秘密の情報と記録は本人だけが読める）
install -d -o runner -g runner -m 700 "$BIN" "$CONFIG"
install -d -o runner -g runner -m 755 "$RUNNER_HOME/.local" "$RUNNER_HOME/.local/state"
install -d -o runner -g runner -m 700 "$STATE"

# スクリプト
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
for f in dispatch_update.sh check_x.py; do
    curl -fsSL -o "$tmp/$f" "$RAW/$f"
    install -o runner -g runner -m 755 "$tmp/$f" "$BIN/$f"
    echo "置いた: $BIN/$f"
done

# タイマー
for u in $UNITS; do
    curl -fsSL -o "$tmp/$u" "$RAW/$u"
    install -o root -g root -m 644 "$tmp/$u" "/etc/systemd/system/$u"
    echo "置いた: /etc/systemd/system/$u"
done
systemctl daemon-reload
systemctl enable --now takaneko-dispatch.timer takaneko-xcheck.timer

# 秘密の情報（値は表示しない）
ok=1
for f in app.pem .env; do
    if [ ! -f "$CONFIG/$f" ]; then
        echo "! $CONFIG/$f がありません（docs/vps-setup.md の手順 3）"
        ok=0
    elif [ "$(stat -c '%U %a' "$CONFIG/$f")" != "runner 600" ]; then
        echo "! $CONFIG/$f の持ち主・権限が runner・600 ではありません: $(stat -c '%U %a' "$CONFIG/$f")"
        ok=0
    fi
done
if [ -f "$CONFIG/.env" ] && ! grep -q '^X_BEARER_TOKEN=.' "$CONFIG/.env"; then
    echo "! $CONFIG/.env に X_BEARER_TOKEN がありません"
    ok=0
fi

echo
systemctl list-timers takaneko-dispatch.timer takaneko-xcheck.timer --no-pager
echo
if [ "$ok" = 1 ]; then
    echo "完了。確かめる: sudo -u runner python3 $BIN/check_x.py --dry-run"
else
    echo "スクリプトとタイマーは入れました。上の ! を直してから、もう一度実行してください"
fi
