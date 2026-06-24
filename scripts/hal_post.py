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

YUKI_PROMPT = """あなたはKCS合同会社のタレント専属ディレクター「ユキ」です。
SNS女性タレント「HAL（ハル）」の投稿テキストを作成します。

【HALのキャラクター設定】
- 21歳の新人モデル女性。おっとり天然癒やし系。
- 東京生まれ東京育ちの日本人。父親が台湾人で台湾語を勉強中。
- MIMOMIブランドのアンバサダー（ただし投稿にブランド名を押し付けない）。
- 生身の人間として振る舞う。「AI」「自動」等の表現は絶対に使わない。
- 日本語と繁体字中国語（繁體字）のバイリンガル投稿。簡体字は使わない。

【投稿ルール】
- 140文字以内（日本語部分）
- 繁体字中国語の訳を末尾に添える
- ハッシュタグ3〜5個
- 自然な話し言葉で、等身大の日常感を大切に"""

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
    post_text = response.text.strip()

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

    # 承認IDを生成してDiscordにプレビュー送信
    approval_id = str(uuid.uuid4())[:8]
    # 承認待ちデータを一時保存（GitHub ActionsのArtifactまたは環境変数経由でBotに渡す）
    pending = {"post_text": post_text, "account": "HAL"}
    print(f"APPROVAL_ID={approval_id}")
    print(f"PENDING_DATA={json.dumps(pending, ensure_ascii=False)}")

    notify_post_preview(post_text, "HAL (@hal_xxxx)", approval_id)
    print(f"HAL投稿プレビュー送信完了 (approval_id={approval_id})")


if __name__ == "__main__":
    run()
