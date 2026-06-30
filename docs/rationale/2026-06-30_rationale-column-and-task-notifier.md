# Rationale: チャットログ Rationale 専用列 + 実務タスク Discord Notifier

- **日付:** 2026-06-30（第3次パッチ）
- **担当ロール:** ノア（自己修復パッチ）＋ ケンジ（実装）＋ サクラ秘書（進行管理）
- **対象ファイル:** `GAS_KCS合同会社_Backend.js`
- **関連関数:** `cmdAskGemini`, `logChatMessage`, `extractRationale`, `setupChatLogSchema`, `setupKCS`, `agencyTaskNotifier`, `setupAgencyTaskNotifierTrigger`, `setupMonitoringTriggers`, `KCS_REQUIRED_TRIGGERS`
- **マニュアル準拠:** §6.1（説明責任）／§6.2（MTTR）／§7（異常系プロトコル）

## 1. 変更2点の意思決定

### 1.1 チャットログに Rationale 専用列を分離（7列目）

**Before:** `cmdAskGemini` は `opts.rationale=true` のとき末尾に `[Rationale: ...]` を含む文字列を返し、`logChatMessage` の `response` 列にそのまま記録していた。

**問題:** Rationale だけ抽出して傾向分析やフィルタしたいときに、6列目の本文と混在しておりスプレッドシート関数（QUERY/FILTER）で扱いにくい。サクラ秘書の朝礼集計でも分離した方が便利。

**修正:**
1. `setupKCS` の `logH` に `'Rationale'` を追加（7列目）
2. `extractRationale(text)` 新規ヘルパー — 末尾の `[Rationale: ...]` を切り出して `{response, rationale}` で返す。マッチしなければ rationale は空文字（既存挙動互換）
3. `cmdAskGemini` の opts.staffName 経路で `extractRationale` を呼び、`logChatMessage({..., rationale: split.rationale})` に分離して渡す
4. Discord 返信本文は **末尾 Rationale を残したまま** 表示する（人間が見たときの説明責任を維持）
5. 既存6列シート用に `setupChatLogSchema()` 冪等migratorも追加

**なぜ Discord 表示から Rationale を消さなかったか:** §6.1 の趣旨は「人間可読なドキュメントの強制生成」。Rationale を Discord 上でも見える状態にしておくことで、社長が応答を流し読みしただけで判断根拠まで把握できる。スプレッドシート集計と Discord 即時可読性の両立。

### 1.2 実務タスク管理 Discord Notifier（10分毎）

**Before:** `_createAgencyTask` でタスクが「待機中」のままシートに溜まるだけで、誰も気づかない。前パッチで Discord 応答末尾に taskId は出すようにしたが、その応答を流したらタスクは埋もれる。

**今回の対応:**
1. `agencyTaskNotifier()` 新設 — 10分毎トリガー実行
   - 「待機中」かつ未通知の task を最大10件バッチで Discord `KCS本部` チャンネルに告知
   - 通知済 ID は `AGENCY_NOTIFIED_IDS` ScriptProperty（直近200件rolling）で dedup
   - メッセージ末尾に現在の `FULL_AUTO_MODE` 状態（自動実行/承認待ち）を必ず併記 → §6.3 Human-in-the-loop 状態の常時可視化
2. `setupAgencyTaskNotifierTrigger()` 手動Run用ヘルパー
3. `KCS_REQUIRED_TRIGGERS` に `'agencyTaskNotifier'` 追加 → kcsHealthMonitor の自己修復経路で欠落検知＆再登録
4. `setupMonitoringTriggers()` にも追加 → 一括セットアップ時に自動登録

**意図的に「通知のみ」にした理由:** タスクの自動実行（例: HAL投稿生成・スクレイピング起動）はブランド影響が大きく、誤分類で不適切な投稿が走るとXアカウント停止リスクすらある。まず「人間が気づく」ステージで止め、確度・分類精度が上がってから自動実行へ昇格させる段階的アプローチ。マニュアル §6.2「MTTR重視」の精神に沿い、見えないバグより見える警告を優先。

**棄却案:**
- ScriptProperty ではなく 10列目に `notified_at` を持つ → スキーマ拡張のリスクと dedup ロジックの重複。`kcsHealthMonitor` の `KCS_DIRTY_REPORTED_IDS` と同じ Pattern で統一した方が保守しやすい
- Webhook の宛先を `daily-report` 固定 → タスクは「実務」なので KCS本部 がふさわしい。フォールバックは `daily-report` → 任意

## 2. 既存破壊リスク評価

| 変更 | 既存呼び出し影響 | 対応 |
|---|---|---|
| `logChatMessage` に7列目追加 | 旧シート（6列）でも `appendRow` は7列分書き込み → 自動拡張で OK | ✅ 安全（GAS の `appendRow` は列数を超えても無条件追加） |
| `setupKCS` の logH 拡張 | 既存ヘッダ上書きで7列目に 'Rationale' 追記 | ✅ 既存データ行は無傷 |
| `cmdAskGemini` で extractRationale 抽出 | opts.staffName 経路のみ。旧呼び出し12箇所は影響なし | ✅ 後方互換 |
| `KCS_REQUIRED_TRIGGERS` 追加 | kcsHealthMonitor の `checkTriggersIntact` が「欠落」と検知 → runSelfHeal が `setupMonitoringTriggers` を叩いて再登録 | ✅ 自動修復フロー成立 |

## 3. デプロイ後の社長アクション

1. GASエディタで `setupChatLogSchema()` を一度Run → 既存チャットログシートに7列目 Rationale が追加される
2. GASエディタで `setupAgencyTaskNotifierTrigger()` を一度Run → 10分毎トリガー登録（次の kcsHealthMonitor 起動を待っても自動修復されるが、即時反映したければ手動Run）
3. Discord に「ハル、明日のコーデ投稿作成して」と送る → 実務タスク管理シートに新規行追加 → 次の10分以内に KCS本部 で通知到着
4. チャットログシートに行が追加され、7列目に「[Rationale: ...] の中身」が分離記録される

## 4. ロールバック手順

1. `clasp pull`
2. 戻す範囲:
   - `cmdAskGemini` の `extractRationale` 呼び出しを削除（rationale プロパティ非送出）
   - `logChatMessage` の `data.rationale` を読まないように戻す
   - `setupKCS` の logH を6列に戻す
   - `KCS_REQUIRED_TRIGGERS` から `'agencyTaskNotifier'` を削除
   - `agencyTaskNotifier` / `setupAgencyTaskNotifierTrigger` / `setupChatLogSchema` / `extractRationale` を削除
3. `clasp push -f`

Rationale 7列目データは残るが無害（読まれないだけ）。

## 5. 残課題（次々パッチ）

- **実務タスク実行ワーカー本体** — `discord_directive` を staffName + instruction 解析で `generateHALPost` / `generateSunakkunPost` 等にdispatch。`FULL_AUTO_MODE=TRUE` 時は自動着火、`FALSE` 時は `!実行 task_xxx` コマンドで承認後着火
- **Discord 受信2系統の正規化＋ディスパッチ共通化** — 引き続き残課題
- **`チャットログ` の Rationale ベクトル集計** — 朝礼ブリーフィングで「昨日のスタッフ判断傾向」要約への接続
