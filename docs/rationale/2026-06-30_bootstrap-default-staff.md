# Rationale: bootstrapDefaultStaff — マニュアル§2組織図の20名を冪等投入

- **日付:** 2026-06-30（同日の続パッチ）
- **担当ロール:** ノア（自己修復）＋ サクラ秘書（進行管理）＋ ジュン専務（戦略意思決定）
- **対象ファイル:** `GAS_KCS合同会社_Backend.js`
- **関連関数:** `bootstrapDefaultStaff`, `_getCustomStaffList`, `selectPersona`, `buildStaffSystemPrompt`, `setupCustomStaffSchema`
- **マニュアル準拠:** §2（組織構造）／§3（スタッフ特定とペルソナ）／§6.1（Rationale必須）／§6.3（Human-in-the-loop）

## 1. 背景: 実装したのに動かない問題

前パッチで Discord ペルソナディスパッチ機能（`selectPersona` → `buildStaffSystemPrompt` → `cmdAskGemini(staffName, ...)`）を本番反映したが、ブラウザ検証で **`カスタムスタッフ` シートのデータ行が0件** であることが判明。`_getCustomStaffList()` が空配列を返し、`selectPersona()` が常に null → 全ペルソナ応答が既存 AIマネージャー フォールバックに落ちる状態。

**コードはあっても運用データ（20名分）がないと無価値** という典型的なギャップ。本パッチは「動かす最小データ」を冪等に投入する。

## 2. 設計判断

### 2.1 なぜ「テンプレ生成関数」を選んだか

代替案:
- (a) 社長に20名分を手動入力してもらう → スプレッドシート20行 × 10列 = 200セル。長文プロンプトを含み現実的に重労働
- (b) サクラ秘書 AI に書かせる → 既存スタッフが居ないので呼び出せない（鶏卵問題）
- (c) **テンプレ関数を1個Runで自動投入（採用）** → 1コマンドで完了、レビュー可能、idempotent

### 2.2 idempotent 設計

既存 ID（A列）を Set に集めて、`DEFAULT_STAFF` の各 ID が **未登録のときだけ appendRow**。
- 社長が後で1名手動編集 → 関数を再Runしても上書きされない
- 1名足したい場合は DEFAULT_STAFF にコード追加して再Run → 差分のみ追加
- ScriptProperty を使わずシート自身を真実の源とする

### 2.3 20名の役割マッピング（マニュアル§2準拠）

| 部門 | スタッフ | aiMode | temperature | 主な意図 |
|---|---|---|---|---|
| AIタレント | HAL / すなくん / もる / ルナ / エマ | gemini | 0.7-0.85 | キャラ性重視、温度高め |
| 戦略・管理 | ジュン専務 / サクラ秘書 / ハルキ | claude | 0.5-0.75 | 意思決定の安定性重視 |
| 制作・マーケ | アカリ / ユキ / リョウ | gemini | 0.6-0.7 | 創発と分析の両立 |
| 技術・実務 | ケンジ / タクミ | claude/gemini | 0.3-0.7 | ケンジは温度極低（コードは決定論的に） |
| 特化型 | マモル / ノア / ナナ / カイト / ステラ / シオリ / シオン | mix | 0.3-0.85 | 役割ごとに最適化 |

**aiMode について:** 現状の `cmdAskGemini` は実装上 Gemini 固定。aiMode 列は将来 Claude API ルーティングに切り替える際の hint として記録。今すぐの挙動には影響しない。

### 2.4 HAL のプロンプト設計

HAL のシステムプロンプトには `{{DOU_TEKI_KI_OKU}}` と `{{TIEUP_PRODUCTS}}` プレースホルダを意図的に含めた:
- `buildStaffSystemPrompt(staff, username)` が HAL 検知時に `getStaffMemory('HAL', username)` と `getHALTieupProductContext()` を呼んで置換
- プレースホルダなしで書くと長期記憶が活用されない（指示書 §3 違反）
- 既存メモリ [[feedback-hal-chinese]] [[feedback-hal-mimomi-pending]] [[feedback-x-posting-absolute-rules]] を反映:
  - 日本語＋繁體字バイリンガル必須
  - 簡体字禁止
  - AI自認禁止
  - MIMOMI/タイアップ言及制限

### 2.5 すなくん のプロンプト設計

メモリ [[project-persona-sunakun]] 通り **26歳** で統一（前パッチで GAS と memory も 24→26 統一済み）。
リプ誘導文の必須化を **絶対ルール** としてプロンプト本体に組み込み、`buildStaffSystemPrompt` 側の補強と二重ガード化。

## 3. 既存破壊リスク評価

| 変更 | 影響 | 対応 |
|---|---|---|
| `カスタムスタッフ` シートに20行 append | 既存行ゼロが前提（検証済）| ✅ idempotent なので再Run安全 |
| HAL/すなくん の長文 X 投稿用プロンプト（SUNAKKUN_SYSTEM_PROMPT 等）| 別経路で温存（generateHALPost 等）| ✅ Discord 雑談用と完全分離 |
| 既存 generateHALPost / generateSunakkunPost 経路 | 一切触らない | ✅ 影響なし |

## 4. 検証チェックリスト

- [ ] GASエディタで `bootstrapDefaultStaff()` を1回Run → ログに「追加: 20件 / スキップ: 0件 / 合計: 20名」が出る
- [ ] スプレッドシート カスタムスタッフ シートの行2〜21 にスタッフ20名分が並ぶ
- [ ] Discord で「ハル、お疲れ様」→ 🌸 **HAL:** で応答（プロンプト中のプレースホルダが置換されている）
- [ ] Discord で「ケンジ、現在のGASトリガー教えて」→ 💻 **ケンジ:** で応答、技術者口調
- [ ] Discord で「アカリ、今日のトレンドある？」→ 🔥 **アカリ:** で応答、時事ネタを織込む
- [ ] 2回目の `bootstrapDefaultStaff()` Run → ログに「追加: 0件 / スキップ: 20件」（idempotent 動作確認）

## 5. ロールバック手順

1. `clasp pull`
2. `bootstrapDefaultStaff` 関数定義を削除
3. `clasp push -f`
4. シートのデータ行を残したいなら何もしない、消したいなら手動で行2〜21削除

データ自体は永続化されているので、関数を消してもスタッフ定義は残る（むしろ削除リスクなし）。

## 6. 残課題（次パッチ）

- **アイコンURL列の自動投入** — 現状空。Drive にプロフィール画像を upload して URL を埋める
- **discordUserId 列の自動投入** — Discord で社長と各スタッフが対話するなら、社長側で20個の bot user を作る or 既存 user を割り当てる必要。手動入力でも可
- **AI Mode ルーティング** — `cmdAskGemini` を `cmdAskAI(text, config, projectName, customSystemPrompt, opts)` に格上げして aiMode=claude のときは Claude API、gemini のときは Gemini API に振り分け
- **task_177621558 の ffmpeg 修復** — もるパイプライン側の動画生成失敗。`scripts/` 配下の動画関連コード調査が必要
