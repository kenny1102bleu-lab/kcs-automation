# KCSAPP × HyperFrames 動画生成統合ガイド

各スタッフ（HAL / すなくん / もる）が共通基盤で動画を量産できる仕組み。

## アーキテクチャ

```
┌────────────────────────┐    repository_dispatch        ┌─────────────────────────┐
│ GAS Backend.js         │ ─────────────────────────────▶│ WF-07 video_render       │
│  requestVideoRender()  │                                │  (Node 22 + FFmpeg)      │
└────────────────────────┘                                │                          │
                                                          │  scripts/common/         │
┌────────────────────────┐    dispatch_render()           │   hyperframes_runner.py  │
│ Python staff scripts    │ ─────────────────────────────▶│                          │
│  hal_post.py            │                                │  ┌──────────────────┐   │
│  sunakun_post.py        │                                │  │ video_templates/  │   │
│  moru_shorts.py         │                                │  │  hal_x_post/      │   │
└────────────────────────┘                                │  │  suna_short/      │   │
                                                          │  │  moru_shorts/     │   │
┌────────────────────────┐    workflow_call               │  └──────────────────┘   │
│ Other GitHub Workflows  │ ─────────────────────────────▶│                          │
└────────────────────────┘                                └──────────────┬───────────┘
                                                                          │
                                                                          ▼
                                                                  ┌──────────────┐
                                                                  │ Artifact     │
                                                                  │ + Discord通知 │
                                                                  └──────────────┘
```

## 各スタッフの呼び出し方

### HAL（hal_post.py から動画化したい時）
```python
from scripts.common.hyperframes_runner import dispatch_render
dispatch_render(
    template_name="hal_x_post",
    variables={
        "duration": 10,
        "caption_duration": 4.5,
        "caption_ja": "おはよう、今日もよろしくね…✨",
        "caption_tc": "早安～今天也請多多指教喔…✨",
    },
    staff="hal",
    source="hal_post.py",
)
```

### すなくん（sunakun_post.py から）
```python
dispatch_render(
    template_name="suna_short",
    variables={
        "duration": 12,
        "hook_line": "これ知らないと",
        "hook_accent": "1万円損するかも",
        "badge": "コスパ◎",
        "product_name": "Anker PowerCore 10000",
        "price": "2,990",
        "price_unit": "円〜",
        "cta_text": "「リンク希望」とコメントで！",
    },
    staff="suna",
    source="sunakun_post.py",
)
```

### もる（独立スクリプト scripts/moru_shorts.py）
- WF-08 が JST 火/木/土 17:00 cron 起動
- Claude Sonnet 4.6 で台本生成 → HyperFrames で縦動画レンダリング

### GAS から動画依頼
```javascript
const result = requestVideoRender(
  'hal_x_post',                               // テンプレ
  {duration: 10, caption_ja: '...', ...},     // 変数
  'hal',                                      // staff
  'morning_briefing'                          // source
);
```

または Web App POST:
```bash
curl -L -X POST https://script.google.com/macros/s/.../exec \
  -H "Content-Type: application/json" \
  -d '{"action":"request_video_render","template":"hal_x_post","variables":{...},"staff":"hal"}'
```

## 必要なSecrets

| 名前 | 用途 | 場所 |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude台本生成 | GitHub Secrets |
| `DISCORD_WEBHOOK_URL` | 通知 | GitHub Secrets |
| `WORKFLOW_DISPATCH_TOKEN` | scripts→WF-07 dispatch用PAT（workflow scope必須） | GitHub Secrets |
| `GITHUB_TOKEN` | GAS→WF-07 dispatch用PAT（workflow scope必須） | GAS設定シート |
| `GITHUB_ACTIONS_REPO` | リポジトリ名（既定: kcs-automation） | GAS設定シート |
| `GITHUB_OWNER` | オーナー名（既定: kenny1102bleu-lab） | GAS設定シート |

## レンダリング所要時間（実測ベース）

| 動画長 | 解像度 | 推定時間 |
|---|---|---|
| 10秒 | 1920x1080 | 約30秒 |
| 15秒 | 1080x1920 | 約45秒 |
| 60秒 | 1080x1920 | 約3分 |
| 3分 | 1920x1080 | 約9分 |

GitHub Actions Free tier（月2000分）で十分カバー可能。

## トラブルシュート

### dispatch_render が ok=False
- `GITHUB_TOKEN` (workflow scope) を設定したか確認
- `GITHUB_REPOSITORY` env が `owner/repo` 形式か確認

### WF-07 が起動しない
- repository_dispatch event_type が `render_video` か
- リポジトリの Actions が有効化されているか
- Token に workflow scope があるか（read:repo だけだと不可）

### レンダリング失敗
- WF-07 のログを `gh run view <id> --log-failed` で確認
- テンプレ側 `{{VAR}}` の未置換が原因のことが多い
- メディアパスが正しいか（assets/main.mp4 配置）
