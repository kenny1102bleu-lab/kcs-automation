"""
WF-09: アカリ トレンド要約動画（HyperFrames経由）

アカリはプロデューサー。Cowork(情報収集エージェント)がPending_Newsに集めた
ニュース/トレンドを読み、エージェントごと(HAL/すなくん)にその声で台本を
書き、実制作は「レン」スタッフに引き渡す。台本を書くところまでがアカリの
仕事で、レンダリングそのものはレンの担当。
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
from scripts.common.ren import produce_video
from scripts.common.env_clean import clean_env

import requests

AKARI_PROMPT = """あなたはKCS合同会社のプロデューサー「アカリ」です。
渡されたニュース1件をもとに、指定されたエージェント本人の口調で
15秒の縦型カード動画の台本を作成します。

【出力フォーマット】必ず以下のJSONのみ:
{
  "trend_title": "見出し（30文字以内、そのエージェントの口調で）",
  "summary": "1〜2文の紹介文（80文字以内、そのエージェントの口調で）",
  "rank1": "ポイント1（20文字以内）",
  "rank2": "ポイント2（20文字以内）",
  "rank3": "ポイント3（20文字以内）"
}
"""

AGENT_VOICE = {
    "HAL": "HAL（21歳、日台ハーフの新人モデル。おっとり天然、K-POP好きが高じると早口になる。サッカー観戦も好き）",
    "SUNAKUN": "すなくん（26歳、ガジェットオタク男子。テンション高め、フレンドリー）",
}


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


def _fetch_pending_news(limit: int = 5) -> list[dict]:
    url = os.environ.get("GAS_WEBHOOK_URL", "")
    if not url:
        notify("⚠️ アカリ: GAS_WEBHOOK_URL未設定のためPending_Newsを取得できません")
        return []
    try:
        r = requests.post(url, json={"action": "get_pending_news", "limit": limit}, timeout=15, allow_redirects=False)
        # このエンドポイントは302で本体を返すため、リダイレクト先を追う
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
            r = requests.get(r.headers["Location"], timeout=15)
        data = r.json()
        return data.get("items", []) if data.get("ok") else []
    except Exception as e:
        notify(f"⚠️ アカリ: Pending_News取得失敗: {e}")
        return []


def _write_script(item: dict, agent: str) -> dict | None:
    angle = item.get("hal_angle") if agent == "HAL" else item.get("sunakun_angle")
    user_message = (
        f"エージェント: {AGENT_VOICE[agent]}\n"
        f"ニュース: {item.get('title')}\n"
        f"カテゴリ: {item.get('category')}\n"
        f"このエージェントとしての切り口メモ: {angle}\n"
        "上記を踏まえて動画台本用のJSONを生成してください。"
    )
    raw = call_claude(AKARI_PROMPT, user_message, model="claude-sonnet-4-6")
    return _safe_parse(raw)


def run():
    items = _fetch_pending_news(limit=5)
    if not items:
        notify("⚠️ アカリ: Pending_Newsに使える候補がありませんでした。動画生成をスキップします。")
        return

    today = datetime.now().strftime("%Y.%m.%d")
    produced = 0

    for agent, angle_key in (("HAL", "hal_angle"), ("SUNAKUN", "sunakun_angle")):
        item = next((i for i in items if str(i.get(angle_key) or "").strip()), None)
        if not item:
            notify(f"ℹ️ アカリ: {agent}向けの候補がPending_Newsにありませんでした。スキップします。")
            continue

        parsed = _write_script(item, agent)
        if not parsed or not str(parsed.get("trend_title", "")).strip():
            notify(f"⚠️ アカリ: {agent}向け台本生成に失敗しました。\n元ニュース: {item.get('title')}")
            continue

        variables = {
            "duration": 15,
            "date_label": today,
            "trend_title": str(parsed.get("trend_title", ""))[:30],
            "summary": str(parsed.get("summary", ""))[:80],
            "rank1": str(parsed.get("rank1", ""))[:20],
            "rank2": str(parsed.get("rank2", ""))[:20],
            "rank3": str(parsed.get("rank3", ""))[:20],
        }
        workflow_id = uuid.uuid4().hex[:8]
        produce_video(
            template_name="akari_trend",
            variables=variables,
            agent=agent,
            workflow_id=workflow_id,
            source=f"akari_trend_video.py:{workflow_id}",
        )
        produced += 1

    print(f"[akari_trend_video] produced={produced}")


if __name__ == "__main__":
    run()
