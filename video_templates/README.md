# video_templates/

HyperFrames テンプレ集（KCSAPP 各スタッフ共通）。`scripts/common/hyperframes_runner.py` から呼び出される。

## テンプレ一覧

| ディレクトリ | 用途 | 解像度 | 主要変数 |
|---|---|---|---|
| `hal_x_post/` | HAL X 横型投稿（日中バイリンガル字幕） | 1920×1080 | `duration`, `caption_duration`, `caption_ja`, `caption_tc`, `media_path` |
| `suna_short/` | すなくん 縦型ガジェット紹介 | 1080×1920 | `duration`, `hook_line`, `hook_accent`, `badge`, `product_name`, `price`, `price_unit`, `cta_text`, `media_path` |
| `moru_shorts/` | もる YouTube Shorts 解説型 | 1080×1920 | `duration`, `title`, `subtitle`, `narration`, `media_path` |
| `akari_trend/` | アカリ トレンド要約カード | 1920×1080 | `duration`, `date_label`, `trend_title`, `summary`, `rank1`, `rank2`, `rank3` |

## テンプレ仕様

各テンプレディレクトリには以下を配置：
- `template.html` — `{{VAR}}` プレースホルダー入りHTML（描画時に runner が置換）
- `hyperframes.json` — HyperFrames設定
- `package.json` — `npx hyperframes render` を呼ぶscripts

## 呼び出し方

### CLI（WF-07 内から）
```bash
echo '{"duration":10,"caption_duration":4.5,"caption_ja":"おはよう","caption_tc":"早安","media_path":"/abs/path/to/video.mp4"}' \
  | python -m scripts.common.hyperframes_runner --template hal_x_post --output /tmp/out.mp4
```

### Python（各スタッフ post スクリプトから）
```python
from scripts.common.hyperframes_runner import render_local
out = render_local("hal_x_post", {
    "duration": 10,
    "caption_duration": 4.5,
    "caption_ja": "おはよう、今日もよろしくね…✨",
    "caption_tc": "早安～今天也請多多指教喔…✨",
    "media_path": "/tmp/hal_raw.mp4",
}, "/tmp/hal_polished.mp4")
```

### GAS / 他workflow（dispatch経由）
```python
from scripts.common.hyperframes_runner import dispatch_render
dispatch_render("moru_shorts", {...}, staff="moru", source="WF-XX")
```

GAS から呼ぶ場合は GitHub repository_dispatch (event_type=`render_video`) を叩く。
Backend.js の `requestVideoRender(template, variables, staff)` 関数を使用。

## メディア注入

`variables.media_path` に絶対パスを渡すと、`assets/main.mp4` (or `.png`) として
work dir 内にコピーされる。テンプレ側は固定パス `assets/main.mp4` を参照する設計。

## 新規テンプレ追加手順

1. `video_templates/<name>/` ディレクトリ作成
2. `template.html` を書く（既存テンプレを参考に `{{VAR}}` で変数化）
3. `hyperframes.json` と `package.json` を `_shared/` からコピー
4. 必要なら本READMEに追記
