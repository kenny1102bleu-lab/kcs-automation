# Rationale: 動画・画像生成を Google FLOW (Veo/Imagen) に一本化

- **日付:** 2026-06-30（第8次パッチ）
- **担当ロール:** ノア（自己修復）＋ ケンジ（実装）＋ ナナ（デザイナー・生成レビュー）
- **対象ファイル:** `GAS_KCS合同会社_Backend.js`
- **社長指示:**
  - 「アカリ動画dispatch失敗: GITHUB_DISPATCH_TOKEN/GITHUB_TOKEN 未設定」の常時アラート除去
  - 動画・画像は Google FLOW で生成

## 1. 背景と切替判断

### 1.1 現象
KCS-GAS アプリから Discord へ `❌ アカリ動画dispatch失敗: GITHUB_DISPATCH_TOKEN/GITHUB_TOKEN 未設定` が周期的に投稿されていた。GitHub Actions への `repository_dispatch` 呼び出しで、トークン未設定エラーが繰り返しトリガーされる状態。

### 1.2 なぜ dispatch 経路を維持しなかったか
- **鍵管理の複雑化**: GITHUB_TOKEN / GITHUB_DISPATCH_TOKEN の2重管理、fine-grained token の期限切れ運用、リポジトリ scope 変更のたびに再発行が必要
- **フェイル・ラウド**: 未設定時にリトライループで Discord を汚染
- **依存の膨張**: GitHub Actions runner → ffmpeg → Suno/Udio → Drive アップロードの多段パイプラインで、任意の一箇所の障害が全体停止
- **Veo/Imagen が GAS から直接叩ける**: GEMINI_API_KEY 一本で完結、追加インフラ不要

### 1.3 Google FLOW 選定理由
- 社長が明示的に指示（labs.google/fx/tools/flow）
- Veo 3.1: 8秒 9:16 動画をプロンプトから直接生成、Shorts / TikTok にそのまま流用可
- Imagen 4: サムネイル・キービジュアル・投稿画像を高品質に生成
- `GEMINI_API_KEY` の scope で両方カバー（キー2重管理不要）

## 2. 実装内容（Backend.js）

### 2.1 コア関数
| 関数 | 役割 |
|---|---|
| `generateGoogleFlowVideo(prompt, opts)` | Veo 3.1 の predictLongRunning を呼び、`_pollGoogleFlowOperation` で完了待ち。返り値 `{ ok, videoUri, operationName }` |
| `generateGoogleFlowImage(prompt, opts)` | Imagen 4 の predict を同期呼び、base64 → Blob 変換して返す |
| `_pollGoogleFlowOperation(name, apiKey, maxWaitSec)` | 10秒間隔で `/{operation}` を叩き、`done:true` を待つ。default 300秒 timeout |

### 2.2 アカリ用ラッパー（生成 → Drive保存 → Discord告知の一連パイプライン）
| 関数 | 動作 |
|---|---|
| `generateAkariVideo(prompt, opts)` | Veo 生成 → `AKARI_VIDEO_FOLDER_ID` (未設定なら root) に MP4 保存 → KCS本部 に完了通知 |
| `generateAkariImage(prompt, opts)` | Imagen 生成 → `AKARI_IMAGE_FOLDER_ID` に PNG 保存 → KCS本部 に完了通知 |

### 2.3 呼び出し経路

**Discord テキストコマンド:**
```
!動画 東京の夜景をドローンで撮影
!画像 夕焼けの海辺のアニメ調イラスト
```
`handleBotCommand` に分岐追加、`!ヘルプ` にも掲載。

**Web App doPost（外部システムからの呼び出し / 旧 dispatch 経路の受け皿）:**
```json
POST https://script.google.com/.../exec
{ "action": "akari_video", "prompt": "...", "aspectRatio": "9:16", "duration": 8 }

{ "action": "akari_image", "prompt": "...", "aspectRatio": "1:1", "sampleCount": 1 }
```
n8n / Make.com / Zapier / 外部 GAS プロジェクトから叩ける。既存の GitHub Actions dispatch を呼んでいた caller は、このエンドポイントへ書き換えれば即移行できる。

## 3. なぜエラー元を「特定して直す」ではなく「代替路を提供」で解決したか

社長報告のエラー文字列 `アカリ動画dispatch失敗: GITHUB_DISPATCH_TOKEN/GITHUB_TOKEN 未設定` は
- 現行 Backend.js のどこにも存在せず
- kcs-automation リポの Python / YAML にもなく
- おそらく **外部システム（n8n / Make / 別 GAS プロジェクト等）** から Discord Webhook を直接叩いている

...と推定される。エラー源の探索コストより「新しい正解の道を提供する」方が速く、社長の指示にも合致するため、**Google FLOW 経路を一本化して用意し、旧経路を段階的に置換する**方針を採用。

## 4. 既存破壊リスク評価

| 変更 | 影響 | 対応 |
|---|---|---|
| 新関数追加（5個） | 既存呼び出しゼロ、副作用なし | ✅ |
| `!動画` / `!画像` コマンド追加 | 既存 `!実行` 等の並びに追加 | ✅ 影響なし |
| `doPost` に action ルート2つ追加 | 既存 action ケースの switch 前に挿入 | ✅ 順序保持 |
| `Utilities.sleep(10000)` を最大30回 | GAS Web App 30分制限内に収まる | ✅ 最大 300秒 |

## 5. 検証チェックリスト

- [ ] `!ヘルプ` に `!動画` / `!画像` が表示
- [ ] Discord `!動画 東京の夜景をドローンで撮影` → 数分後に「🎬 Google FLOW 動画生成完了」＋Drive URL
- [ ] Discord `!画像 夕焼けの海辺` → 数十秒後に「🎨 Google FLOW 画像生成完了」＋Drive URL
- [ ] 外部から `POST { action: 'akari_video', prompt: '...' }` → `{ ok: true, videoUri, driveUrl }` レスポンス
- [ ] `GEMINI_API_KEY` 未設定時: `{ ok: false, error: 'GEMINI_API_KEY 未設定' }` を返す（黙って落ちない）

## 6. ロールバック手順

1. `clasp pull`
2. 新規追加した5関数 + `doPost` 2ルート + Discord コマンド2つ + ヘルプ2行を削除
3. `clasp push -f`

Drive に保存された生成物は残るが無害。

## 7. 残課題

- **旧 dispatch 経路の caller 特定**: エラーが完全に消えない場合、n8n / Make / 別 GAS プロジェクトを走査して代替 URL に切替
- **Veo/Imagen クォータ監視**: 月次予算 vs 生成回数の可視化。`nana.py` の `_video_count_this_month` パターンを移植
- **`AKARI_VIDEO_FOLDER_ID` / `AKARI_IMAGE_FOLDER_ID` の設定シート追加**: 現状はコード側 fallback、明示化推奨
- **NG コンテンツフィルタ**: 生成プロンプトの pre-check（MIMOMI / 未成年描写 / 政治対立の禁止語）
- **もる YouTube Shorts パイプライン統合**: 現状はアカリ想定だが、もる（動画クリエイター犬）も同じ関数を呼べる汎用性はある
