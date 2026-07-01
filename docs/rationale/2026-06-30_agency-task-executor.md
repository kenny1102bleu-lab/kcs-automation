# Rationale: runAgencyTask 実行ワーカー本体（!実行 Discord コマンド）

- **日付:** 2026-06-30（第6次パッチ）
- **担当ロール:** ノア（自己修復）＋ ケンジ（実装）＋ サクラ秘書（進行管理）
- **対象ファイル:** `GAS_KCS合同会社_Backend.js`
- **関連関数:** `runAgencyTask`, `_dispatchAgencyTask`, `handleBotCommand`（!実行）, `generateHALPost`, `generateSunakkunPost`, `_createAgencyTask`, `agencyTaskNotifier`
- **マニュアル準拠:** §4（コンテンツ生成・投稿パイプライン）／§6.2（MTTR）／§6.3（Human-in-the-loop）／§7.1（異常系プロトコル）

## 1. 完成させたループ

これまで:
```
Discord指示 → detectChatMode(work) → _createAgencyTask() → シート「待機中」→ agencyTaskNotifier → Discord通知
                                                                                          ↑
                                                                                      ここで停止
```

本パッチで:
```
… → Discord通知 →【社長判断】→ !実行 taskId → runAgencyTask → _dispatchAgencyTask → generateXxxPost → 完了/失敗
```

マニュアル §4「コンテンツ生成・投稿パイプライン」が Discord からのフリーテキスト指示で通り抜けるようになった。

## 2. Human-in-the-loop の担保方針

### 2.1 なぜ「自動タイマー実行」を選ばなかったか

代替案:
- **(a) `agencyTaskNotifier` を実行系に格上げ** → 10分毎に待機中を自動実行。危険度大
- **(b) FULL_AUTO_MODE=TRUE 時に自動実行** → gate はあるが誤判定リスク
- **(c) `!実行 taskId` 明示コマンドのみ（採用）** → 社長判断100%、誤自動化ゼロ

「投稿系」は X アカウントへの publish になり、ブランドリスクが高い。特にHAL/すなくんは公開アカウントで、誤った投稿は shadowban / follower loss / スポンサー契約破棄に直結。

Discord Notifier （§前パッチ）で「気づく」段階までは自動化して、**「実行する」段階は必ず社長の意思決定を挟む**設計。マニュアル §6.3 の原則に厳密準拠。

### 2.2 ステータス遷移と重複防止

`待機中 → 実行中 → 完了 / 失敗 / 要手動対応`

- 実行開始時に即 `実行中` へ更新 → 同一 taskId の同時 `!実行` 二重処理を物理的に防ぐ
- 例外時も try/catch で dispatch を包み、必ずステータスと結果列を更新（**タスクが「実行中」で塩漬けになるのを防ぐ**）

## 3. Dispatch 設計

### 3.1 現状の対応表

| staffName | 指示キーワード | 委譲先 |
|---|---|---|
| HAL | 投稿/ポスト/tweet/コーデ/商品/ガジェット/X投稿 | `generateHALPost({theme})` |
| すなくん (sunakun) | 同上 | `generateSunakkunPost({theme})` |
| **その他18名** | - | `要手動対応` を返却 |

### 3.2 なぜHAL/すなくんだけ自動化したか

- 既存の `generateHALPost` / `generateSunakkunPost` は FULL_AUTO_MODE の内部ガード＋Pending_Postsフローを持っており、承認プロセスを二重に保証
- 他スタッフ（ジュン専務/サクラ秘書/ケンジ/ノア/等）は主に情報加工・判断・意思決定担当で「投稿」の実体がない
- ナナ（画像）/カイト（動画）/ステラ（占星術コンテンツ）は将来対応候補。今回は範囲外

### 3.3 テーマ抽出の素朴実装

```js
const theme = instruction.replace(/(投稿|ポスト|tweet|作成|して|お願い)/gi, '').trim();
```

「ハル、コーデ投稿を作成してお願い」→ `theme = "ハル、コーデを"`

粗いが最初は十分。将来的には Gemini でテーマ抽出を精度化する余地あり。

## 4. Discord コマンド

新規追加:
```
!実行 task_1776xxxxxx_ab12
```

正規表現: `^(実行|execute|run)\s`。ヘルプメッセージにも追記。

エラーレスポンス例:
- 存在しない taskId → `❌ 実行失敗 ... > taskId が見つかりません`
- 既に「完了」済み → `❌ 実行失敗 ... > タスクは既に「完了」状態です`
- HAL/すなくん以外 → `⚠️ 要手動対応 ... > 未対応の組み合わせ...`

## 5. 既存破壊リスク評価

| 変更 | 影響 | 対応 |
|---|---|---|
| `runAgencyTask` 新規 | 既存 addAgencyTask/agencyTaskNotifier と独立 | ✅ 影響なし |
| `_dispatchAgencyTask` 新規 | 既存フローに介入せず、明示コマンド経由のみ | ✅ 影響なし |
| `!実行` コマンド追加 | 既存 !返信承認/!返信スキップの後ろに配置 | ✅ 影響なし |
| ヘルプメッセージ拡張 | 表示行が1行増えるだけ | ✅ 影響なし |

**generateHALPost / generateSunakkunPost 内部**の FULL_AUTO_MODE ガードは触っていない。この2関数は自身の中で:
- FULL_AUTO_MODE=TRUE → 生成後に即 X 投稿
- FULL_AUTO_MODE=FALSE → Pending_Posts 経由で承認待ち通知

したがって `!実行` を叩いても、実際の X publish は既存の FULL_AUTO_MODE 設定に従う（**二重の Human-in-the-loop**）。

## 6. 検証チェックリスト

- [ ] `!ヘルプ` で `!実行 [taskId]` が表示される
- [ ] Discord で「ハル、明日のコーデ投稿作成して」→ 実務タスク登録 → agencyTaskNotifier 通知 → `!実行 task_xxx` → 「✅ 実行完了 (テーマ: 明日のコーデ)」
- [ ] Discord で「ノア、修復パッチ書いて」→ 実務タスク登録 → `!実行 task_xxx` → 「⚠️ 要手動対応」
- [ ] 存在しない taskId で `!実行 task_dead` → 「❌ 実行失敗 > taskId が見つかりません」
- [ ] 同じ taskId で連続 `!実行` → 2回目は「タスクは既に『実行中』or『完了』状態です」

## 7. ロールバック手順

1. `clasp pull`
2. `runAgencyTask` / `_dispatchAgencyTask` 関数定義を削除
3. `handleBotCommand` の `!実行` 分岐を削除
4. ヘルプメッセージから `!実行` 行を削除
5. `clasp push -f`

シートの status/result 列は残っても無害。

## 8. 残課題

- **より賢いテーマ抽出** — 現状は素朴な正規表現置換。Gemini で「指示 → theme パラメータ抽出」を挟む
- **他スタッフの実行対応** — アカリのトレンドまとめ、シオリのリサーチ、ノアの Rationale 生成、等
- **`実務タスク管理` のGUI** — 現状はスプレッドシート直参照。React ダッシュボード（マニュアル §1.2）から見えるようにする
- **並列実行時のロック** — 現状は「実行中」ステータス即書きで防いでいるが、getRange/setValue 間の 100ms 程度の race がある。厳密ロックが必要なら LockService に置換
