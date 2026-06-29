# Rationale: Discord個別スタッフ会話（ペルソナディスパッチ）機能追加

- **日付:** 2026-06-30
- **担当ロール:** ノア（自己修復パッチ）＋ ケンジ（実装）
- **対象ファイル:** `GAS_KCS合同会社_Backend.js`
- **関連関数:** `_getCustomStaffList`, `getStaffMemory`, `escapeRegExp`, `selectPersona`, `detectChatMode`, `buildStaffSystemPrompt`, `cmdAskGemini`（拡張）, `handleDiscordMessageFromMake`, `discordAgentTick`
- **マニュアル準拠:** §3（スタッフ特定）／§4（モード判定）／§5（パッチ詳細）／§6（説明責任・MTTR・Human-in-the-loop）

## 1. 背景

「KCS AIマネージャー」一律応答を廃止し、20名のAIスタッフが各々のペルソナ・年齢・スキル・長期記憶で自律応答する Discord 体験を構築する（指示書 §1）。

## 2. 既存実装の課題と意思決定

### 2.1 受信フローの二重化はそのまま維持

`discordAgentTick`（1分Bot polling）と `handleDiscordMessageFromMake`（n8n/Make Webhook）の **2系統を統合せず両方にペルソナ統合を入れた**。

**理由:** 統合リファクタは破壊範囲が広く、現行 n8n インスタンス停止中（[[project-n8n-inactive]]）の状況では検証が片肺になる。両方に薄く統合する方が安全で、将来の統合は別パッチに切り出す（残課題§5）。

### 2.2 `cmdAskGemini` シグネチャ衝突の回避

指示書原案は第4引数を `staffName` にしていたが、既存12箇所の呼び出し（HAL投稿生成・すなくん投稿生成・GrowthEngine・engagementTick・autoReply等）は全て第4引数を `customSystemPrompt` として渡していた。

**採用案:** 第5引数 `opts = { staffName, staffEmoji, staffRole, temperature, rationale }` のオプション・オブジェクトを追加。既存呼び出しは無変更で動作維持、新規呼び出しのみが拡張機能を opt-in する。

**棄却案:** 名前付き引数ぽいオブジェクト1引数版（`cmdAskGemini({text, config, ...})`）にすべて統一 → 12箇所の改修が必須で破壊範囲が指示書原案と同じ。利益小・リスク大。

### 2.3 `getCustomStaff()` の戻り型問題

既存実装は `jsonResponse([])`（ContentService オブジェクト）を返す Web App エンドポイント。指示書は `.find()` できる配列を前提。

**採用案:** 内部用 `_getCustomStaffList()` を新設し、`getCustomStaff()` はその結果を `jsonResponse` でラップする薄いラッパーへ刷新。WebApp 互換は完全維持しつつ、内部 GAS から直接配列アクセスできる。

### 2.4 スタッフ名マッチの誤爆対策

`new RegExp(s.name, 'i').test(text)` を素朴に書くと:
- メタ文字（`.`, `+`, `?` 等）を含む名前で正規表現崩壊
- 「ハル」が「ハルキ」のメッセージにヒットして誤発火

**対策:**
1. `escapeRegExp` で全名称をリテラル化
2. 名前長の降順ソート → 長い名前を先にマッチ（「ハルキ」→「ハル」の順）
3. 前後を非英数字 or 句読点 or 文末で挟む境界正規表現 `(^|[^A-Za-z0-9_])name([、,:：．。\\s！？!?]|$)` を採用

### 2.5 データアイソレーション（指示書 §3 厳守）

`getStaffMemory(staffName, username)` は `${staffName}_Memory` シートを動的解決し、HAL は既存 `getHALMemory` に委譲、他はオンデマンド参照（シート未作成なら空文字）。横断参照を物理的に不可能にし、HAL の記憶が他スタッフから読めない構造。

### 2.6 すなくん年齢 24→26 統一

GAS 内 `SUNAKKUN_SYSTEM_PROMPT`（line 3505）と userPrompt（line 3547）は 24歳、memory `project_persona_sunakun.md` 本文は 26歳、memory index と `project_kcsapp_system.md` は 24歳と三重矛盾していた。

**真実認定:** 指示書「ソース2.2およびImage 1/5を厳守」＋ペルソナ本文の26歳を真として、GAS 2箇所とメモリ2箇所を 26 に統一。

### 2.7 Rationale 強制は opt-in に

指示書 §5.2 は「内部プロンプトで Rationale 末尾を強制」と書かれていたが、既存12箇所の呼び出しに全て Rationale が混入すると HAL/すなくん投稿生成の出力フォーマットが壊れる（JSONパース失敗）。

**採用案:** `opts.rationale=true` のときのみシステム指示に Rationale 要求を追加。Discord ペルソナ応答のみが opt-in する。

## 3. モード判定（指示書 §4）

`detectChatMode(text)`:
- **work モード:** 命令形（「〜して」「〜してください」）／実務動詞（作成・生成・実行・投稿・送信・発行・登録）／「お願い」を含む
- **chat モード:** それ以外（挨拶・問いかけ・雑談）

現状は **判定結果をログに残すのみ**。実務タスク発行（`addAgencyTask`）への接続は将来パッチ（Human-in-the-loop 検証パスの設計が必要なため）。

## 4. ロールバック手順

1. `clasp pull`
2. 戻す範囲:
   - `getCustomStaff` 旧実装に戻す（`_getCustomStaffList`含めて削除）
   - 新規追加した `escapeRegExp` / `getStaffMemory` / `selectPersona` / `detectChatMode` / `buildStaffSystemPrompt` を削除
   - `cmdAskGemini` の opts 引数とロジックを削除（既存4引数版へ）
   - `handleDiscordMessageFromMake` の自由文分岐を `cmdAskGemini(text, config, 'KCS本部')` 単独に戻す
   - `discordAgentTick` の KCS本部分岐を旧 if-else に戻す
   - SUNAKKUN 年齢を26→24に戻す（注意: 真実は26なので戻すべきではない）
3. `clasp push -f`

## 5. 残課題

- **受信2系統の統合** — `discordAgentTick` と `handleDiscordMessageFromMake` のメッセージ正規化＋ディスパッチ共通化（指示書 §2 指摘）
- **`addAgencyTask` 接続** — work モード検知時に `Pending_Posts` 経由で人間承認 → 実行（§6 Human-in-the-loop）
- **カスタムスタッフシートに `discordUserId` カラム追加** — 現状コードは10列目を読みに行くが運用シートは未対応。サクラ秘書に列追加を依頼予定
- **`autoReplyTick` の `replyDraft` 無サニタイズ** — 前回 Rationale で指摘済み、別パッチ

## 6. 検証チェックリスト（指示書 §7）

- [ ] ハル（21歳）: 「ハル、お疲れ様」→ 日本語＋繁體字のおっとり返答（カスタムスタッフシートに繁體字ルール記載前提）
- [ ] すなくん（26歳）: 「すなくん、最新マウス教えて」→ 26歳カジュアル＋リンク誘導文を含む
- [ ] 動的ロード: 「アカリ」「もる」メンションでカスタムスタッフシート行のプロンプトが適用される
- [ ] Rationale: `チャットログ` シートに各応答が記録され、本文末尾に `[Rationale: ...]` が付く
- [ ] フォールバック: メンションなし → 旧 AIマネージャー応答
- [ ] 既存破壊なし: HAL/すなくん投稿生成・GrowthEngine・engagementTick が動作継続
