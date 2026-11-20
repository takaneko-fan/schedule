---
name: ship
description: 作業ブランチの変更をコミットし、main にマージして、GitHub App の名義で push する（コミット・マージ・push をまとめて行う。タグは付けない）。ユーザーが /ship と打ったとき、または「反映して」「コミットしてマージしてプッシュして」と言ったときに使う。
---

# 反映（takaneko-fan/schedule）

作業ブランチ（`feature/…`）の変更を、**コミット → `main` へのマージ → push** までまとめて行う。`/ship` や「反映して」は、ユーザーがこの 3 つをまとめて指示したものとして扱う。

- バージョンのタグは付けない（タグはユーザーが番号まで指示したときだけ。docs/operations.md の「バージョン番号」）。
- push は必ず `ops/push_as_app.sh`（GitHub App の名義）で行い、`git push` は使わない。
- 途中で何か一つでも引っかかったら、そこで止めて報告する（無理に続けない）。

## 1. 始める前の確認

- `git status -sb` で、今いるブランチを確認する。`feature/…` でなければ（`main` にいるなど）止めて報告する。
- コミットする変更がなく、`main` にマージしていないコミットもなければ「反映するものなし」と報告して終わる。

## 2. コミット

`/commit`（`.claude/skills/commit/SKILL.md`）の手順のとおりに行う。

- 名義（`takaneko-fan <bot@example.invalid>`）の確認、差分に個人の名前・メールアドレスがないことの確認（値は表示しない）
- ファイル名を指定して追加する（`git add -A` は使わない。`.env`・`__pycache__/`・一時ファイルは入れない）
- コミットメッセージは `種類: 日本語の要約` と本文、最後に `Co-Authored-By` の行

コミットしていない変更がなければ、この段は飛ばす（ブランチのコミットだけをマージする）。

## 3. main へのマージ

```sh
BRANCH=$(git rev-parse --abbrev-ref HEAD)
git checkout -q main
git pull -q --ff-only                       # 予定データの自動の更新を取り込む
git merge --no-ff -q "$BRANCH" -m "chore: $BRANCH をマージ" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- マージがぶつかったら止めて報告する（`git merge --abort` で元に戻す）。

## 4. push

```sh
ops/push_as_app.sh main                     # push の前にテストが自動で実行される
```

- テストが失敗して push されなかったら、止めて報告する（マージは手元に残っている）。

## 5. 後片付けと報告

```sh
git branch -d "$BRANCH"                     # マージしたブランチを手元で消す
git log --format='%h %an <%ae> %s' -3
git status -sb | head -1                    # main...origin/main（差がないこと）
```

日本語で、次を表にして報告する。

- コミット: ハッシュ・メッセージ・名義（個人情報が入っていないこと）
- マージ: `main` を最新にしてから `--no-ff` でマージしたこと、マージコミットのハッシュ
- テスト: push の前に通ったこと（件数）
- push: `ops/push_as_app.sh main` で App の名義で送ったこと。作業ブランチを消したこと
- タグは付けていないこと
