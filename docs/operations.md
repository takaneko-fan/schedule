# 高嶺のなでしこ 非公式スケジュール 運用

自動更新の仕組み、手元からの作業、障害時の確認をまとめます。機能は [機能仕様書](spec.md)、作りは [設計](design.md) にあります。

## 運用の流れ

### 全体

| 種類 | 誰が | どのブランチに | 頻度 |
| --- | --- | --- | --- |
| 予定データの更新 | 自動（VPS のタイマー → GitHub Actions） | `main` に直接コミット | 1 日 8 回 |
| プログラム・文書の変更 | 手作業 | 作業ごとの短い `feature/…` で作り、`main` にマージ | 必要なとき |
| 公開 | 自動（GitHub Pages） | `main` の全ファイル | `main` に push されるたび |

- 長く使うブランチは `main` だけにする。`develop` などは作らない（定期の更新が `main` に直接コミットするため、別の長いブランチはすぐ遅れてぶつかりやすい。本番と同じ動きを試せる別の環境もない）。
- push はすべて GitHub App の名義で行い、個人のアカウント名を公開の記録に残さない（下記「手元からの push」）。

### プログラム・文書を変えるとき

1. **相談:** 何をなぜ変えるかを決める。必要なら実データ・過去の履歴・公式の発表を調べる。
2. **ブランチ:** `main` を最新にしてから `feature/…` を作る。
3. **実装:** コードを変え、`tests/` にテストを足す。
4. **確認:** テストに加え、実データでの通しの実行や過去のデータでの試算を行う。通知などの外部への送信は `--dry-run` で確かめる。
5. **文書:** [機能仕様書](spec.md)、[設計](design.md)、この文書、[検証記録・経緯](notes.md) を直す。処理の流れが変わるなら [処理の流れの図](flow.html) も直す。
6. **コミット:** 名義（`takaneko-fan`）と、差分に個人の名前・メールアドレスがないことを確かめてからコミットする。
7. **マージ:** `main` を最新にしてから `--no-ff` でマージする（`chore: <ブランチ名> をマージ`）。
8. **push:** `ops/push_as_app.sh` で送る。送る前にテストが自動で実行され、失敗したら送らない。
9. **後片付け:** マージした `feature/…` は手元で消す（`git branch -d`）。マージコミットが残るので履歴は消えない。
10. **確認:** 必要なら、次の定期の更新が正常に動くかを確かめる。

取り消すときは、マージコミットを `git revert -m 1 <マージコミット>` で取り消してから push する。

### バージョン番号

公開したタグは、取得した人の手元に残り、付け直すと同じ番号で中身が違うものになるので、取り消しにくい。次の決まりで付ける。

**1. 付けるとき**

- ユーザーが「バージョンを付けて」と番号まではっきり指示したときだけ付ける。毎回のマージでは付けない。区切りになりそうなとき（利用者に影響する変更を出す前後、まとまった機能がそろったときなど）は、番号の案を示して提案するだけにする。
- `main` にマージした後で、テストがすべて通っているときだけ付ける。

**2. 番号の決め方**

形式は `vMAJOR.MINOR.PATCH`。番号は飛ばさず、1 つずつ上げる。

| 上げる番号 | どんなとき |
| --- | --- |
| MAJOR | CSV の列、照会カレンダーの URL、予定の ID の作り方など、利用者に対応が必要な変更 |
| MINOR | 利用者（Web 版・照会カレンダー・CSV・通知を使う人）から見て大きな機能の追加。新しい情報元、新しい出力やカレンダー、Web 版の大きな機能など |
| PATCH | それ以外。小さな機能の追加（運用者向けの通知や記録、読み取りの改善など）、不具合の修正、判定の調整、文書だけの変更。迷ったら PATCH |

**3. 手順（毎回同じ）**

