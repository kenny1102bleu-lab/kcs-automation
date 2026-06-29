# Rationale: engagementTick サニタイズ多層化と kcsHealthMonitor dedup

- **日付:** 2026-06-29
- **担当ロール:** ノア（自己修復パッチ）
- **対象ファイル:** `GAS_KCS合同会社_Backend.js`
- **関連トリガー:** `engagementTick`（5分毎）／`kcsHealthMonitor`（1時間毎）
- **マニュアル準拠:** §6.1（設計意図必須）／§6.2（MTTR重視）／§7（異常系プロトコル）

## 1. 現象（マモル検知）

`kcsHealthMonitor` が毎時、同一の汚染ツイート2件を Discord に通報し続けた:

- `sunakun:AI挨拶` — ツイート先頭が「承知しました／了解しました／わかりました」で始まる
- `sunakun:前回投稿の追記前置き` — ツイート本文に「先ほどの投稿への追加コメント」を含む

4時間で同一アラートが4連発（08:48〜11:48）し、Discord エラーログを汚染。

## 2. 設計意図（なぜそう直したか）

### 2.1 一次原因: `engagementTick` のサニタイズ抜け

`engagementTick` は投稿後15-25分のセルフリプライを Claude/Gemini に書かせて `replyToX` へ直送する。
既存コードは `🤖` 行と コードフェンスしか削っておらず、AI の口語前置き（「承知しました」「先ほどの投稿への追加コメント」等）が素通りで X に投稿されていた。

`sanitizePostText` という多層除去関数が既に存在していたが **engagementTick からは未呼び出し** だった（一次投稿パスでのみ使われていた）。

**修正方針:** `engagementTick` の selfReply / crossReply 両方を `sanitizePostText` → `KCS_NG_CONTENT_PATTERNS` 再走査 → ヒット時はスキップ＋warn。サニタイズ済みでもNGが残るパターンは投稿せず、ログに本文先頭60文字を残してデバッグ可能にした。

**なぜ「投稿スキップ」を選び「リトライ」にしなかったか:** リトライは Gemini のレート/コストを浪費し、同じ前置きが連発する可能性が高い。エンゲージメント機能は補助であり、スキップしても主投稿は無事に出る → 副作用最小・MTTRゼロを優先。

### 2.2 二次原因: `kcsHealthMonitor` の dedup 不在

`checkRecentXContent` は直近10ツイートを毎時スキャンし、ヒットしたものを全て alerts として吐く設計。
`runSelfHeal` 内に「X投稿汚染 → 削除権限なしのため通知のみ」と明記されており、自己修復は意図的に noop。
結果、同じ汚染ツイートが「直近10件」に居る限り無限に通知が飛ぶ。

**修正方針:** `KCS_DIRTY_REPORTED_IDS`（直近100件）に tweetId を記録し、既報のものはサイレントスキップ。新規検知のみが alert に進む。

**なぜ TTL を入れず単純な FIFO 100件 にしたか:** 汚染ツイートはアフィリ投稿サイクル（1日3-4本）で自然に「直近10件」から押し出される。実質3日でwindow外。100件あれば充分な余裕。TTLロジックを足すと複雑度が上がるだけで効果差なし。

### 2.3 通知強化

通知本文に `https://x.com/i/web/status/{tweetId}` と本文先頭80文字を含めた。
社長が Discord 通知から **1クリックで対象ツイートを開いて削除判断** できるようにすることで、運用 MTTR（検知→対応）を分単位に短縮。

## 3. ロールバック手順

1. `clasp pull` で最新版取得
2. `git diff` で本変更を確認
3. 戻す場合は以下3箇所を reverse:
   - `engagementTick` 内 selfReply/crossReply のサニタイズ＋NG再走査ブロック
   - `checkRecentXContent` の dedup ロジック
   - `kcsHealthMonitor` の issue メッセージ整形（URL付与）
4. `clasp push -f`

副作用なし（既存呼び出しの追加引数なし、新規ScriptProperty `KCS_DIRTY_REPORTED_IDS` を残しても無害）。

## 4. 検証

- 12:48 の自動実行で、現存の汚染ツイート2件を「新規」扱いでURL付き通報1回 → 同時に既報マーク
- 13:48以降は既報扱いで沈黙
- 新たな AI 前置きが selfReply で生成されても投稿前にブロック＋スキップログ

## 5. 残課題（次回以降）

- `autoReplyTick` の `replyDraft` も無サニタイズ。承認手動なので緊急度は低いが、同じガードを入れるべき
- カスタムスタッフ別の応答にも `sanitizePostText` を後段で必ず通す方針へ統一すべき（次パッチで `cmdAskGemini` 出力にも適用予定）
