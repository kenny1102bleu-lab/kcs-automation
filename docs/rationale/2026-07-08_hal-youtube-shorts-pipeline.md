# Rationale: HAL YouTube Shorts自動アップロード + 朝礼連動の企画会議

- **日付:** 2026-07-08
- **対象:** `09_hal_youtube_shorts.js`（新規）、`GAS_KCS合同会社_Backend.js`の`morningBriefing()`、`appsscript.json`
- **社長指示（要約）:**「HALの動画は生成したらYouTube Shortsに上げる。首脳スタッフ（ジュン専務陣）が前日の実績とニュースからバズる案を練り、担当スタッフ（ユキ）に作らせてほしい」

## 1. 設計

### フロー
1. **朝礼（`morningBriefing()`）**: 前日のYouTube Shorts実績（本数・合計/平均再生数・ベスト/ワースト動画）をデータとして提示し、**アカリ**（プロデューサー・トレンド担当）がベスト動画の傾向や検索材料をもとに具体的な企画（テーマ・切り口・タイトル案）を発案、**ジュン専務**がそれを評価・承認して結論としてユキへの指示に変換する。結論は`[MAIN]`内の読み物としてだけでなく、機械処理用の`[VIDEO_CONCEPT]`タグ（theme/title/description/tags）としても出力させ、`saveHALVideoConceptFromBriefing()`がスクリプトプロパティ`HAL_VIDEO_TODAY_CONCEPT`に保存する。
   - **2026-07-08 追記**: 当初はジュン専務自身が企画内容（テーマ・タイトル）まで発案する設計だったが、社長から「専務が企画を考えるのは役割と合わない、アカリに渡すべきでは」と指摘があり修正。ジュン専務は統括・最終決定権者、アカリはクリエイティブ／トレンド発案者という役割分担に統一した。
2. **動画制作**: ユキ（または社長）が朝礼の企画を元に実際の動画（mp4）を生成し、Googleドライブの`HAL_Shorts_Ready`フォルダに置く（既存の`HAL_Flow_Images`パターンを踏襲）。
3. **自動アップロード**: `processHALShortsQueue()`（30分毎トリガー）がフォルダを監視し、見つけたファイルをその日の企画のタイトル/説明/タグでYouTube Shortsとして公開、`HAL_Shorts_Used`へ移動、`HAL_YouTube_Log`シートに記録、Discordに完了通知。

### データ取得（読み取り、OAuth不要）
`getHALShortsPerformance(days)` — 既存の`YOUTUBE_API_KEY`/`YOUTUBE_CHANNEL_ID`（設定シート、`getYouTubeChannelStats()`と同じキー）を使い、`search.list`で直近動画→`videos.list?part=statistics`で動画別の再生数・いいね数・コメント数を取得。X（Twitter）と違い、YouTube Data APIは無料枠でも動画別統計を取得できるため、Xの時のような「エンゲージメント計測不能」問題は発生しない。

### アップロード（OAuth必要）
`uploadHALShort()` — GAS組み込みの**YouTube Advanced Service**（`appsscript.json`の`enabledAdvancedServices`に追加）を使用。スクリプト所有者（kenny1102bleu@gmail.com）の権限で`YouTube.Videos.insert()`を呼ぶため、個別のAPIキー管理やOAuth2ライブラリの追加設定は不要だが、**初回だけ手動でのOAuth同意が必要**（§3参照）。

## 2. 新規シート/フォルダ

| 名前 | 種別 | 用途 |
|---|---|---|
| `HAL_Shorts_Ready` | Driveフォルダ | 生成済み動画(mp4)の投入先。**社長側で作成が必要** |
| `HAL_Shorts_Used` | Driveフォルダ | アップロード済み動画の移動先（`processHALShortsQueue()`が自動作成） |
| `HAL_YouTube_Log` | スプレッドシート | タイムスタンプ/ファイル名/動画ID/タイトル/企画テーマ/URL（自動作成） |

## 3. 社長側で必要な作業（自動化できない部分）