1. 作業ブランチで、`CHANGELOG.md` に新しいバージョンの節（日付、追加・修正）を足し、[機能仕様書](spec.md) の「対応するバージョン」と改訂履歴を直す。
2. `main` にマージし、テストを実行する（`python3 -m unittest discover -s tests`）。
3. そのマージコミットに、説明付きのタグを付ける: `git tag -a vX.Y.Z -m "vX.Y.Z: 主な変更"`
4. `ops/push_as_app.sh main vX.Y.Z` で、`main` と一緒に App の名義で送る。
5. GitHub にタグが出たことを確かめる（`git ls-remote --tags origin`）。

**4. 付けた後**

- 公開したタグは、消さない・付け直さない。誤りに気づいたら、次の番号で直す（番号を間違えたら次の正しい番号を付ける、内容に誤りがあれば PATCH で直す）。
- 例外は、公開から間もなく、誰も使っていないと言える場合だけ。その場合もユーザーの指示があるときに限る（2026-11-17 に、公開から数分の v1.1.0 を消して v1.0.1 に付け替えたのがこれに当たる）。

**5. 付けないもの**

- 予定データの自動の更新、作業ブランチ、テストが通っていない状態。

ある時点の予定データを作ったバージョンは `git describe --tags <コミット>` で分かる（例: `v1.0.0-2-g7f3c2a1` は v1.0.0 の後の 2 つ目のコミット）。

### 困ったとき

| 症状 | 確認・対応 |
| --- | --- |
| 「予定の更新」が失敗した | Actions の実行ログを見る。安全策で止まった場合、本当に予定が減ったのなら `--force` を付けて実行する（下記「主なオプション」） |
| 「予定の更新」が始まらない（待ちのまま） | Settings → Actions → Runners でランナーの状態を見る。Idle なら GitHub 側の詰まりなので、`ops/dispatch_update.sh` で起動し直す（詰まった実行は取り消せないことがあるが、放っておけば後で動くか消える） |
| ランナーが Offline | 下記「手元からの push」の後の、ランナーの確認を行う |
| healthchecks.io から知らせが来た | 更新が 1 回以上動いていない。上の 2 つを確かめる |
| 通知が届かない | Secrets のトピック名、ntfy の 1 日の上限（下記「通知の送り方」）を確かめる |
| 「会場を取り込めていない可能性」の通知が来た | 通知の記事・投稿で会場を確かめる。会場の書き方が読み取りの決まりにない形なら、決まりを直す（「プログラム・文書を変えるとき」へ）。直すまでの間は、そのままでも次の発表（記事）で取り込める場合がある |
| 予定が間違っている | 公式の告知と見比べ、読み取りの規則か統合の決まりの問題かを調べる（「プログラム・文書を変えるとき」へ） |

### AI の読み取りの評価

公式 X の投稿をルールと AI の両方で読んだ結果は、定期の更新を動かす VPS の `~/.local/state/takaneko-fan/ai_compare.jsonl` にたまる（[設計](design.md)「ルールと AI の比べ合わせ」）。2〜4 週間たまったら、VPS で集計する。

```sh
python3 ops/ai_compare_report.py                    # 全期間
python3 ops/ai_compare_report.py --since 2026-11-17 # この日以降の投稿だけ
```

- 項目ごとの一致率と、食い違い・片方だけの例（投稿の URL 付き）、AI だけが読めた投稿の一覧が出る。
- 例を 1 件ずつ投稿と見比べ、「ルールが正しい／AI が正しい／どちらも誤り」に分ける。結果は [検証記録・経緯](notes.md) に書き、会場を AI でも読むか、ルールを直すかを決める。

### 定期的に見直すこと

- 予定データのコミットに、出力が変わらないものが混ざっていないか
- 文書（機能仕様書・設計・この文書・処理の流れの図）と実装がずれていないか
- AI と X API の利用料（実行ログの概算）
- ルールと AI の読み取りの比べ合わせ（上記「AI の読み取りの評価」）
- 公式の発表の仕方（中止・欠席の告知の書き方など）が変わっていないか

## 更新の流れ（`run_update.sh`）

更新は GitHub Actions（`.github/workflows/update.yml`）が 1 日 8 回 `run_update.sh` を実行して自動で行います。予定に変化があればコミット・ push され、GitHub Pages と照会カレンダーに反映されます。すぐに更新したいときは、App の鍵がある環境で `ops/dispatch_update.sh` を実行します。

