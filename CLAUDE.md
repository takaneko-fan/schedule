# このリポジトリで作業するときの決まり

高嶺のなでしこの非公式スケジュール（takaneko-fan/schedule）。どの環境で作業するときも、次の決まりに従う。詳しい運用は [docs/operations.md](docs/operations.md) の「運用の流れ」にある。

## 言葉

- ユーザーへの返答・報告・進捗は、必ず日本語で書く。コミットメッセージ・文書・コードのコメントも日本語。

## 開発の流れ

- 新しい機能を考えるときは、[構想書](docs/concept.md) の目的と基本の考え方に合っているかを確かめる。
- 作業を始めるときは [保留中の作業](docs/backlog.md) を確認する。保留にした作業はそこに足し、終わった項目は消す（経緯は検証記録に残す）。
- 長く使うブランチは `main` だけ。`develop` などは作らない。予定データの自動の更新（1 日 8 回）が `main` に直接コミットする。
- プログラム・文書の変更は、`main` を最新にしてから作業ごとの `feature/…` を作って行い、`main` に `--no-ff` でマージする（`chore: <ブランチ名> をマージ`）。マージした `feature/…` は手元で消す。
- 変更にはテスト（`tests/`、`python3 -m unittest discover -s tests`）を足し（テストは外部に通信しない。新しいテストファイルも最初に `tests/no_network.py` を import する）、実データでの通しの実行や過去のデータでの試算で確かめる。通知などの外部への送信は `--dry-run` で確かめる。
- 機能・処理の流れを変えたら、[機能仕様書](docs/spec.md)・[設計](docs/design.md)・[運用](docs/operations.md)・[検証記録](docs/notes.md) と [処理の流れの図](docs/flow.html) も直す。
- コミット・マージ・push は、ユーザーが指示したときだけ行う。コミットは `/commit`（`.claude/skills/commit/SKILL.md`）の手順に従う。`/ship` または「反映して」は、コミット・`main` へのマージ・push をまとめて指示したものとして扱い、`.claude/skills/ship/SKILL.md` の手順で行う（タグは付けない）。
- push は必ず `ops/push_as_app.sh`（GitHub App の名義）で行い、`git push` は使わない。個人のアカウント名を公開の記録に残さないため。push の前にテストが自動で実行される。
- コミットの名義は `takaneko-fan <bot@example.invalid>`。差分やメッセージに個人の名前・メールアドレスを入れない。名義が設定されていない環境（`git config --local user.name` が `takaneko-fan` でない）では、最初に `bash ops/setup_local.sh` を実行する（開発用コンテナでは作成時に自動で実行される）。
- バージョン番号（`vMAJOR.MINOR.PATCH` の git のタグ）は、ユーザーが番号まではっきり指示したときだけ、`main` にマージしてテストが通った後に付ける。毎回のマージでは付けず、区切りのときは番号の案を示して提案するだけ。番号の決め方と手順は docs/operations.md の「バージョン番号」。
- 公開したタグは消さない・付け直さない。誤りは次の番号で直す。

## 注意

- 費用のかかる操作（X API の取得、AI の大量の呼び出し）や外部への送信は、事前にユーザーに確認する。
- `.env`（API キー）はコミットしない。`.env` と App の鍵（`app.pem`）は PC の `~/.config/takaneko-fan/` に置く。リポジトリに `.env` がなければ、プログラムはそこの `.env` を直接読む（docs/operations.md「API キーと App の鍵の置き場所」）。鍵やキーの値を表示・出力しない。
- 投稿・記事の本文はリポジトリに保存しない。
- このリポジトリは公開で、GitHub Pages は `main` の全ファイルを公開する。秘密の情報を書かない。
