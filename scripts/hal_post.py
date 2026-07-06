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
from scripts.common.post_parser import parse as parse_post, assemble_post
from scripts.common.nana import generate_media
from scripts.common.ng_patterns import scan as ng_scan
from scripts.common.env_clean import clean_env
from scripts.common.x_limits import validate as x_validate, weighted_length, count_hashtags

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

【投稿の構成（2段構成）】
- 1段目: 日本語本文＋繁体字訳のみ（ハッシュタグは含めない）
- 2段目: ハッシュタグのみ（hashtagsに配列で出力）

【投稿ルール】
- Xの実際の文字数上限は280ユニット（漢字・ひらがな・繁體字・絵文字などの
  全角文字は1文字=2ユニットとしてカウントされる。半角の英数字やハッシュタグの
  記号部分は1文字=1ユニット）。post_text（日本語部分＋繁体字訳）とハッシュタグを
  合計して280ユニットを超えないこと。目安として日本語部分は55〜65文字程度、
  繁体字訳も55〜65文字程度に収める
- 繁体字中国語の訳を末尾に添える
- ハッシュタグ3〜5個
- 外部リンクの本文直貼りは禁止（必要なら「リプ欄に置いとくね」誘導）
- 自然な話し言葉で、等身大の日常感を大切に

【メディア判断】
投稿のテーマに応じてメディア種別を選んでください:
- "image": OOTD・コーデ・カフェ写真・K-POP共感など（基本これ）
- "video": 動く瞬間が映える時のみ（踊る、表情変化、開封の儀）。月数本まで。
- "none": テキストだけで親近感を出したい小ネタやニュース反応

【撮影シチュエーション判断（photo_context）】
投稿内容が仕事関連（撮影現場、モデル業務、イベント、取材等）かプライベート
（休日、日常、自宅、友人との時間等）かを判定してください:
- "work": 誰かに撮ってもらった構図。第三者視点、自然な引きの構図、
  スマホを持っている素振りなし、プロに撮影されたような画角。
- "private": 自撮りの構図。スマホを自分で持って撮った近距離アングル、
  腕や手が画面端に見えることがある、カジュアルな自撮り感。

【出力フォーマット】必ず以下のJSONのみで出力（説明文なし）:
{"post_text":"日本語本文＋繁体字訳（ハッシュタグなし）", "hashtags":["タグ1","タグ2","タグ3"], "media_type":"image|video|none", "media_prompt":"画像/動画生成プロンプト（noneなら空文字）", "photo_context":"work|private"}"""

def run():
    genai.configure(api_key=clean_env("GEMINI_API_KEY"))
    model = genai.GenerativeModel("gemini-2.5-flash", system_instruction=YUKI_PROMPT)

    manual_theme = os.environ.get("POST_THEME", "").strip()
    if manual_theme:
        user_message = f"本日のテーマ: {manual_theme}\nHALの投稿テキストを作成してください。"
    else:
        theme = fetch_theme("hal")
        user_message = format_theme_prompt(theme, "本日のテーマ: 今日のコーデや気分\nHALの投稿テキストを作成してください。")
    response = model.generate_content(user_message)
    parsed = parse_post(response.text)

    # 1段目(日本語+繁体字) + 2段目(ハッシュタグ) を組み立てる。
    post_text = assemble_post(parsed["post_text"], parsed["hashtags"])

    # マモル審査（最大2回まで自動修正）
    for attempt in range(2):
        result = review(post_text)
        if result["status"] == "approved":
            break
        if attempt == 0 and "fixed_text" in result:
            post_text = result["fixed_text"]
        else:
            msg = f"⚠️ HAL投稿がマモル審査を通過できませんでした。\n理由: {result['reason']}\n投稿テキスト: {post_text[:300]}"
            print(msg)
            notify(msg)
            return

    # X文字数・ハッシュタグ数チェック（全角文字は2ユニット換算の実際の上限280で検証）
    x_ok, x_reason = x_validate(post_text)
    if not x_ok:
        msg = f"⚠️ HAL投稿がX文字数/ハッシュタグルールを満たせませんでした。\n理由: {x_reason}\n投稿テキスト: {post_text[:300]}"
        print(msg)
        notify(msg)
        return

    # NGパターン最終監査（マモル後の二重チェック）
    ng = ng_scan(post_text)
    if ng:
        msg = f"⚠️ HAL投稿NG監査拒否: {ng[0]} = `{ng[1]}`\n投稿テキスト: {post_text[:300]}"
        print(msg)
        notify(msg)
        return

    qa_summary = (
        "✅ 自動監査 通過済み: AI開示語 / MIMOMI早期言及 / URL直貼り / 簡体字 / "
        "AI口調(案1･2等) / 絵文字クラスタ / スパム / 個人情報\n"
        f"✅ X文字数: {weighted_length(post_text)}/280ユニット / ハッシュタグ: {count_hashtags(post_text)}個\n"
        f"✅ マモル(Claude)コンプライアンス審査: 承認（{result.get('reason','問題なし')}）\n"
        f"📸 撮影シチュエーション判定: {'仕事（第三者撮影構図）' if parsed.get('photo_context') == 'work' else 'プライベート（自撮り構図）'}\n"
        "👀 社長確認ポイント: トーン・事実関係・K-POP/推し活の時事ネタの鮮度のみ"
    )

    # ナナ: メディア生成（image/video/none）
    media = generate_media(parsed["media_type"], parsed["media_prompt"], account="HAL",
                            photo_context=parsed.get("photo_context", "private"))
    if media.get("error"):
        qa_summary += f"\n⚠️ メディア生成失敗（テキストのみ投稿可）: {media['error']}"

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

    notify_post_preview(post_text, "HAL (@hal_xxxx)", approval_id, media_info=media, qa_summary=qa_summary)
    print(f"HAL投稿プレビュー送信完了 (approval_id={approval_id}, media={parsed['media_type']})")


if __name__ == "__main__":
    run()
