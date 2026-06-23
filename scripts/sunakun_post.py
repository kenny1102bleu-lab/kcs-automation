"""
WF-03: すなくん投稿フロー（毎日 12:00 / 19:00 / 22:00）
タクミ(Gemini)がアフィリエイト文生成 → マモル審査 → Discord 承認プレビュー送信。
"""
import sys
import os
import uuid
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import google.generativeai as genai
from scripts.common.mamoru import review
from scripts.common.discord_notify import notify, notify_post_preview

TAKUMI_PROMPT = """あなたはKCS合同会社のアフィリエイト担当「タクミ」です。
ガジェット系アフィリエイトアカウント「すなくん」の投稿テキストを作成します。

【すなくんのキャラクター設定】
- ガジェット好きな20代男性。テンション高め、フレンドリー。
- 楽天・Amazonのアフィリエイトリンクを自然に紹介する。
- 「これマジでいい！」「コスパ最強」など口語的な表現が得意。

【投稿ルール】
- 140文字以内
- アフィリエイトリンクのプレースホルダー [LINK] を末尾に入れる
- ハッシュタグ3〜5個
- 「広告」「PR」の表記を必ず含める"""

def run():
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel("gemini-2.5-flash", system_instruction=TAKUMI_PROMPT)

    theme = os.environ.get("POST_THEME", "最新のコスパ最強ガジェット")
    response = model.generate_content(f"本日のテーマ: {theme}\nすなくんの投稿テキストを作成してください。")
    post_text = response.text.strip()

    for attempt in range(2):
        result = review(post_text)
        if result["status"] == "approved":
            break
        if attempt == 0 and "fixed_text" in result:
            post_text = result["fixed_text"]
        else:
            notify(f"⚠️ すなくん投稿がマモル審査を通過できませんでした。\n理由: {result['reason']}")
            return

    approval_id = str(uuid.uuid4())[:8]
    pending = {"post_text": post_text, "account": "SUNAKUN"}
    print(f"APPROVAL_ID={approval_id}")
    print(f"PENDING_DATA={json.dumps(pending, ensure_ascii=False)}")

    notify_post_preview(post_text, "すなくん (@sunakun_xxxx)", approval_id)
    print(f"すなくん投稿プレビュー送信完了 (approval_id={approval_id})")


if __name__ == "__main__":
    run()
