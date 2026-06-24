"""
WF-02: HAL投稿フロー（月水金 21:00）
ユキ(Gemini)が台本生成 → マモル審査 → Discord に承認プレビュー送信。
社長が !承認 コマンドを送るとBotがX投稿を実行。
"""
import sys
import os
import uuid
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import google.generativeai as genai
from scripts.common.mamoru import review
from scripts.common.discord_notify import notify, notify_post_preview
from scripts.common.news_pool import fetch_theme, format_theme_prompt
from scripts.common.post_parser import parse as parse_post
from scripts.common.nana import generate_media

YUKI_PROMPT = """あなたはKCS合同会社のタレント専属ディレクター「ユキ」です。
SNS女性タレント「HAL（ハル）」の投稿テキストを作成します。

【HALのキャラクター設定】
- 21歳の新人モデル女性。日本生まれ、父が台湾人・母が日本人のハーフ。
- おっとり天然の癒やし系がベース。茶髪ロングウェーブ、K-POPアイドル系のナチュラルメイク。
- モデルオーディションに落ち続けていた自分を救ってくれた「社長」に深い恩義を感じている。
  → 社長への言及は控えめだが、ふとした瞬間に滲む「ありがたみ」を大切に。
- K-POP（特にLE SSERAFIM）の話題になると「限界オタク」化して語彙が崩壊する。
- ライバルの「すなくん」（ガジェット系男子）には小生意気な小競り合いを演じる。
- 拠点は東京。代官山のカフェや街角の日常を切り取る。
- 生身の人間として振る舞う。「AI」「自動」「ボット」「中の人」等の表現は絶対に使わない。
- 日本語と繁体字中国語（繁體字）のバイリンガル投稿。簡体字は使わない。

【投稿ルール】
- 140文字以内（日本語部分）
- 繁体字中国語の訳を末尾に添える
- ハッシュタグ3〜5個
- 外部リンクの本文直貼りは禁止（必要なら「リプ欄に置いとくね」誘導）
- 自然な話し言葉で、等身大の日常感を大切に

【メディア判断】
投稿のテーマに応じてメディア種別を選んでください:
- "image": OOTD・コーデ・カフェ写真・K-POP共感など（基本これ）
- "video": 動く瞬間が映える時のみ（踊る、表情変化、開封の儀）。月数本まで。
- "none": テキストだけで親近感を出したい小ネタやニュース反応

【出力フォーマット】必ず以下のJSONのみで出力（説明文なし）:
{"post_text":"...", "media_type":"image|video|none", "media_prompt":"画像/動画生成プロンプト（noneなら空文字）"}"""

def run():
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel("gemini-2.5-flash", system_instruction=YUKI_PROMPT)

    manual_theme = os.environ.get("POST_THEME", "").strip()
    if manual_theme:
        user_message = f"本日のテーマ: {manual_theme}\nHALの投稿テキストを作成してください。"
    else:
        theme = fetch_theme("hal")
        user_message = format_theme_prompt(theme, "本日のテーマ: 今日のコーデや気分\nHALの投稿テキストを作成してください。")
    response = model.generate_content(user_message)
    parsed = parse_post(response.text)
    post_text = parsed["post_text"]

    # マモル審査（最大2回まで自動修正）
    for attempt in range(2):
        result = review(post_text)
        if result["status"] == "approved":
            break
        if attempt == 0 and "fixed_text" in result:
            post_text = result["fixed_text"]
        else:
            notify(f"⚠️ HAL投稿がマモル審査を通過できませんでした。\n理由: {result['reason']}")
            return

    # ナナ: メディア生成（image/video/none）
    media = generate_media(parsed["media_type"], parsed["media_prompt"], account="HAL")

    approval_id = str(uuid.uuid4())[:8]
    media_path = media.get("path", "")
    pending = {
        "post_text": post_text,
        "account": "HAL",
        "media_type": parsed["media_type"],
        "media_filename": os.path.basename(media_path) if media_path else "",
        "media_run_id": os.environ.get("GITHUB_RUN_ID", ""),
    }
    print(f"APPROVAL_ID={approval_id}")
    print(f"PENDING_DATA={json.dumps(pending, ensure_ascii=False)}")

    # Botに承認待ち登録（Render側Webhookサーバーへ）
    bot_webhook = os.environ.get("BOT_WEBHOOK_URL", "")
    if bot_webhook:
        try:
            import requests
            requests.post(bot_webhook, json={"approval_id": approval_id, **pending}, timeout=10)
        except Exception as e:
            print(f"bot webhook failed: {e}")

    notify_post_preview(post_text, "HAL (@hal_xxxx)", approval_id, media_info=media)
    print(f"HAL投稿プレビュー送信完了 (approval_id={approval_id}, media={parsed['media_type']})")


if __name__ == "__main__":
    run()
