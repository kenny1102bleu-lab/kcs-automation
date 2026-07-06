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
from scripts.common.news_pool import fetch_theme, format_theme_prompt
from scripts.common.post_parser import parse as parse_post
from scripts.common.nana import generate_media
from scripts.common.ng_patterns import scan as ng_scan
from scripts.common.buzz_patterns import get_buzz_summary
from scripts.common.engagement_loop import get_win_patterns

TAKUMI_PROMPT = """あなたはKCS合同会社のアフィリエイト担当「タクミ」です。
ガジェット系アフィリエイトアカウント「すなくん」の投稿テキストを作成します。

【すなくんのキャラクター設定】
- 26歳のガジェットオタク男子。テンション高め、フレンドリー、たまにイキる。
- 楽天・Amazonアフィリ特化。商品ジャンルはガジェット、生活雑貨、コスパ系。
- HAL（同じ事務所のモデル女子）の天然発言にツッコミを入れる小競り合い芸あり。
- 口語: 「これマジでいい！」「コスパバグってる」「秒で売り切れる」。

【運用ルール（X最適化）】
- 140文字以内
- **外部リンクの本文直貼りは絶対禁止**。リンクはリプライ欄に置く
- 毎投稿に必ず「①いいね ②保存 ③『リンク希望』と返信」の3アクション誘導を含める
- ハッシュタグ3〜5個
- 「広告」「PR」の表記を必ず含める
- 「AI」「自動」「ボット」「中の人」等の存在否定ワードは絶対に使わない

【メディア判断】
- "image": 商品写真メイン（基本これ）
- "video": 開封の儀・実機レビュー（バズ狙い、月数本まで）
- "none": 速報的なお得情報のみ

【出力フォーマット】必ず以下のJSONのみで出力（説明文なし）:
{"post_text":"...", "media_type":"image|video|none", "media_prompt":"画像/動画生成プロンプト（noneなら空文字）"}"""


def _build_context_block() -> str:
    """バズ型と勝ちパターンを動的取得してプロンプト末尾に追加するブロックを返す。
    両方Noneなら空文字（既存挙動を維持）。"""
    blocks = []
    buzz = get_buzz_summary()
    if buzz:
        blocks.append("【参考にする最近のバズ型（中身ではなく構造のみ参考）】\n" + buzz)
    win = get_win_patterns(account="SUNAKUN")
    if win:
        blocks.append("【直近の自アカウント勝ち/負けパターン】\n" + win)
    if not blocks:
        return ""
    return "\n\n" + "\n\n".join(blocks) + "\n\n上記は参考情報。3段構成・PR表記・URL直貼り禁止・3アクション誘導の運用ルールは絶対遵守。"

def run():
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    system_prompt = TAKUMI_PROMPT + _build_context_block()
    model = genai.GenerativeModel("gemini-2.5-flash", system_instruction=system_prompt)

    manual_theme = os.environ.get("POST_THEME", "").strip()
    if manual_theme:
        user_message = f"本日のテーマ: {manual_theme}\nすなくんの投稿テキストを作成してください。"
    else:
        theme = fetch_theme("sunakun")
        user_message = format_theme_prompt(theme, "本日のテーマ: 最新のコスパ最強ガジェット\nすなくんの投稿テキストを作成してください。")
    response = model.generate_content(user_message)
    parsed = parse_post(response.text)
    post_text = parsed["post_text"]

    for attempt in range(2):
        result = review(post_text)
        if result["status"] == "approved":
            break
        if attempt == 0 and "fixed_text" in result:
            post_text = result["fixed_text"]
        else:
            notify(f"⚠️ すなくん投稿がマモル審査を通過できませんでした。\n理由: {result['reason']}")
            return

    # NGパターン最終監査
    ng = ng_scan(post_text, exclude=["body_url_direct"])  # すなくんは[LINK]プレースホルダ許容
    if ng:
        notify(f"⚠️ すなくん投稿NG監査拒否: {ng[0]} = `{ng[1]}`\n投稿テキスト: {post_text[:200]}")
        return

    qa_summary = (
        "✅ 自動監査 通過済み: AI開示語 / 簡体字 / AI口調(案1･2等) / "
        "絵文字クラスタ / スパム / 個人情報\n"
        f"✅ マモル(Claude)コンプライアンス審査: 承認（{result.get('reason','問題なし')}）\n"
        "👀 社長確認ポイント: 商品情報の正確性・PR表記の有無・3アクション誘導文言のみ"
    )

    media = generate_media(parsed["media_type"], parsed["media_prompt"], account="SUNAKUN")

    approval_id = str(uuid.uuid4())[:8]
    media_path = media.get("path", "")
    pending = {
        "post_text": post_text,
        "account": "SUNAKUN",
        "media_type": parsed["media_type"],
        "media_filename": os.path.basename(media_path) if media_path else "",
        "media_run_id": os.environ.get("GITHUB_RUN_ID", ""),
    }
    print(f"APPROVAL_ID={approval_id}")
    print(f"PENDING_DATA={json.dumps(pending, ensure_ascii=False)}")

    bot_webhook = os.environ.get("BOT_WEBHOOK_URL", "")
    if bot_webhook:
        try:
            import requests
            requests.post(bot_webhook, json={"approval_id": approval_id, **pending}, timeout=10)
        except Exception as e:
            print(f"bot webhook failed: {e}")

    notify_post_preview(post_text, "すなくん (@sunakun_xxxx)", approval_id, media_info=media, qa_summary=qa_summary)
    print(f"すなくん投稿プレビュー送信完了 (approval_id={approval_id}, media={parsed['media_type']})")


if __name__ == "__main__":
    run()
