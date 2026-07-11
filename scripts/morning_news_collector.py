"""
WF-09: 朝のニュース収集（毎朝 05:50 JST、HAL/すなくんの初回投稿より前）
Claude Web Search（サーバー側実行ツール）でHAL/すなくん向けニュース候補と
東京の天気を調査し、GAS webhook経由でKCS-Database-JPのPending_Newsシートに
投入する。

移植元: Claude Codeデスクトップのスケジュールタスク kcs-morning-news-collector。
デスクトップアプリの起動有無に依存せず動くよう、GitHub Actionsへ移植した
（2026-07-11、社長指示）。GAS webhook（add_pending_news）は変更していない。
"""
import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import anthropic
import requests

from scripts.common.env_clean import clean_env
from scripts.common.discord_notify import notify

GAS_WEBHOOK_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbxmVS3EyDiT8KtX4r10SIE8eDu3ri_7aRbYXR4kFpEqSKuQnDsJLlTO1HV6p7RW1mTF/exec"
)

RESEARCH_PROMPT = """あなたはKCS合同会社のHAL・すなくん向け朝の情報収集エージェントです。
ウェブ検索を使って、以下の3項目を調査してください。

【1. HAL向け】
HALは21歳、台湾人の父と日本人の母を持つハーフ、東京/代官山拠点、K-POP(特にLE SSERAFIM)の
熱狂的ファン、密かにサッカー(日本代表/推しクラブ)ファン。以下のジャンルから最新・ポジティブ・
タイムリーな話題を1つ検索してください:
K-POPニュース(特にLE SSERAFIM) / ファッション・コーデ流行 / 東京・代官山のカフェやグルメ話題 /
台湾関連の心温まるニュース / サッカーニュース(日本代表や注目クラブの試合) /
一般的な心温まる社会ニュース。
最も良いもの(タイムリーで前向き)を1つ選び、HALの声(おっとり、K-POPの話になると早口オタク化)で
SNS投稿にどう反応できるか1-2文の日本語で"hal_angle"に書いてください。

【2. すなくん向け】
すなくんは26歳、ガジェット愛好家のアフィリエイト系ペルソナ(ハイテンション・カジュアル)です。
新製品/PCパーツ/タイムセール情報/家電トレンドから最新・ポジティブな話題を1つ検索し、
アフィリエイト投稿の切り口を1-2文の日本語で"sunakun_angle"に書いてください。

【3. 天気(共通)】
東京の本日の天気予報(最高/最低気温、晴れ/雨/曇り/猛暑/寒波などの状況、注意報があれば)を検索して
ください。両方のペルソナ向けに天気を話題にした投稿の始め方をそれぞれ1-2文の日本語で
"hal_angle"と"sunakun_angle"の両方に書いてください。

【ルール】
- HAL・すなくんの項目は、政治・悲劇・論争的・ネガティブな話題は使わないでください。良い話題が
  見つからなければその項目は省略してよい(行を作らない)。天気の行は常に含めること。
- 出力は必ず以下のJSON配列のみ(説明文・前置き・後書き不要)。最大3件(HAL/すなくん/天気):
[
  {"category":"ジャンル(例:K-POP/ファッション/ガジェット/天気)","title":"タイトル(日本語)",
   "url":"情報源URL(無ければ空文字)","positivity":0-100の整数,
   "hal_angle":"HAL向け文言、対象外なら空文字","sunakun_angle":"すなくん向け文言、対象外なら空文字"}
]
天気の行は"category":"天気"、hal_angle/sunakun_angle両方に記入すること。"""


def _extract_text(response) -> str:
    parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    return "".join(parts).strip()


def research_news() -> list[dict]:
    client = anthropic.Anthropic(api_key=clean_env("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=2048,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}],
        messages=[{"role": "user", "content": RESEARCH_PROMPT}],
    )
    text = _extract_text(response)
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start < 0 or end < 0:
        raise ValueError(f"JSON配列が見つかりません: {text[:300]}")
    rows = json.loads(cleaned[start:end + 1])
    if not isinstance(rows, list):
        raise ValueError(f"JSON配列ではありません: {text[:300]}")
    return rows


def submit_row(row: dict) -> dict:
    payload = {
        "action": "add_pending_news",
        "category": str(row.get("category", "")),
        "title": str(row.get("title", "")),
        "url": str(row.get("url", "")),
        "positivity": int(row.get("positivity") or 50),
        "hal_angle": str(row.get("hal_angle", "")),
        "sunakun_angle": str(row.get("sunakun_angle", "")),
    }
    r = requests.post(GAS_WEBHOOK_URL, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def run():
    try:
        rows = research_news()
    except Exception as e:
        notify(f"⚠️ **朝のニュース収集 失敗**\n調査エラー: {type(e).__name__}: {str(e)[:300]}")
        print(f"[morning_news_collector] research failed: {e}")
        sys.exit(0)

    results = []
    for row in rows[:3]:
        try:
            res = submit_row(row)
            ok = bool(res.get("ok"))
        except Exception as e:
            ok = False
            res = {"error": str(e)[:200]}
        results.append((row.get("category", "?"), str(row.get("title", ""))[:40], ok, res))

    if not results:
        notify("⚠️ **朝のニュース収集**: 調査結果0件（該当ニュースなし）")
        print("[morning_news_collector] no rows returned")
        return

    lines = [f"{'✅' if ok else '❌'} {cat}: {title}" for cat, title, ok, _ in results]
    summary = "📰 **朝のニュース収集完了**\n" + "\n".join(lines)
    failed = [str(res) for _, _, ok, res in results if not ok]
    if failed:
        summary += "\n\n⚠️ 失敗詳細: " + " / ".join(failed)[:400]
    notify(summary)
    print(summary)


if __name__ == "__main__":
    run()
