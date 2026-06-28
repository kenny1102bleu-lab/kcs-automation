"""
WF-XX: アカリ トレンド要約動画（HyperFrames経由）

アカリはトレンドアナリスト。news_pool等から集めたトレンドをカード型動画にして
Discord/YouTube/Xに展開する。
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from scripts.common.claude_client import call_claude
from scripts.common.discord_notify import notify
from scripts.common.hyperframes_runner import dispatch_render


AKARI_PROMPT = """あなたはKCS合同会社のトレンドアナリスト「アカリ」です。
本日のトレンドを15秒の横型カード動画用に要約します。

【出力フォーマット】必ず以下のJSONのみ:
{
  "trend_title": "今日の最大トレンド見出し（30文字以内）",
  "summary": "1〜2文の要約説明（80文字以内）",
  "rank1": "ランキング1位（20文字以内）",
  "rank2": "ランキング2位（20文字以内）",
  "rank3": "ランキング3位（20文字以内）"
}
"""


def _safe_parse(text: str) -> dict | None:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:].strip()
    s, e = t.find("{"), t.rfind("}")
    if s < 0 or e <= s:
        return None
    try:
        return json.loads(t[s:e + 1])
    except Exception:
        return None


def run():
    topic = os.environ.get("POST_THEME", "").strip() or "本日の開発・AI・SNS関連の主要トレンド"
    user_message = f"題材: {topic}\nトレンド動画用のJSONを生成してください。"

    raw = call_claude(AKARI_PROMPT, user_message, model="claude-sonnet-4-6")
    parsed = _safe_parse(raw)
    if not parsed:
        notify(f"⚠️ アカリ動画台本生成失敗\nRaw: {raw[:300]}")
        return

    today = datetime.now().strftime("%Y.%m.%d")
    variables = {
        "duration": 15,
        "date_label": today,
        "trend_title": str(parsed.get("trend_title", ""))[:30],
        "summary": str(parsed.get("summary", ""))[:80],
        "rank1": str(parsed.get("rank1", ""))[:20],
        "rank2": str(parsed.get("rank2", ""))[:20],
        "rank3": str(parsed.get("rank3", ""))[:20],
    }
    if not variables["trend_title"]:
        notify("⚠️ アカリ動画: trend_titleが空。スキップ")
        return

    workflow_id = uuid.uuid4().hex[:8]
    result = dispatch_render(
        template_name="akari_trend",
        variables=variables,
        staff="akari",
        source=f"akari_trend_video.py:{workflow_id}",
    )

    if result.get("ok"):
        notify(
            f"📊 **アカリ トレンド動画 レンダリング開始** ({workflow_id})\n"
            f"📅 {today}\n"
            f"見出し: {variables['trend_title']}\n"
            f"#1 {variables['rank1']} / #2 {variables['rank2']} / #3 {variables['rank3']}\n"
            f"→ WF-07 完了後にMP4をDiscord通知"
        )
    else:
        notify(f"❌ アカリ動画dispatch失敗: {result.get('error') or result.get('message')}")

    print(f"[akari_trend_video] dispatched workflow_id={workflow_id} ok={result.get('ok')}")


if __name__ == "__main__":
    run()