1. `git pull --ff-only` で最新にする。
2. `python3 fetch_schedule.py` を実行する（`run_update.sh` の引数はそのまま渡る）。安全策で止まったら、ここで失敗する。
3. `python3 remind.py` で今日の申込を知らせ、`python3 notify.py --warnings` で記載ミスの可能性を知らせる（予定に変化がない回も実行。失敗しても続行）。
4. `schedule.*`・`deadlines.*` の 6 ファイルと `memos.json`・`post_ai.json`・`events/`・`x_events.json`・`x_notices.json` を `git add` する。差分がなければ「予定に変化なし」で終了。
5. `feat: YYYY-MM-DD HH:MMの登録`（日本時間の日時）でコミットし、push する。
6. `python3 notify.py` で更新の内容を通知する。通知に失敗しても `run_update.sh` は失敗にしない。

push されると、GitHub が自動で Pages を更新します（`main` の全ファイルを公開）。

### 自動更新（GitHub Actions・セルフホストランナー）

公式サイトは国外からの REST API・管理画面（`/wp-json/`、`/wp-admin/admin-ajax.php`）へのアクセスを受け付けないため、GitHub のサーバー（主に米国）では取得できません。国内の VPS に置いたセルフホストランナーで実行します。

| 項目 | 内容 |
| --- | --- |
| 実行時刻 | 日本時間 0:17 / 9:17 / 12:17 / 16:17 / 18:17 / 20:17 / 21:17 / 22:17。公式のお知らせ記事の約 8 割が 16〜23 時台に公開され、0〜7 時台は約 2% のため（2026-11 時点の全 612 件）、夕方から夜に寄せている。VPS の systemd タイマーが GitHub App の名義で起動する（下記）。ワークフローの定時実行（`schedule`）は使わない |
| 手動実行 | App の鍵がある環境で `ops/dispatch_update.sh` を実行する。Actions の画面の Run workflow でも動くが、起動者として個人のアカウント名が公開されるので使わない |
| 実行先 | `runs-on: [self-hosted, conoha]`。ConoHa VPS 1GB（大阪、Ubuntu 24.04、Python 3.12）、ランナー名 `conoha-osaka` |
| サーバー | ランナーは専用ユーザー `runner`（sudo なし）のサービスとして常駐。ssh は鍵のみ、ufw で外からは 22 番だけ許可 |
| 権限 | `contents: write`（`GITHUB_TOKEN` で push する） |
| 同時実行 | `concurrency: update` で 1 つずつ実行し、push の衝突を防ぐ |
| API キー | リポジトリの Secrets の `OPENAI_API_KEY` / `GEMINI_API_KEY`。未登録なら既存の参加メモだけを使う |
| X のトークン | Secrets の `X_BEARER_TOKEN`。未登録なら公式 X の確認を飛ばす |
| 通知のトピック | Secrets の `NTFY_TOPIC`。未登録なら通知を送らない |
| リマインドのトピック | Secrets の `NTFY_REMIND_TOPIC`。未登録ならリマインドを送らない |
| 停止の検知 | Secrets の `HC_PING_URL`（healthchecks.io の Ping URL、`takaneko-fan@proton.me` のアカウント）。成功で `HC_PING_URL`、失敗・タイムアウトで `HC_PING_URL/fail` に連絡する。チェックは Cron `17 0,9,12,16,18,20,21,22 * * *`（Asia/Tokyo）、猶予 1 時間で、更新が 1 回抜けると知らせが届く。**VPS のタイマーの時刻を変えたら、この Cron も合わせる** |
| 失敗時 | 安全策の停止や取得エラーで実行が失敗し、GitHub から通知メールが届く。`update.yml` の最後のステップが ntfy にも優先度を上げて通知する（タイムアウトを含む） |
| 公開リポジトリの対策 | 外部からのプルリクエストのワークフロー実行は、毎回承認が必要な設定にしている |

### VPS のタイマーからの起動