1. **Googleドライブに `HAL_Shorts_Ready` フォルダを作成する**（`HAL_Flow_Images`と同じ場所推奨）。
2. **初回のみOAuth同意**: GASエディタで`uploadHALShort`または`processHALShortsQueue`を一度手動実行し、「このアプリはGoogleで確認されていません」の警告から「詳細」→「（プロジェクト名）に移動」で許可する。YouTubeへのアップロード権限（`youtube.upload`スコープ）を承認する必要がある。
3. **YouTube Data API v3が有効化されていない場合**: 手動実行時に「API has not been used in project... or it is disabled」エラーが出たら、エラーメッセージ中のリンクからCloud ConsoleでAPIを有効化する（数分反映待ち）。
4. **（任意）Discordでユキ専用チャンネルを使いたい場合**: 設定シートの`DISCORD_WEBHOOK_URLS`のJSONに`"ユキ"`または`"youtube"`キーでWebhook URLを追加する。未設定の場合はKCS本部チャンネルに通知される。

## 4. 未実装・今後の課題

- YouTube側のフォロワー（登録者数）目標ペース管理は未実装（Xの分析同様、固定目標値を勝手に設定するのは避けた）。
- 動画がYouTube側で実際に「Shorts」として認識されるかは動画自体の縦横比・尺（3分以内目安）に依存する。タイトル末尾に`#Shorts`を付与しているが、これはあくまで補助シグナル。

## 5. 追記（2026-07-08）: 承認フローを追加（当初は即時公開だった）

初版は`privacyStatus: public`で即時公開する設計だったが、社長から「動画もX投稿と同じ承認フォーマットでDiscordに流してほしい」と要望があり修正。

調査の過程で判明した重要な事実: そのX投稿承認フォーマット（マモル審査・ミオ評価・`!承認`/`!却下`・30分自動キャンセル）は、**本ドキュメントが対象とするGASではなく、別の独立したPython + GitHub Actionsシステム**（`kcs-automation`リポの`scripts/hal_post.py`・`scripts/common/mamoru.py`・`scripts/common/nana.py`・`bot/discord_bot.py`、Render常駐のDiscord Bot）から出力されているものだった。`nana.py`はHALのX投稿用にVeoで5秒動画も生成できるが、これは本パイプライン（YouTube Shorts用の長尺動画）とは別物。

このためGAS側に**独自の承認機構**を実装した（Python側の`pending_approvals`/GitHub Actions dispatchとは連携していない、完全に別系統）。

**変更後のフロー:**
1. `HAL_Shorts_Ready`に動画が置かれる
2. `processHALShortsQueue()`が検知 → 即座に`HAL_Shorts_Pending`へ退避（同一ファイルへの重複承認依頼を防止）→ 承認IDを発行しスクリプトプロパティ`YOUTUBE_PENDING_<ID>`に保存 → Discordに承認プレビュー送信（Driveプレビューリンク付き、実ファイルは添付しない）
3. `!Shorts承認 <ID>` / `!Shorts却下 <ID>`（`handleBotCommand()`に追加）で承認・却下。承認時のみ実際に`uploadHALShort()`を呼びYouTubeへ公開、`HAL_Shorts_Used`へ移動。却下時は`HAL_Shorts_Rejected`へ移動
4. 30分無応答の場合は次回トリガー時（`expireHALShortsApprovals()`、`processHALShortsQueue()`冒頭で毎回実行）に自動キャンセルし`HAL_Shorts_Rejected`へ移動

**コマンド名について:** Python側のDiscord Bot（discord_bot.py）が同じDiscordサーバーで`!承認`/`!却下`を常時リッスンしているため、衝突を避けて`!Shorts承認`/`!Shorts却下`という別名にした。

**新規フォルダ:** `HAL_Shorts_Pending`（承認待ち退避）、`HAL_Shorts_Rejected`（却下・タイムアウト後の退避先）が追加された。

## 6. ロールバック手順

1. `clasp pull`
2. `09_hal_youtube_shorts.js`を削除、`GAS_KCS合同会社_Backend.js`内の関連追記（`halShorts`データ取得、YouTube Shorts実績セクション、`[VIDEO_CONCEPT]`出力ルール、ユキの登場、`saveHALVideoConceptFromBriefing`呼び出し、`!Shorts承認`/`!Shorts却下`コマンド分岐、`processHALShortsQueue`トリガー登録・メニュー項目）を削除、`appsscript.json`の`enabledAdvancedServices`を削除
3. `clasp push -f`
