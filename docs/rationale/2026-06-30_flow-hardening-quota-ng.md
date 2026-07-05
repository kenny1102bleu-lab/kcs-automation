# Rationale: Google FLOW hardening（クォータ監視／NGプロンプト／Settings デフォルト）

- **日付:** 2026-06-30（第9次パッチ）
- **担当ロール:** ノア（自己修復）＋ マモル（監視）＋ サクラ秘書（進行管理）
- **対象ファイル:** `GAS_KCS合同会社_Backend.js`
- **マニュアル準拠:** §6.2（MTTR / 品質KPI）／§6.3（Human-in-the-loop）／§7.1（異常系プロトコル）

## 1. 背景

前パッチで Google FLOW（Veo 3.1 / Imagen 4）の生成関数を追加したが、以下の運用リスクが残っていた:

1. **予算暴走リスク:** Veo は $0.35〜0.75/8秒クリップ、Imagen も課金対象。無制限に生成できると1バグで月数万〜数十万の請求
2. **ブランド事故リスク:** MIMOMI言及禁止 [[feedback-hal-mimomi-pending]] や未成年描写等の NG コンテンツがプロンプトに混入すると、生成物経由でアカウント停止に直結
3. **可視性の欠如:** 今月何本作ったか、あといくら使えるかが見えない → §6.2 の「MTTR重視」に反する

## 2. 実装内容

### 2.1 月次クォータ監視

**保存方式:** ScriptProperty `FLOW_USAGE_VIDEO_202606` / `FLOW_USAGE_IMAGE_202606` に月別カウントを持つ。月が変われば新キーで自動的に0開始（過去キーは残るが 3ヶ月放置で自然に切離せる）。

**関数:**
- `_flowUsageKey(kind, yyyymm)` / `_flowCurrentYyyymm()` — キー生成
- `flowUsageThisMonth(kind)` — 現在月の使用回数取得
- `_flowQuotaCheck(kind)` — オーバー時 `{ok:false, error, used, limit}` 返却
- `_flowRecordUse(kind)` — 生成成功時に+1

**限界値:**
- `MAX_VIDEO_PER_MONTH`: default **20**（月 $7〜$15 想定）
- `MAX_IMAGE_PER_MONTH`: default **200**（月 $5〜$10 想定）

Settings シートで社長がいつでも上下できる。

### 2.2 NG プロンプトフィルタ

**なぜ既存 `KCS_NG_CONTENT_PATTERNS` を使わなかったか:**
既存パターンは「投稿本文用」で `AI挨拶` / `JSON生波カッコ` 等のフィルタが含まれる。これらは映像プロンプトには誤検知する（「AIが〜」というプロンプトを弾く等）。

**FLOW_PROMPT_NG_PATTERNS を別途定義:** ブランド保護観点のみ、投稿用パターンとは独立。

| パターン | 意図 |
|---|---|
| MIMOMI言及 | [[feedback-hal-mimomi-pending]] 未確定タイアップの秘匿 |
| 未成年描写 | プラットフォーム規約遵守、アカウント停止防止 |
| 暴力・武器 | ブランドセーフ |
| 政治対立 | ブランドセーフ、シャドウバン回避 |
| アダルト直接語 | Veo/Imagen の safety filter に触れる前に自前で弾く |

**動作:** マッチ時は API を叩かない → APIコスト0で拒否 → `{ok:false, error:"prompt_ng: パターン名"}` を返す。

### 2.3 生成成功時のカウント記録

- Veo: `_pollGoogleFlowOperation` 内の成功パス（`videoUri` 取得直後）
- Imagen: `generateGoogleFlowImage` の成功 return 直前

失敗時（NG／クォータ超過／API error／timeout）はカウントしない。**成功したものだけを課金分としてカウント**する原則。

### 2.4 `!FLOW残` Discord コマンド

```
!FLOW残
→ 📊 Google FLOW クォータ 2026/06
   動画: 3 / 20
   画像: 47 / 200
```

`!ヘルプ` にも記載。

### 2.5 Settings デフォルト追加

`setupKCS()` の defaults に4項目追加:
- `AKARI_VIDEO_FOLDER_ID` — 動画保存フォルダ（空ならルート、既存の fallback ロジックと整合）
- `AKARI_IMAGE_FOLDER_ID` — 画像保存フォルダ
- `MAX_VIDEO_PER_MONTH` — 20
- `MAX_IMAGE_PER_MONTH` — 200

新規セットアップ時に自動、既存シートは追記対象。値0は「無制限」ではなく「0件で即拒否」なので、社長が空欄放置すると FLOW が使えなくなる点に注意（デフォルトが空ではなく `20`/`200` の理由）。

## 3. 既存破壊リスク評価

| 変更 | 影響 | 対応 |
|---|---|---|
| Veo/Imagen 関数の頭に pre-check 2段 追加 | 既存呼び出しはそのまま通る（GEMINI_API_KEY があってプロンプト正常なら） | ✅ 挙動変化なし |
| ScriptProperty `FLOW_USAGE_*` 新規 | 他の GAS 機能とキー衝突なし | ✅ 影響なし |
| Settings 4項目追加 | 既存シートは列上書きされないが値未設定なら code fallback | ✅ 影響なし |
| `!FLOW残` コマンド追加 | 既存 `!` コマンドと重複しない | ✅ |

## 4. 検証チェックリスト

- [ ] `!FLOW残` → 「動画: 0 / 20, 画像: 0 / 200」で応答
- [ ] `!動画 MIMOMI ロゴを大きく` → `{ok:false, error:"prompt_ng: MIMOMI言及"}` で拒否
- [ ] `!画像 かわいい猫のアニメイラスト` → 正常生成、`!FLOW残` の画像カウントが +1
- [ ] `MAX_VIDEO_PER_MONTH` を 0 に設定 → `!動画 テスト` → 「月間上限 0 件に達しました」で拒否
- [ ] 月替わり後の `!FLOW残` → 新しい yyyymm キーで 0/limit から再開

## 5. ロールバック手順

1. `clasp pull`
2. `FLOW_PROMPT_NG_PATTERNS` 定数、5個の `_flow*` ヘルパー、`flowUsageThisMonth`、`!FLOW残` 分岐、pre-check 2段、`_flowRecordUse` 呼出、Settings 4項目、ヘルプ1行を削除
3. `clasp push -f`

ScriptProperty の `FLOW_USAGE_*` は残置しても無害。

## 6. 残課題

- **旧 dispatch caller 特定** — 継続課題。エラー再発時は n8n / Make / 別 GAS プロジェクトを走査
- **`!FLOW残` に Drive フォルダ情報追加** — 保存先が root or 指定フォルダを可視化
- **月次サマリ通知** — 毎月1日9時に「先月 動画X本 / 画像Y本 生成」を KCS本部に告知
- **もる YouTube Shorts パイプライン統合** — `generateMoruVideo(prompt, script)` 等の分岐
- **プロンプト強化** — 「アカリのカラーは #FF6B35」のようなブランドカラー自動注入
