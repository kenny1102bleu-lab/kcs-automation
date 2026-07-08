# Rationale: もるの動画関連スキルを削除、YouTube Shorts制作はユキに一本化

- **日付:** 2026-07-08
- **対象:** `scripts/moru_shorts.py`（削除）、`.github/workflows/08_moru_shorts.yml`（削除）
- **社長指示:** 「もるはエージェント！スタッフじゃない！」「ユキに引継ぎさせてもるは何もしない。動画等のスキルを削除して」

## 経緯

[[project-hal-youtube-shorts]]（GAS側のYouTube Shorts自動アップロード承認パイプライン）を作る過程で、朝礼（`morningBriefing()`）の動画企画の受け手を「ユキ」から「もる」に変更しようとした（`カスタムスタッフ`シートの正式レコードで、もるが「YouTube Shorts動画パイプライン担当」と登録されていたため）。

しかし社長から「もるはDiscordの会議に参加する"スタッフ"ではなく、GitHub Actionsで自律実行される"エージェント"だ」と訂正が入った。実際、`scripts/moru_shorts.py`は`08_moru_shorts.yml`（火木土17:00 or `workflow_dispatch`）でCronから直接叩かれる設計で、Discordチャットから指示を受け取る仕組みは無かった。GAS朝礼の`[VIDEO_CONCEPT]`タグ経由でもるに"指示を渡す"という設計は、実際のもるの動き方と整合していなかった。

このため、YouTube Shorts制作は「ユキ」（GAS朝礼で企画指示を受け取り、Google Driveの`HAL_Shorts_Ready`に動画を配置する人格）に一本化し、もるの動画関連スキル・パイプラインは削除した。

## 削除した内容

- `scripts/moru_shorts.py` — もるの台本生成（Claude）→ WF-07(HyperFrames)へのdispatchロジック
- `.github/workflows/08_moru_shorts.yml` — 上記を火木土17:00に自動実行していたワークフロー

## 削除しなかったもの

- `.github/workflows/07_video_render.yml`（WF-07 HyperFramesレンダラー）— HAL/すなくんの動画生成でも共有される汎用インフラのため維持。`moru_shorts`テンプレート名の参照は残っているが、呼び出し元が無くなったので実質使われなくなる。

## 残課題（社長対応が必要）

`KCS-Database-JP`スプレッドシートの`カスタムスタッフ`シート、id=`moru`の行に以下が残っている:
- 役職: 「システム犬（YouTube Shorts動画パイプライン）」
- スキル: `YouTube, 動画生成, ffmpeg, デプロイ`
- システムプロンプト: 「担当はYouTube Shortsの自動生成・デプロイ。動画生成・ffmpegパイプラインの状態を簡潔に報告する。」

これはコードからは削除できない**データ**（スプレッドシートの1セル編集）。GAS Webアプリ経由で書き換えるスクリプトを用意することもできるが、1回きりの単純なセル編集なので、スプレッドシートを直接開いて該当行を編集する方が早い。もし社長の代わりにスクリプトで書き換えてほしい場合は別途対応する。

## ロールバック手順

1. `git log --oneline -- scripts/moru_shorts.py .github/workflows/08_moru_shorts.yml` で削除前のコミットを確認
2. `git checkout <直前のコミット> -- scripts/moru_shorts.py .github/workflows/08_moru_shorts.yml`
3. コミット
