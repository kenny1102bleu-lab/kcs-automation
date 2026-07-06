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
from scripts.common.post_parser import parse as parse_post, assemble_post
from scripts.common.nana import generate_media
from scripts.common.ng_patterns import scan as ng_scan
from scripts.common.buzz_patterns import get_buzz_summary
from scripts.common.engagement_loop import get_win_patterns
from scripts.common.env_clean import clean_env
from scripts.common.product_source import fetch_trending_product, download_product_image, record_posted_url
from scripts.common.x_limits import validate as x_validate, weighted_length, count_hashtags

# PR表記と3アクション誘導はAIの記憶に頼らず、コード側で必ず1段目に固定で
# 差し込む（AIが省略することがあったため確実性を優先）。
SUNAKUN_PR_PREFIX = "【PR】"
SUNAKUN_CTA = "①いいね ②保存 ③『リンク希望』と返信で教えるよ！"

TAKUMI_PROMPT = """あなたはKCS合同会社のアフィリエイト担当「タクミ」です。
ガジェット系アフィリエイトアカウント「すなくん」の投稿テキストを作成します。

【すなくんのキャラクター設定】
- 26歳のガジェットオタク男子。テンション高め、フレンドリー、たまにイキる。
- 楽天・Amazonアフィリ特化。商品ジャンルはガジェット、生活雑貨、コスパ系。
- HAL（同じ事務所のモデル女子）の天然発言にツッコミを入れる小競り合い芸あり。
- 口語: 「これマジでいい！」「コスパバグってる」「秒で売り切れる」。

【投稿の構成（2段構成）】
- 1段目: 本文のみ。PR表記と①②③の誘導文言はコード側が自動で付け足すので
  post_textにはそれらを含めず、商品の魅力を伝える内容だけをしっかり書く
- 2段目: ハッシュタグのみ（hashtagsに配列で出力）

【運用ルール（X最適化）】
- Xの実際の文字数上限は280ユニット（漢字・ひらがな・絵文字などの全角文字は
  1文字=2ユニットとしてカウントされる。半角の英数字は1文字=1ユニット）。
  post_textは、後からPR表記・誘導文言・ハッシュタグが追加される分の余裕を
  見て、80〜100文字程度に収める
- **外部リンクの本文直貼りは絶対禁止**。リンクはリプライ欄に置く
- ハッシュタグ3〜5個
- 「AI」「自動」「ボット」「中の人」等の存在否定ワードは絶対に使わない

【メディア判断】
- "image": 商品写真メイン（基本これ）
- "video": 開封の儀・実機レビュー（バズ狙い、月数本まで）
- "none": 速報的なお得情報のみ

【出力フォーマット】必ず以下のJSONのみで出力（説明文なし）:
{"post_text":"本文のみ（PR表記・誘導文言・ハッシュタグなし）", "hashtags":["タグ1","タグ2","タグ3"], "media_type":"image|video|none", "media_prompt":"画像/動画生成プロンプト（noneなら空文字）"}"""


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
    return "\n\n" + "\n\n".join(blocks) + "\n\n上記は参考情報。2段構成・URL直貼り禁止の運用ルールは絶対遵守。"

