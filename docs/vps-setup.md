# VPS の構築手順書

定期の更新を動かす VPS を、一から作り直すための手順書です。上から順に行えば、今の VPS（2026-11-20 に確認）と同じ状態になります。
仕組みの説明は [運用](operations.md) の「自動更新」「VPS のタイマーからの起動」「公式 X の確認（10 分ごと）」にあります。

## 0. 始める前に

### いつ使うか

- VPS が壊れた、契約を変えた、OS を入れ直したなど、VPS を一から作るとき
- `dispatch_update.sh`・`check_x.py`・タイマーを直したときの入れ直しは、[手順 4](#手順-4-スクリプトとタイマーを入れる) だけを行う

### 用意するもの

| もの | 使う手順 | 置き場所・入手先 |
| --- | --- | --- |
| ConoHa のアカウント | 1 | ConoHa の管理画面 |
| ssh の鍵（root でログインする用） | 1 | 手元の PC。VPS を作るときに ConoHa に登録する |
| GitHub の組織 `takaneko-fan` の管理者の権限 | 2 | GitHub |
| GitHub App の鍵 `app.pem` | 3 | 手元の PC の `~/.config/takaneko-fan/app.pem` |
| X API のトークン `X_BEARER_TOKEN` | 3 | 手元の PC の `~/.config/takaneko-fan/.env`（なければ X の開発者ポータル） |

API キー（OpenAI・Gemini）、通知のトピック、停止の検知の URL は GitHub の Secrets にあるので、VPS には置かない。

### かかる時間と費用

- 時間: 1 時間ほど（手順 6 の記録の作り直しの待ち時間を含む）
- 費用: VPS の料金のほか、手順 6 の公式 X の読み直しで X API が 0.7 ドル前後、AI が十数円

### 全体の流れ（チェックリスト）

- [ ] 手順 1. VPS を作り、基本の設定をする
- [ ] 手順 2. GitHub Actions のランナーを入れて登録する
- [ ] 手順 3. 秘密の情報（`app.pem`・`.env`）を置く
- [ ] 手順 4. スクリプトとタイマーを入れる（`ops/setup_vps.sh`）
- [ ] 手順 5. 前の VPS を止める
- [ ] 手順 6. 記録を作り直す
- [ ] 手順 7. 動いたかを確かめる

作業はすべて、VPS に root で ssh して行う（手順 3 の鍵のコピーだけ手元の PC）。

---

## 手順 1. VPS を作り、基本の設定をする

**1-1. ConoHa で VPS を作る**

| 項目 | 選ぶもの |
| --- | --- |
| リージョン | 大阪（公式サイトは国外からのアクセスを受け付けないため、国内にする） |
| プラン | メモリ 1GB |
| OS | Ubuntu 24.04 LTS |
| ssh の鍵 | 手元の PC の公開鍵を登録する |
| ファイアウォール（セキュリティグループ） | 使わない（VPS の ufw で守る） |

**1-2. root で ssh できることを確かめる**

```sh
# 手元の PC で
ssh root@<VPS のアドレス>
```

**1-3. 時刻・パッケージを整える**（VPS の root で）

```sh
timedatectl set-timezone Asia/Tokyo
apt-get update && apt-get install -y git curl openssl python3
```

**1-4. ssh がパスワードでは入れない設定になっているかを確かめる**

ConoHa で ssh の鍵を登録して作ると、最初から「root は鍵だけ、パスワードでのログインは不可」になっている（今の VPS もこの状態で、自分では設定していない）。

```sh
sshd -T | grep -Ei '^(permitrootlogin|passwordauthentication) '
```

次の 2 行が出れば、何もしなくてよい。

```
permitrootlogin without-password
passwordauthentication no
```

違っていたときだけ、次で設定する。1-2 で鍵でログインできたことを確かめてから行う（鍵で入れないまま行うと、VPS に入れなくなる）。ssh の設定は同じ項目なら最初に読まれた値が使われるので、ファイル名を `00-` で始めて、ほかの設定ファイル（`50-cloud-init.conf` など）より先に読まれるようにする。

```sh
printf 'PermitRootLogin prohibit-password\nPasswordAuthentication no\n' > /etc/ssh/sshd_config.d/00-takaneko.conf
sshd -t && systemctl reload ssh
sshd -T | grep -Ei '^(permitrootlogin|passwordauthentication) '   # 上の 2 行になったことを確かめる
```

**1-5. ファイアウォール（ufw）で、入ってくる通信を ssh だけにする**

```sh
ufw allow OpenSSH
ufw --force enable
```

**1-6. 更新を動かすユーザー `runner` を作る**（sudo の権限は付けない）

```sh
adduser --disabled-password --gecos "" runner
```

**確かめる**

```sh
timedatectl | grep "Time zone"                 # Asia/Tokyo
python3 --version                              # Python 3.12.x
sshd -T | grep -Ei '^(permitrootlogin|passwordauthentication) '
                                               # permitrootlogin without-password（prohibit-password と同じ意味）
                                               # passwordauthentication no
ufw status                                     # Status: active、22/tcp (OpenSSH) ALLOW
systemctl is-enabled unattended-upgrades       # enabled（セキュリティ更新が自動で入る）
swapon --show                                  # スワップがある（ConoHa の標準のイメージでは 2GB）
id runner                                      # uid=…(runner)。groups に sudo がないこと
```

スワップがないときは、2GB のスワップを作る（`fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile && echo '/swapfile none swap sw 0 0' >> /etc/fstab`）。

---

## 手順 2. GitHub Actions のランナーを入れて登録する

ランナーは、組織 `takaneko-fan` 単位で登録する。名前は `conoha-osaka`、ラベルは `conoha`（更新のワークフローは、このラベルのランナーでだけ動く）。

**2-1. 前の VPS のランナーを消す**（GitHub の画面）

組織の Settings → Actions → Runners を開く。前の VPS のランナー `conoha-osaka` が残っていれば、`…` → Remove で消す（前の VPS がまだ動いている場合は、手順 5 で止めてから消す）。

**2-2. 新しいランナーの登録の画面を開く**（GitHub の画面）

同じ画面の「New runner」→「New self-hosted runner」→ Linux・x64 を選ぶ。画面に出る 2 つの値を控える。

- ランナーのバージョン（`actions-runner-linux-x64-2.xxx.x.tar.gz` の数字）
- 登録トークン（`./config.sh --url … --token …` の `--token` の後ろ。1 時間で失効する）

**2-3. ランナーを入れて登録する**（VPS で、`runner` として）

```sh
sudo -u runner -i                              # runner になる
mkdir actions-runner && cd actions-runner
curl -o actions-runner.tar.gz -L https://github.com/actions/runner/releases/download/v<バージョン>/actions-runner-linux-x64-<バージョン>.tar.gz
tar xzf actions-runner.tar.gz
./config.sh --url https://github.com/takaneko-fan --token <登録トークン> \
    --name conoha-osaka --labels conoha --work _work --unattended
exit                                           # root に戻る
```

`config.sh` が `Settings Saved.` と出れば登録できている。`libicu` などの部品が見つからないというエラーで止まったときは、root で `/home/runner/actions-runner/bin/installdependencies.sh` を実行してから、もう一度 `config.sh` を実行する（今の VPS では不要だった）。

**2-4. ランナーをサービスとして常駐させる**（VPS の root で）

```sh
cd /home/runner/actions-runner
./svc.sh install runner                        # runner ユーザーで動くサービスとして入れる
./svc.sh start
```

**確かめる**

```sh
systemctl status actions.runner.takaneko-fan.conoha-osaka --no-pager   # active (running)
```

GitHub の組織の Settings → Actions → Runners で、次を確かめる。

- `conoha-osaka` が **Idle**（緑）
- ラベルに `self-hosted`・`Linux`・`X64`・`conoha` がある
- Settings → Actions → Runner groups → Default で、`schedule` リポジトリが使える（公開リポジトリでの使用を許可している）

---

## 手順 3. 秘密の情報（`app.pem`・`.env`）を置く

**3-1. App の鍵を VPS にコピーする**（手元の PC で）

```sh
scp ~/.config/takaneko-fan/app.pem root@<VPS のアドレス>:/root/app.pem
```

**3-2. 置き場所に移し、`runner` だけが読めるようにする**（VPS の root で）

```sh
install -d -o runner -g runner -m 700 /home/runner/.config/takaneko-fan
install -o runner -g runner -m 600 /root/app.pem /home/runner/.config/takaneko-fan/app.pem
rm /root/app.pem
```

**3-3. 公式 X の確認用のトークンを書く**（VPS の root で）

```sh
sudo -u runner sh -c 'umask 077; touch /home/runner/.config/takaneko-fan/.env'
sudo -u runner nano /home/runner/.config/takaneko-fan/.env
```

開いたエディタに次の 1 行を書いて保存する（値は手元の PC の `.env` と同じ。画面に出したり、どこかに貼ったりしない）。

```
X_BEARER_TOKEN=<トークン>
```

**確かめる**

```sh
ls -la /home/runner/.config/takaneko-fan
# drwx------ runner runner … .
# -rw------- runner runner … .env
# -rw------- runner runner … app.pem
```

---

## 手順 4. スクリプトとタイマーを入れる

`ops/setup_vps.sh` が、リポジトリの `main` から次のものを取ってきて置き、タイマーを有効にする。何度実行してもよい。

- `dispatch_update.sh`（更新の起動）・`check_x.py`（公式 X の確認）→ `/home/runner/bin/`
- 全体の更新のタイマー（1 日 8 回）と、公式 X の確認のタイマー（10 分ごと）→ `/etc/systemd/system/`

**4-1. スクリプトを取ってきて実行する**（VPS の root で）

```sh
curl -fsSL -o /root/setup_vps.sh https://raw.githubusercontent.com/takaneko-fan/schedule/main/ops/setup_vps.sh
bash /root/setup_vps.sh
```

**確かめる**

最後に次のように出ればよい。

```
NEXT                          LEFT …  UNIT                     ACTIVATES
… JST                         …       takaneko-xcheck.timer    takaneko-xcheck.service
… JST                         …       takaneko-dispatch.timer  takaneko-dispatch.service

完了。確かめる: sudo -u runner python3 /home/runner/bin/check_x.py --dry-run
```

`!` で始まる行が出たときは、その内容（`app.pem` がない、権限が違う、`X_BEARER_TOKEN` がない）を手順 3 で直してから、`bash /root/setup_vps.sh` をもう一度実行する。

---

## 手順 5. 前の VPS を止める

前の VPS がまだ動いている場合は、新しい VPS と二重に更新を起動しないよう、前の VPS のタイマーとランナーを止める。

```sh
# 前の VPS の root で
systemctl disable --now takaneko-dispatch.timer takaneko-xcheck.timer
systemctl stop actions.runner.takaneko-fan.conoha-osaka
```

その後、手順 2-1 のとおり GitHub の画面で前のランナーを消し、ConoHa で前の VPS を削除する。前の VPS がもう動いていなければ、この手順は飛ばす。

---

## 手順 6. 記録を作り直す

VPS にだけある記録（[運用](operations.md)「VPS にだけある記録」）は、作り直すと消える。次の順で作り直す。

**6-1. 全体の更新を 1 回動かす**（VPS の root で）

```sh
sudo -u runner /home/runner/bin/dispatch_update.sh
```

GitHub の Actions の画面で「予定の更新」が成功するのを待つ（数分）。ログの最後に「公式サイトの内容を保存（公式 X だけの更新で使う）: …」が出ていることを確かめる。

**6-2. 公式 X を 30 日分読み直す**（VPS の root で）

```sh
sudo -u runner /home/runner/bin/dispatch_update.sh 30
```

公式 X の投稿の本文と、告知画像の読み取り（出演時間・画像の一覧の予定・参加メモの材料）が作り直される。1 回で画像を読むのは 10 件までなので、ログの「画像の読み取り: N 件」が 10 件より少なくなるまで、6-2 を繰り返す（1 回ごとに Actions の画面で成功を待つ）。同じ日（UTC。日本時間の 9 時に切り替わる）のうちなら、X API の料金は二重にならない。

**作り直されない記録**（そのままでよい）

| 記録 | 影響 |
| --- | --- |
| ルールと AI の読み取りの比べ合わせ（`ai_compare.jsonl`） | 評価は作り直した日から数え直す |
| 公式の記事・ページの版（`official_texts.json`） | 作り直した日からの版になる（公式と公式 X のどちらが後かの判断が、しばらく粗くなる） |
| 記載ミス・取りこぼしの通知（`warned.json`）、リマインド（`remind_state.json`）を送った記録 | 同じ知らせが、もう一度届くことがある |

---

## 手順 7. 動いたかを確かめる

| # | 確かめること | 方法 | よい状態 |
| --- | --- | --- | --- |
| 1 | ランナー | 組織の Settings → Actions → Runners | `conoha-osaka` が Idle |
| 2 | タイマー | `systemctl list-timers 'takaneko-*'` | 2 つが出る |
| 3 | 全体の更新 | 手順 6 の Actions の実行 | 成功し、ログの最後に「公式サイトの内容を保存」 |
| 4 | 公式 X の確認 | `sudo -u runner python3 /home/runner/bin/check_x.py --dry-run` | 「新しい投稿なし」または「新しい投稿あり」 |
| 5 | 公式 X の確認のタイマー | 20 分後に `journalctl -u takaneko-xcheck.service --since "30 min ago" --no-pager` | 10 分ごとに記録がある |
| 6 | 停止の検知 | healthchecks.io のチェック | 成功（緑） |
| 7 | 公開の結果 | Web 版（GitHub Pages） | 予定が表示される |

すべてよい状態なら、構築は完了。

---

## うまくいかないとき

| 症状 | 確認・対応 |
| --- | --- |
| `config.sh` が `libicu` などの部品のエラーで止まる | root で `/home/runner/actions-runner/bin/installdependencies.sh` を実行し、もう一度 `config.sh` を実行する |
| `config.sh` で登録できない | 登録トークンは 1 時間で失効する。手順 2-2 の画面を開き直して、新しいトークンで実行する。同じ名前のランナーが残っていると言われたら、手順 2-1 で消すか、`config.sh` に `--replace` を足す |
| ランナーが Offline のまま | `systemctl status actions.runner.takaneko-fan.conoha-osaka` を見る。止まっていれば `cd /home/runner/actions-runner && ./svc.sh start` |
| 更新が「待ち」のまま始まらない | ランナーのラベルに `conoha` があるか、Runner groups で `schedule` が使えるかを確かめる |
| 更新が失敗し、公式サイトの取得でエラーになる | VPS が国内（大阪）にあるかを確かめる。公式サイトは国外からのアクセスを受け付けない |
| `setup_vps.sh` が `!` を出す | 出た内容のとおりに手順 3 を直し、もう一度実行する |
| `check_x.py --dry-run` が「X_BEARER_TOKEN がありません」 | 手順 3-3 の `.env` を見直す（`X_BEARER_TOKEN=` で始まる 1 行があるか、`runner`・600 か） |
| `check_x.py --dry-run` が「前回の更新で読んだ投稿の記録がない」 | 手順 6-1 の全体の更新が、まだ成功していない。成功を待ってからもう一度 |
| ssh で入れなくなった | ConoHa の管理画面のコンソールから入り、`/etc/ssh/sshd_config.d/00-takaneko.conf`（1-4 で作った場合）と鍵の登録を見直す |
