# Rationale: autoReplyTick サニタイズ二重ガード + workモード→実務タスク接続 + discordUserId列migration

- **日付:** 2026-06-30（同日の続パッチ）
- **担当ロール:** ノア（自己修復パッチ）＋ ケンジ（実装）
- **対象ファイル:** `GAS_KCS合同会社_Backend.js`
- **関連関数:** `autoReplyTick`, `handleBotCommand`（!返信承認）, `addAgencyTask`, `_createAgencyTask`, `setupCustomStaffSchema`, `setupKCS`, `handleDiscordMessageFromMake`, `discordAgentTick`
- **マニュアル準拠:** §4（仕事/雑談モード自動判定）／§6.3（Human-in-the-loop）／§7.1（エラーハンドリング）／§6.1（Rationale必須）

## 1. パッチ3点の意思決定

### 1.1 autoReplyTick サニタイズ二重ガード

**問題:** 前回 Rationale で予告した残課題。`autoReplyTick` で生成される `replyDraft` は `callClaudeAPI` 戻りを無加工で `PENDING_REPLY_*` に保存しており、AI挨拶や前置きが含まれたまま Discord 承認画面に出ていた。社長が見落として `!返信承認` した場合、`engagementTick` と同じ汚染ツイートが量産される構造。

**修正:**
1. **生成時サニタイズ**（`autoReplyTick` 内）— `sanitizePostText` で前置きを除去後、`KCS_NG_CONTENT_PATTERNS` で再走査。ヒット時は `draftStatus` を `ng_{name}` でマークし、Discord 承認画面に `⚠️ NG警告` を太字表示
2. **投稿直前の二重ガード**（`handleBotCommand` の `!返信承認` ハンドラ内）— PENDING_REPLY 取り出し直後にも `sanitizePostText` + NG 再走査。万一サニタイズ漏れが残っていても投稿前に必ず止める。NG時は `PENDING_REPLY_*` を消さず保持（手動修正の余地を残す）

**なぜ二段にしたか:** 一段だけだと、(a) ScriptProperty の TTL や Discord 表示済みのドラフトを再利用するワークフロー、(b) 設定変更により`sanitizePostText` ルール拡張した後で旧 PENDING_REPLY を承認する場合、に漏れる。承認瞬間のスナップショットで判定する二段方式が確実。

**棄却案:** `notifyDiscordError` で1段目NG時に承認データを破棄 → 社長が手動で書き直す機会を奪う。今回は破棄せず警告通知する設計を採用。

### 1.2 workモード時の `addAgencyTask` 接続（指示書 §4 完成）

**前回未着手の理由:** `addAgencyTask` の戻り値が WebApp 用 `jsonResponse` で内部から taskId 取得できなかった + Human-in-the-loop ガバナンス設計が必要だった。

**今回の対応:**
1. **`_createAgencyTask(data)` 内部関数追加** — `{taskId, status}` を生で返す。`addAgencyTask` は薄ラッパーへ刷新（WebApp 互換維持）
2. **ペルソナ応答後の work モード分岐** — `handleDiscordMessageFromMake` と `discordAgentTick` 両方で、`detectChatMode(text) === 'work'` 時に `_createAgencyTask({taskType: 'discord_directive', ...})` を発行
3. **応答末尾にタスクID表示** — `📋 実務タスク登録: \`task_xxx\`（FULL_AUTO_MODE=承認待ち/自動実行）` を AI 返答に追記。`FULL_AUTO_MODE` の現状値を明示することで Human-in-the-loop の状態が社長に常時可視

**実装の最小性:** タスク発行のみで「実際の投稿実行」は既存 `generateHALPost` / `autoPostAffiliateAmazon` 等の専用関数に委譲しない（責務分離）。`実務タスク管理` シートに記録されるだけで、ステータス更新やX投稿への接続は別パッチで進化させる（残課題§3）。

**なぜ「指示記録」だけにしたか:** ペルソナ応答の workモード語彙（「投稿して」「作成して」）は曖昧で、誤判定で実際の X 投稿が走るとブランドリスクが大きい。まず「タスクとして残す」だけにし、社長が `実務タスク管理` シートを見て手動着火する運用を維持。後で確度が上がったら自動実行へ昇格させる。

### 1.3 discordUserId 列 schema migrator

**問題:** `_getCustomStaffList()` は10列目を `discordUserId` として読むが、運用中の `カスタムスタッフ` シートは9列しかない。`<@123...>` メンション解決が永久に動かない。

**修正:**
1. **`setupKCS()` の `staffH` を10列に拡張** — 新規セットアップ時から `discordUserId` 列が入る
2. **`setupCustomStaffSchema()` 冪等migrator追加** — 既存シートに対し、10列目が存在しなければ追加するピンポイント関数。`setupKCS()` 全体実行のリスク（他シートの header 上書き）を避けたい場合の選択肢

社長は GAS エディタから `setupCustomStaffSchema` を一度Runするだけで列追加完了。データ行は触らない（破壊なし）。

## 2. 既存破壊リスク評価

| 変更 | 既存呼び出し影響 | 対応 |
|---|---|---|
| `addAgencyTask` → 薄ラッパー化 | WebApp 経由の戻り値スキーマ `{status, taskId}` 維持 | ✅ 維持 |
| `autoReplyTick` の `replyDraft` を sanitize | PENDING_REPLY_* の payload に `draftStatus` キー追加 | ✅ 旧 ScriptProperty 読み出しも JSON.parse で問題なし |
| `!返信承認` で投稿前 sanitize | サニタイズで文末記号が変わる可能性 | ✅ 軽微、X投稿として無害 |
| `setupKCS` の staffH 拡張 | 既存シートを上書き保存（同名列＋新規列） | ⚠️ 1列増えるが既存データ行は無傷 |

## 3. 検証チェックリスト

- [ ] `setupCustomStaffSchema()` を GAS エディタで実行 → カスタムスタッフシートに `discordUserId` 列が追加される
- [ ] `カスタムスタッフ` シートの ハル行 と すなくん行 の10列目に各 Discord ユーザーIDを記入
- [ ] Discord で `<@123...>` 形式のメンション → ID 一致でペルソナ選択される
- [ ] 「ハル、明日のコーデ投稿を作成して」→ work モード判定 → `実務タスク管理` シートに新規行追加＋応答末尾にタスクID表示
- [ ] 「ハル、お疲れ様」→ chat モード判定 → タスク登録なし、純粋に挨拶応答
- [ ] X 通知メールで autoReplyTick 起動 → 「承知しました」入り Claude 応答が来た場合 → Discord 承認画面に `⚠️ NG警告` 太字表示
- [ ] 警告無視で `!返信承認` 実行 → 投稿前に🛑停止メッセージ、PENDING_REPLY 保持

## 4. 残課題（次々パッチ）

- **Discord受信2系統の統合** — `discordAgentTick` と `handleDiscordMessageFromMake` の重複ロジック共通化
- **`実務タスク管理` → 実行ワーカー接続** — 待機中タスクをポーリングして実行（HAL投稿生成・スクレイピング等）。Human-in-the-loop は `FULL_AUTO_MODE` で制御
- **`チャットログ` シートの Rationale 抽出専用列** — 現状は応答本文末尾に `[Rationale: ...]` を含めるだけ。専用列に分離するとフィルタ・集計が楽
