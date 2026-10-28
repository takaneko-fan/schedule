---
name: commit
description: このリポジトリの変更をルールに沿ってコミットする（プッシュはしない）。ユーザーが「コミットして」「コミット」と言ったとき、または /commit で使う。
---

# コミット（takaneko-fan/schedule）

このリポジトリの変更を、下のルールに沿って **コミットだけ** する。プッシュはしない（ユーザーが別に「プッシュして」と言ったときだけ行う）。プッシュは必ず `ops/push_as_app.sh`（GitHub App の名義）で行い、`git push` は使わない（個人のアカウント名が公開の記録に残るため）。

## 1. 名義の確認（身元を守る）

- `git config user.name` が `takaneko-fan`、`git config user.email` が `bot@example.invalid` であることを確認する。違っていたらコミットせず、ユーザーに報告する。
- コミットするファイルの差分とメッセージに、個人の名前・メールアドレスが含まれていないか確認する。個人の値は PC 全体の git 設定（`git config --global user.name` / `user.email`）にあるので、それを grep の検索語に使い、値そのものは表示しない:

  ```sh
  for v in "$(git config --global user.name)" "$(git config --global user.email)"; do
    [ -n "$v" ] && git diff --cached | grep -qiF -- "$v" && echo "個人情報が含まれています"
  done
  ```

  見つかったら中止して報告する（どのファイルかだけ伝え、値は表示しない）。個人の名前やアカウント名をこのファイルを含めリポジトリ内に書かない。

## 2. 追加するファイル

- まず `git status --short` で変更を確認する。
- `git add -A` / `git add .` は使わず、ファイル名を指定して追加する。
- 次はコミットしない: `.env`（API キー）、`__pycache__/`、作業用の一時ファイル。
- 追加後に `git diff --cached --name-only` を確認し、`.env` が含まれていたら中止する。

## 3. コミット前のチェック

- `fetch_schedule.py` が変わっていれば `python3 -m py_compile fetch_schedule.py` で文法エラーがないことを確認する。
- 変更が何もなければコミットせず「変更なし」と報告する。

## 4. コミットメッセージ

形式は `種類: 日本語の要約`。最後に空行をはさんで次の行を付ける。

```
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

| 種類 | 使う場面 | 例 |
|---|---|---|
| `feat` | 機能の追加。予定データだけの更新もこれ | `feat: 2026-10-25の登録` ／ `feat: 参加メモを追加` |
| `fix` | 不具合の修正 | `fix: 会場が未定の予定に後から出た記事の会場名を補う` |
| `docs` | README・仕様書だけの変更 | `docs: 仕様書に参加メモの詳細を追記` |
| `chore` | 設定・不要ファイルの削除など | `chore: 使っていないファイルを削除` |
| `revert` | 変更の取り消し | `revert: GitHub Actionsによる自動更新を取りやめ` |

- 予定データだけ（`schedule.*`、`deadlines.*`、`memos.json`）が変わった場合は `feat: YYYY-MM-DDの登録`（日付は日本時間の今日。`TZ=Asia/Tokyo date +%F`）。`run_update.sh` と同じ形。
- プログラムと、それで作り直した予定データが一緒に変わった場合は 1 つのコミットにまとめ、プログラムの変更に合った種類にする。
- 変更が大きいときは、本文に何をなぜ変えたかを数行で書く。
- メッセージは HEREDOC で渡す（`git commit -F - <<'EOF' … EOF`）。

## 5. 報告

コミットのハッシュ・作者・メッセージ・含めたファイルを日本語で報告する（`git log --format='%h %an <%ae> %s' -1`、`git show --stat --format= HEAD`）。プッシュはしていないこと、`git status -sb` で origin より何コミット進んでいるかも伝える。
