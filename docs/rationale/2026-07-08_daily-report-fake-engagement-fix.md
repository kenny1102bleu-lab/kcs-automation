# Rationale: 日次レポート（20:00）の「いいね0・インプレ0」虚偽表示を修正

- **日付:** 2026-07-08
- **対象ファイル:** `GAS_KCS合同会社_Backend.js` の `generateDailyReport()`（20:00トリガー）
- **経緯:** 同日の朝礼修正（[[2026-07-08_morning-briefing-decision-trigger.md]]、`getYesterdaySnsStats()`）作業時に発覚した同種バグの積み残しチケット（`task_53390f3b`）への対応。

## 1. 問題点（変更前）

`generateDailyReport()` は `getAffiliatePosts()`（`'SNS投稿管理'`シート）から取得した投稿を以下のロジックで集計していたが、シートには `タイムスタンプ / プラットフォーム / 内容 / ステータス / スタッフ名` の5列しか存在しない。

1. `postedToday` の絞り込みが `p['投稿日'] || p['投稿時刻']` という**存在しない列**を参照していたため、`d` は常に空文字列 `''` になり、`''.startsWith(...)` は常に `false`。**その日に何件投稿していても `postedToday` は常に0件**になっていた（朝礼の`getHALPosts()`未定義バグとは別系統だが症状は同じ「常に0」）。
2. `totalLikes` / `totalImpress` も**存在しない列**（`いいね数` / `インプレッション`）を参照しており常に0。そもそもX APIのpublic_metrics取得ジョブはシステム全体のどこにも実装されていない（[[project-morning-briefing-redesign]]で確認済み）。

結果、日次レポート（Discord送信・Obsidian/GitHub保存）は「投稿数0件・いいね0・インプレ0」という、クラッシュはしないが実態と異なるフェイク表示を毎日20:00に出し続けていた。

## 2. 変更内容

- `postedToday` の絞り込みを実在列 `タイムスタンプ`（`logSnsPost()`が`'yyyy/MM/dd HH:mm:ss'`形式で書き込む）に修正。
- いいね数・インプレッションの表示を廃止し、`getYesterdaySnsStats()`と同じ考え方で **投稿数・スタッフ別内訳（`byStaff`）・ステータス内訳（`byStatus`：投稿済み/エラー/スキップ等）** に置き換え。
- `contextText`（Claude API入力）・`mdContent`（Obsidian/GitHub保存用Markdown）の両方に「いいね数・インプレッションは計測する仕組みが未整備のため取得不可」と明記し、AIが数値を捏造しないようにした。

## 3. 対応しなかったこと

- エンゲージメント数値（いいね・インプレッション）そのものの取得は、X APIのpublic_metrics取得ジョブを新規実装する必要がある大きな話のため今回は対応していない。社長には一旦保留と回答済み（[[project-morning-briefing-redesign]]参照）。

## 4. 今後の確認事項

- 数日分の20:00日次レポート（Discord/Obsidian）を見て、投稿数・内訳が実際のSNS投稿管理シートの実績と一致しているか確認する。
- `analyzePerformance()`（`01_analytics_tracker.js`）も同じ列不足の影響を受けるが、本番の`morningBriefing()`/`generateDailyReport()`からは呼ばれていない未使用コード（`morningBriefingV2()`からのみ参照、トリガー未設定）のため今回は対象外。

## 5. ロールバック手順

1. `clasp pull`
2. `generateDailyReport()` 内の該当セクションを本パッチ前の内容に戻す
3. `clasp push -f`
