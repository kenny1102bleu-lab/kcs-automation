# kcs-automation — 運用ガイド

このリポジトリはKCS合同会社のSNS自動投稿システム（GitHub Actions + Discord Bot on Render）。
**このファイルを読んでから変更すること。** 過去に「エラーメッセージだけを見て場当たり的にパッチを当てる」
運用（Kenji自己修復 = `06_system_monitor.yml`）が積み重なった結果、システムが複雑化した反省から作成した。

## ⚠️ 重要: もう一つの基幹システムが並行して存在する

KCS合同会社には、このリポジトリとは**別に** Google Apps Script製の基幹システム
（`GAS_KCS合同会社_Backend.js`、scriptId: メモリ参照 `reference-kcs-gas-script-id`）があり、
そちらにも独立した `generateHALPost` / `generateSunakkunPost` / `autoPostHAL` /
`autoPostAffiliateAmazon` / `autoPostAffiliateRakuten` 等のX投稿ロジックが存在する。

**現状、どちらが「正」の投稿経路かは明確に整理されていない。** 2026-07-08時点で判明している事実:
- このリポジトリ（Python/GitHub Actions）はDiscordの`!承認`を挟む人間確認ゲート付き
- GAS側は`FULL_AUTO_MODE`次第で完全自動投稿（人間確認なし）の経路も持つ
- 両方が同時に稼働している形跡がある（実際にX上で両方のフォーマットの投稿を確認済み）

**変更を加える前に、その変更がこのリポジトリだけで完結するのか、GAS側にも同様の修正が必要なのかを
必ず確認すること。** 典型例: BOM混入バグ、AI応答漏洩バグは両方に存在し、両方直す必要があった。

## アーキテクチャ概要

```
Discord (社長操作)
  ↕ bot/discord_bot.py (Render常駐)
  ↕ GitHub repository_dispatch
GitHub Actions ワークフロー (.github/workflows/):
  01: 朝礼ブリーフィング
  02: HAL投稿生成 → Discord承認プレビュー
  03: すなくん投稿生成 → Discord承認プレビュー
  04: 日次レポート（完全自律、承認不要）
  05: 承認後X投稿（Discordの!承認から起動）
  06: システム監視・自己修復（Kenji、コード変更を伴う）
  07-09: 動画生成系
```

投稿生成 (`scripts/hal_post.py` / `scripts/sunakun_post.py`) の流れ:
1. Gemini(タクミ/ユキ)が本文生成 → `scripts/common/post_parser.py`でJSON解析
2. マモル(`scripts/common/mamoru.py`, Claude)が**本文の中身だけ**を審査
   （組み立て済み最終文字列を審査させると、fixed_textでの書き直し時に構造が壊れる。実際に事故発生済み）
3. コード側で固定要素（すなくんのPR表記・誘導文言、ハッシュタグ）を組み立てる
   （AIに「PR表記を忘れずに」と指示するだけでは繰り返し脱落したため、コード側で保証する設計にした）
4. `scripts/common/x_limits.py`で実際のX文字数（全角2ユニット換算、上限280）を検証・安全に切り詰め
5. NGパターン監査 (`scripts/common/ng_patterns.py`)
6. Discordへ承認プレビュー送信（`bot/webhook_server.py`経由でBotに登録）
7. 社長が`!承認 <ID>`→ WF-05が実際にXへ投稿

## 🔴 絶対に守ること

### 1. 新しい環境変数・Secretsは必ず`clean_env()`を通す
Windows経由でGitHub Secretsに値を貼り付けるとBOM（`﻿`）やゼロ幅文字が混入することがあり、
素の`os.environ[...]`のまま使うと以下のような分かりにくいエラーで壊れる:
- Discord Webhook: `InvalidSchema`
- Claude/Gemini API: 認証エラー・タイムアウト
- Twitter API: `215 - Bad Authentication data`

`scripts/common/env_clean.py`の`clean_env(name)`を必ず経由すること。今セッションだけで
ANTHROPIC_API_KEY, DISCORD_WEBHOOK_URL(S), GEMINI_API_KEY, Twitter系4つの計7箇所で
この修正漏れが発見された。**新しい認証情報を追加するときは真っ先にこれを疑うこと。**

### 2. コード変更後は`py_compile`だけでなく`pyflakes`も実行する
`py_compile`は構文チェックのみで、`NameError`（importし忘れた変数の使用等）を検知できない。
実際に`twitter_client.py`で`import os`を消し忘れ、本番のX投稿が壊れる事故が発生した。
```
python -m pyflakes scripts/ bot/
```
未定義名（`F821`）が出ないことを確認してからpushする。

### 3. マモル審査は「本文の中身」だけに適用する
組み立て済みの最終文字列（PR表記・誘導文言・ハッシュタグ込み）をマモルの審査対象にすると、
`fixed_text`での書き直し時にそれら固定要素ごと丸ごと上書きされ、マモル(Claude)自身の
相槌（「承知いたしました」等）がそのままXに投稿される事故が起きる。必ず「本文のみ」を審査し、
固定要素の組み立てはその後にコード側で行う。

### 4. 投稿文章にも実際の日付・季節を反映する
`scripts/common/date_context.py`の`current_season_text()`をuser_messageに必ず含める。
画像生成（`nana.py`）だけに季節反映を入れて文章生成に入れ忘れると、「7月なのに春コーデ」の
ような季節外れの投稿が生成される。

### 5. 変更は実際にワークフローを起動して検証する
ローカルのユニットテストだけでなく、`gh workflow run <workflow>.yml`で実際にGitHub Actions上で
実行し、ログとDiscordプレビューを確認すること。ローカルで動いてもGitHub Actionsのデータセンター
IPからだと挙動が変わることがある（例: Amazon商品ページのスクレイピングはローカルでは成功するが
GitHub ActionsのIPからはボット検知されブロックされた実例あり）。

### 6. 危険な操作は必ず事前確認
- 課金が発生する操作（有料API呼び出しの大量実行等）
- 破壊的操作（`git push --force`、Secretsの削除等）
- 実際のX投稿を伴う操作（テスト目的でも`!承認`を代行しない。人間確認ゲートを迂回しない）

## テスト・QA資産

- `scripts/qa_check_ng_patterns.py` — NGパターン検知の回帰テスト
- `scripts/qa_check_x_limits.py` — X文字数・ハッシュタグ数検証の回帰テスト
- 変更のたびに両方を実行し、全件パスすることを確認する

## 既知の未解決課題（2026-07-08時点）

- GAS側`approveHALPost`で「投稿データが見つかりません」が発生する件: `getNextQueuedPost()`という
  別の消費者が同じ`HAL_PENDING_`/`SUNAKUN_PENDING_`ScriptPropertiesキーを取り合っている可能性が高いが、
  `getNextQueuedPost`が実際に何から呼ばれているか（n8n/Make.com等）未確認。
- すなくんの自己リプライが商品ページでなく楽天トップページのURLになっていた事例が報告されている
  （GAS側の`post.link`の扱いを要調査）。
- GAS版とPython版のどちらを「正」とするか、または統合するかの意思決定がされていない。