def run():
    genai.configure(api_key=clean_env("GEMINI_API_KEY"))
    system_prompt = TAKUMI_PROMPT + _build_context_block()
    model = genai.GenerativeModel("gemini-2.5-flash", system_instruction=system_prompt)

    manual_theme = os.environ.get("POST_THEME", "").strip()
    # 手動テーマ指定時はテーマ優先（既存のAI生成フローを維持）。
    # 通常運用時は楽天ランキングAPIから実在の商品を取得し、実商品画像・
    # 実アフィリエイトリンクで投稿する（社長指摘: AI生成の「商品風」画像ではなく実写真にすべき）。
    product = None if manual_theme else fetch_trending_product()

    if product:
        user_message = (
            "以下は楽天ランキングから取得した実在の商品情報です。この情報に忠実に、"
            "捏造や誇張をせずに投稿テキストを作成してください。\n"
            f"商品名: {product['title']}\n"
            f"価格: {product['price']}\n"
            f"商品説明: {product['description']}\n\n"
            "実商品の写真を別途添付するため、media_typeは\"image\"、"
            "media_promptは空文字で出力してください。"
        )
    elif manual_theme:
        user_message = f"本日のテーマ: {manual_theme}\nすなくんの投稿テキストを作成してください。"
    else:
        theme = fetch_theme("sunakun")
        user_message = format_theme_prompt(theme, "本日のテーマ: 最新のコスパ最強ガジェット\nすなくんの投稿テキストを作成してください。")
    response = model.generate_content(user_message)
    parsed = parse_post(response.text)

    # 1段目(本文+固定PR表記+固定誘導文言) + 2段目(ハッシュタグ) を組み立てる。
    # PR表記・誘導文言はAIが省略することがあったため、コード側で確実に固定する。
    block1 = f"{SUNAKUN_PR_PREFIX}{parsed['post_text']}\n{SUNAKUN_CTA}"
    post_text = assemble_post(block1, parsed["hashtags"])

    for attempt in range(2):
        result = review(post_text)
        if result["status"] == "approved":
            break
        if attempt == 0 and "fixed_text" in result:
            post_text = result["fixed_text"]
        else:
            msg = f"⚠️ すなくん投稿がマモル審査を通過できませんでした。\n理由: {result['reason']}\n投稿テキスト: {post_text[:300]}"
            print(msg)
            notify(msg)
            return

    # X文字数・ハッシュタグ数チェック（全角文字は2ユニット換算の実際の上限280で検証）
    x_ok, x_reason = x_validate(post_text)
    if not x_ok:
        msg = f"⚠️ すなくん投稿がX文字数/ハッシュタグルールを満たせませんでした。\n理由: {x_reason}\n投稿テキスト: {post_text[:300]}"
        print(msg)
        notify(msg)
        return

    # NGパターン最終監査
    ng = ng_scan(post_text, exclude=["body_url_direct"])  # すなくんは[LINK]プレースホルダ許容
    if ng:
        msg = f"⚠️ すなくん投稿NG監査拒否: {ng[0]} = `{ng[1]}`\n投稿テキスト: {post_text[:300]}"
        print(msg)
        notify(msg)
        return

    qa_summary = (
        "✅ 自動監査 通過済み: AI開示語 / 簡体字 / AI口調(案1･2等) / "
        "絵文字クラスタ / スパム / 個人情報\n"
        f"✅ X文字数: {weighted_length(post_text)}/280ユニット / ハッシュタグ: {count_hashtags(post_text)}個\n"
        "✅ PR表記・3アクション誘導文言: コード側で1段目に固定挿入済み\n"
        f"✅ マモル(Claude)コンプライアンス審査: 承認（{result.get('reason','問題なし')}）\n"
        + ("🛒 商品ソース: 楽天ランキングAPI（実商品・実画像・実アフィリエイトリンク）\n"
           if product else "🛒 商品ソース: AIテーマ生成（実商品URLなし、画像もAI生成）\n")
        + "👀 社長確認ポイント: 商品情報の正確性のみ"
    )

    affiliate_link = ""
    if product:
        media = download_product_image(product["image_url"], product["title"], account="SUNAKUN")
        if media.get("error"):
            qa_summary += f"\n⚠️ 実商品画像の取得に失敗（テキストのみ投稿可）: {media['error']}"
        affiliate_link = product["affiliate_url"]
        record_posted_url(product["item_url"])
    else:
        media = generate_media(parsed["media_type"], parsed["media_prompt"], account="SUNAKUN")
        if media.get("error"):
            qa_summary += f"\n⚠️ メディア生成失敗（テキストのみ投稿可）: {media['error']}"

    approval_id = str(uuid.uuid4())[:8]
    media_path = media.get("path", "")
    pending = {
        "post_text": post_text,
        "account": "SUNAKUN",
        "media_type": "image" if product else parsed["media_type"],
        "media_filename": os.path.basename(media_path) if media_path else "",
        "media_run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "affiliate_link": affiliate_link,
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
    print(f"すなくん投稿プレビュー送信完了 (approval_id={approval_id}, media={pending['media_type']})")


if __name__ == "__main__":
    run()
