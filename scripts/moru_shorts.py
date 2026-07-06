"""
WF-XX: もる YouTube Shorts 生成（HyperFrames経由）

「もる」はYouTube Shorts動画生成パイプライン担当のシステム・ペルソナ。
HyperFramesの moru_shorts テンプレを使って縦型解説動画を量産する。

このスクリプトは「題材を受け取る → ナナで台本/メディア準備 → HyperFramesで仕上げ」を担当。
出力はWF-07経由でレンダリングし、結果はDiscordに通知。

呼び出し方:
  python scripts/moru_shorts.py                 # 自動テーマ
  POST_THEME='Claude新機能解説' python scripts/moru_shorts.py
"""
from __future__ import annotations

import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# stdout/stderr を強制 UTF-8（GitHub Actions ASCII fallback 対策）
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from scripts.common.claude_client import call_claude
from scripts.common.discord_notify import notify
from scripts.common.hyperframes_runner import dispatch_render
from scripts.common.ng_patterns import scan as ng_scan


MORU_PROMPT = """あなたはKCS合同会社のYouTube Shorts専属クリエイター「もる」です。
60秒前後の縦型解説動画の台本を生成します。

【もるのキャラ・スタイル】
- システム・ドッグ（犬モチーフのシステム化ペルソナ）
- 真面目で論理的、専門用語を平易に噛み砕いて伝える
- 短くキレのある導入→3ポイント解説→締めの構成が得意
- 視聴者を「視聴者さん」と呼ぶ
- 「AI」「自動」「ボット」等の自己言及は禁止（生身のクリエイターとして振る舞う）

【出力フォーマット】必ず以下のJSONのみで出力（説明文なし）:
{
  "title": "動画タイトル（30文字以内、強いフック）",
  "subtitle": "サブタイトル（50文字以内、何が学べるか）",
  "narration": "本文ナレーション（120文字以内、テロップに載る要点）",
  "duration": 15
}
"""


def _safe_parse(text: str) -> dict | None:
    """JSON抽出（モデルが余計な前後文字を付けても拾う）"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:].strip()
    s = t.find("{")
    e = t.rfind("}")
    if s < 0 or e <= s:
        return None
    try:
        return json.loads(t[s:e + 1])
    except Exception:
        return None


def run():
    manual_theme = os.environ.get("POST_THEME", "").strip()
    base_theme = manual_theme or "今週の開発トピックから視聴者さんに伝えたい話題"
    user_message = f"今回の題材: {base_theme}\n上記についてYouTube Shortsの台本JSONを作ってください。"

    raw = call_claude(MORU_PROMPT, user_message, model="claude-sonnet-4-6")
    parsed = _safe_parse(raw)
    if not parsed:
        notify(f"⚠️ もるScript生成失敗（JSON抽出不可）\nRaw: {raw[:300]}")
        return

    title    = str(parsed.get("title", ""))[:30].strip()
    subtitle = str(parsed.get("subtitle", ""))[:50].strip()
    narration = str(parsed.get("narration", ""))[:120].strip()
    duration = int(parsed.get("duration", 15))
    duration = max(8, min(60, duration))

    if not title or not narration:
        notify("⚠️ もるScript生成: title/narration が空。スキップ")
        return

    # NGパターン最終監査（HAL/Sunaと同じ二重チェック）
    for label, text in [("title", title), ("subtitle", subtitle), ("narration", narration)]:
        hit = ng_scan(text)
        if hit:
            notify(f"⚠️ もるNG監査拒否: {label}に `{hit[1]}`\n{text[:200]}")
            return

    # HyperFrames（WF-07）に dispatch
    workflow_id = uuid.uuid4().hex[:8]
    variables = {
        "duration": duration,
        "title": title,
        "subtitle": subtitle,
        "narration": narration,
        # media_path は未指定（テンプレ側で assets/main.mp4 が無い場合は背景は黒）
        # 本番では nana.generate_media() で raw 動画を作って Drive/Artifact 経由で渡す設計に拡張
    }
    result = dispatch_render(
        template_name="moru_shorts",
        variables=variables,
        staff="moru",
        source=f"moru_shorts.py:{workflow_id}",
    )

    if result.get("ok"):
        notify(
            f"🎬 **もるYouTube Shorts レンダリング開始** ({workflow_id})\n"
            f"題材: {base_theme}\n"
            f"タイトル: {title}\n"
            f"サブタイトル: {subtitle}\n"
            f"ナレーション: {narration[:80]}{'…' if len(narration) > 80 else ''}\n"
            f"長さ: {duration}秒\n"
            f"→ WF-07 がレンダリング後、完成MP4をDiscord通知します"
        )
    else:
        notify(
            f"❌ もる動画dispatch失敗 ({workflow_id})\n"
            f"理由: {result.get('error') or result.get('message') or 'unknown'}\n"
            f"題材: {base_theme}"
        )

    print(f"[moru_shorts] dispatched template=moru_shorts workflow_id={workflow_id} ok={result.get('ok')}")


if __name__ == "__main__":
    run()