GitHub の定時実行（`schedule`）は混雑で数時間遅れたり、実行自体が抜けたりする（2026-10-26〜28 の実績で 1 日 1 回は抜け、最大 5 時間 43 分の遅れ）。また、定時実行の起動者は cron 行を最後に変更したアカウントとして公開される。手動実行（`workflow_dispatch`）は遅れないので、VPS の systemd タイマーから API でワークフローを起動する。

| 項目 | 内容 |
| --- | --- |
| 起動の名義 | 組織の GitHub App `takaneko-fan-updater`（App ID 5513246）。権限は Actions・Contents・Workflows の読み書き（Contents と Workflows は下記の push 用）、インストール先は `schedule` だけ。Actions の画面の起動者は `takaneko-fan-updater[bot]` になり、個人のアカウントを使わない |
| スクリプト | `ops/dispatch_update.sh` → VPS の `/home/runner/bin/dispatch_update.sh`。App の鍵で JWT を作り、インストールのトークン（1 時間で失効）をもらって `update.yml` を起動する |
| 鍵 | `/home/runner/.config/takaneko-fan/app.pem`（`runner` だけが読める 600）。リポジトリには入れない |
| タイマー | `ops/takaneko-dispatch.timer` / `.service` → `/etc/systemd/system/`。`OnCalendar=*-*-* 00,09,12,16,18,20,21,22:17:00 Asia/Tokyo`、`Persistent=true`（止まっていて逃した回は起動後に 1 回実行） |

確認は `systemctl list-timers takaneko-dispatch.timer`（次回の時刻）、`journalctl -u takaneko-dispatch.service`（起動の結果）。鍵を作り直したときは App の Credentials → Key pairs で新しい鍵を作り、VPS の `app.pem` を置き換えてから古い鍵を削除する。VPS が止まっている間は更新されない（再開後に逃した回を 1 回実行する）。

### 手元からの push

個人のアカウントで push すると、公開されるリポジトリのイベント（PushEvent）、Pages のビルドとデプロイの記録に個人のアカウント名が残る。手元からの push は `ops/push_as_app.sh` で App の名義で行う（引数は `git push` と同じ。省略時は今のブランチ）。鍵は手元の `~/.config/takaneko-fan/app.pem`（VPS とは別の鍵）。push の前に `tests/` のテストを実行し、失敗したら push しない（定期の更新は `run_update.sh` の `git push` で送るので、テストでは止まらない）。うっかり `git push` しないよう、手元のリポジトリでは `remote.origin.pushurl` を存在しない場所にしてある（`ops/setup_local.sh` が設定する）。

ランナーが Offline になったときは、VPS で `systemctl status actions.runner.takaneko-fan.conoha-osaka` を確認します。

## 通知の送り方

通知は IPv4 で送ります。ntfy.sh の無料枠（1 日 250 件）は IPv6 では /64 単位で数えられ、VPS と同じネットワークの他の利用者と共有になるためです（経緯は [検証記録・経緯](notes.md)）。

VPS やランナーが止まっていると更新自体が動かないので、失敗の通知も届きません。この場合は、停止の検知（healthchecks.io）が連絡の途絶えを検知して知らせます。

## 主なオプション（`fetch_schedule.py`）

| オプション | 説明 |
| --- | --- |
| `--start YYYY-MM-DD` | 開始日（既定: 今日） |
| `--end YYYY-MM-DD` | 終了日。この日は含まない（既定: 今日から 366 日後） |
| `--no-detail` | 詳細ページを取得しない（速いが時間・会場が空になる） |
| `--post-days N` | 何日前までのお知らせ記事を確認するか（既定: 365） |
| `--md` / `--csv` / `--ics PATH` | 出力先 |
| `--deadlines PATH` | 申込・締切の出力先（拡張子なし、既定: `deadlines`） |
| `--force` | 安全策のチェックを飛ばして出力する |
| `--x-recheck DAYS` | 公式 X を DAYS 日前から読み直す。読み取った予定は `x_events.json` に、本文は VPS の `x_texts.json`（参加メモの 📣 の材料）に保存する（前回の続きの記録は変えない）。定期の更新の環境で動かすときは `ops/dispatch_update.sh DAYS`（GitHub Actions の手動起動の入力 `x_recheck`）で起動する。読んだ件数分の料金がかかる |

