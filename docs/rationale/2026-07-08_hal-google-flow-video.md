# Rationale: HALのX投稿動画をGoogle Flow(Drive)経由に変更

- **日付:** 2026-07-08
- **対象:** `scripts/common/nana.py`、`scripts/hal_post.py`、`.github/workflows/02_hal_post.yml`、GAS `03_hal_image_post.js`/`doGet`
- **経緯:** WF-02の動画版テスト実行で`veo-3.1-generate-preview:predictLongRunning`が`400 Bad Request`で失敗（投稿は文章のみで公開プレビューされた）。社長から「動画が生成されていないね、Google Flowで製作してほしい」と指示。

## 調査で判明したこと

- GASには`03_hal_image_post.js`という「Google Flow → Drive → GAS → X」の画像投稿パイプラインが既に存在したが、`XService.uploadMedia()`という**未定義の関数**に依存しており、そもそも実行不可能な状態だった。
- さらに実際に10:00トリガーで動いているのは同名っぽいが別実装の`autoPostHAL()`（`GAS_KCS合同会社_Backend.js`）で、`03_hal_image_post.js`とは無関係。`03_hal_image_post.js`は過去の「v2アップグレード」バッチの一部で、本番のどこからも呼ばれていないデッドコードだった。
- つまり「Google Flow経由の投稿」は画像側も実際には機能しておらず、真似すべき生きた実装は存在しなかった。

## 変更内容

新規にPython↔GAS間の橋渡しを実装（Veoデバッグはこれ以上追わず、承認済みの方針に転換）。

1. **GAS側**: `doGet(?action=pop_hal_flow_video)`を追加（`GAS_KCS合同会社_Backend.js`の`doGet`から`03_hal_image_post.js`の`popHALFlowVideo()`を呼ぶ）。Driveの`HAL_Flow_Videos`フォルダから動画(mp4)を1本取り出し、base64で返しつつ`HAL_Flow_Videos_Used`へ自動退避する（同じ動画を2回配らないため）。
2. **Python側**: `nana.py`に`_fetch_hal_flow_video()`を追加。`generate_media()`のvideo分岐で、HALの場合はまずこの関数でGoogle Flow動画の有無を確認し、あればそれを使う（Veo呼び出しをスキップ）。無ければ従来通りVeoにフォールバック。
3. **ワークフロー**: `02_hal_post.yml`に新しいシークレット`HAL_FLOW_VIDEO_API_URL`を渡すよう追加。
4. **副次的な修正**: `hal_post.py`のユキ向けキャラ設定文から固定の「拠点は東京。代官山のカフェや街角の日常を切り取る。」を削除し、複数エリアを例示する形に変更（同日の別修正でnana.py側のシーンバリエーションは直したが、投稿テキストを生成するこちらのプロンプトの修正が漏れていたため）。

## 社長側で必要な作業

1. Googleドライブに**「HAL_Flow_Videos」フォルダ**を作成（`HAL_Flow_Images`と同じ場所推奨）。Google Flowで作った動画（mp4）をこの中に置く。
2. KCS-Database-JPのGASプロジェクトが**Webアプリとしてデプロイ済みか確認**。デプロイ済みなら、そのexec URL（例: `https://script.google.com/macros/s/XXXXX/exec`）を控える。未デプロイなら「デプロイ」→「新しいデプロイ」→「ウェブアプリ」で発行（実行ユーザー: 自分、アクセス: 全員 推奨、既存の`get_staff`等のエンドポイントと同じ設定に合わせる）。
3. GitHubリポジトリの Settings → Secrets and variables → Actions に、新しいシークレット **`HAL_FLOW_VIDEO_API_URL`** を上記URLで追加する。

これが完了するまでは、`HAL_FLOW_VIDEO_API_URL`が空のため`_fetch_hal_flow_video()`は何もせず、これまで通りVeoにフォールバックする（Veo自体は現在400エラーのため、動画無し・テキストのみの投稿になる）。

## 未解決の課題

- Veoの400エラーの根本原因は未調査（モデル名`veo-3.1-generate-preview`が古い/`durationSeconds: 5`が不正、等の可能性）。Google Flow運用に切り替えたため今回は追わなかった。動画ファイルが用意できない日の自動フォールバックとして今後Veoを直す場合は、`_generate_video()`のエラーハンドリングにレスポンスボディの記録を追加してから再調査するとよい。
- base64転送のため、動画サイズが大きい（目安10MB超）とGASのWebアプリ応答上限に抵触する可能性がある。短尺のX投稿用クリップであれば問題ない想定。