安全策（機能仕様書 F-16）で止まったとき、本当に中止・削除された場合や期間を変えた場合は `--force` を付けて実行します。

送らずに本文を確かめるコマンド:

- 更新の通知: `python3 notify.py --dry-run --today 2026-11-02 31b5e7d~1 31b5e7d`
- 記載ミスの可能性: `python3 notify.py --warnings --dry-run`
- たかねこリマインド: `python3 remind.py --dry-run --now 2026-11-15T09:17`

## API キーと App の鍵の置き場所

手元で使う秘密の情報は、リポジトリの外の `~/.config/takaneko-fan/` にまとめて置く（PC ごとに 1 回）。リポジトリは公開なので、ここに置いたものはリポジトリに入れない。

| ファイル | 中身 | 使う場面 | 権限 |
| --- | --- | --- | --- |
| `app.pem` | GitHub App（takaneko-fan-updater）の秘密鍵 | `ops/push_as_app.sh`（push）、`ops/dispatch_update.sh`（更新の起動） | 600 |
| `.env` | `OPENAI_API_KEY`・`GEMINI_API_KEY`・`X_BEARER_TOKEN` | 手元で AI・公式 X を使う通しの実行 | 600 |

- フォルダの権限は 700 にする（本人だけが読める）。`ops/setup_local.sh` は、権限が緩ければ警告する。
- プログラムは、リポジトリに `.env` がなければ `~/.config/takaneko-fan/.env` を直接読む（`load_env`）。開発用コンテナには、このフォルダが読み取り専用でマウントされるので、PC 側でもコンテナの中でも同じファイルが使われ、clone やコンテナの作り直しのたびに作り直さなくてよい。リポジトリに `.env` があればそちらを優先する。
- 置くのは、実際に push や通しの実行をする PC だけにする。テストや文書だけの作業には要らない。
- `~/.config` を dotfiles として公開リポジトリや同期サービスで管理している場合は、`takaneko-fan` を対象から外す。
- 定期の更新（VPS）は、これとは別に GitHub の Secrets と VPS の鍵を使う（上記「自動更新」「VPS のタイマーからの起動」）。

**PC 側での置き方（コンテナの外で行う）:**

```sh
mkdir -p ~/.config/takaneko-fan && chmod 700 ~/.config/takaneko-fan
mv <リポジトリ>/.env ~/.config/takaneko-fan/.env    # すでにリポジトリに .env がある場合
chmod 600 ~/.config/takaneko-fan/.env ~/.config/takaneko-fan/app.pem
bash <リポジトリ>/ops/setup_local.sh                # 権限の確認（コンテナを使う場合は、作成時に自動で実行される）
```

**漏れたかもしれないとき:**

| 対象 | 対応 |
| --- | --- |
| `app.pem` | App の Credentials → Key pairs で新しい鍵を作り、`app.pem` を置き換えてから古い鍵を削除する |
| `OPENAI_API_KEY` / `GEMINI_API_KEY` | 各サービスの画面でキーを無効にして作り直し、`.env` と GitHub の Secrets を差し替える。利用料の画面で不審な利用がないか確かめる |
| `X_BEARER_TOKEN` | X の開発者ポータルでトークンを作り直し、`.env` と Secrets を差し替える。利用料を確かめる |

## コミット名義

コミットは `takaneko-fan <bot@example.invalid>` 名義で行います。GitHub Actions ではワークフロー内で設定します。手元では、このリポジトリの `.git/config` に `user.name` / `user.email` を設定し、個人のグローバル設定より優先させます。設定は `ops/setup_local.sh` が行います（うっかり `git push` しないための `remote.origin.pushurl` の設定も一緒に入れる）。

- 開発用コンテナでは、作成時に自動で実行される（`.devcontainer/devcontainer.json` の `postCreateCommand`）。
- コンテナを使わない環境では、clone の後に `bash ops/setup_local.sh` を 1 回実行する。
- VPS のランナーでは実行しない（`run_update.sh` が `git push` するため）。
