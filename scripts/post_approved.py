"""
WF-05: 承認後X投稿スクリプト
社長が Discord で !承認 コマンドを送った後、Bot が GitHub Actions を叩いて起動。
環境変数: POST_TEXT, ACCOUNT, MEDIA_PATH(optional), AFFILIATE_LINK(optional)
- メディアあれば添付投稿
- 15分後: 会話誘発セルフリプライ（HAL/すなくん共通）
- AFFILIATE_LINK あれば、さらに5分後（投稿から計20分後）にリンクをセルフリプライ
"""
import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.common.twitter_client import post_tweet, post_tweet_with_media, post_reply
from scripts.common.discord_notify import notify_account
from scripts.common.self_reply import generate_engagement_reply


def run():
    post_text = os.environ["POST_TEXT"]
    account = os.environ["ACCOUNT"]
    media_path = os.environ.get("MEDIA_PATH", "").strip()
    affiliate_link = os.environ.get("AFFILIATE_LINK", "").strip()

    try:
        if media_path and os.path.exists(media_path):
            tweet_id = post_tweet_with_media(post_text, media_path, account)
            meta = f" (media: {os.path.basename(media_path)})"
        else:
            tweet_id = post_tweet(post_text, account)
            meta = ""
    except Exception as e:
        # 投稿失敗時に沈黙しない（GitHub Actionsが赤くなるだけではDiscordに気づけない）
        notify_account(f"🔴 **{account}** X投稿失敗\nMEDIA_PATH={media_path or '(なし)'}\nエラー: {e}", account)
        raise

    notify_account(f"✅ **{account}** 投稿完了{meta}\nhttps://x.com/i/web/status/{tweet_id}", account)
    print(f"投稿完了: tweet_id={tweet_id}")

    # セルフリプライ①: 会話誘発コメント（投稿15分後、HAL/すなくん共通）。
    # 旧GAS実装(engagementTick)が担っていたが、HAL/すなくんの実投稿がPython側
    # (post_approved.py)に一本化されて以降トリガーされなくなっており、
    # HALは常時・すなくんもリンクなし投稿時はセルフリプライが皆無だった
    # （2026-07-12発覚）。フォロワーとの会話を誘発しインプレッションを稼ぐ狙い。
    # 生成失敗・NG検知時はNoneが返り、投稿本体には影響しない（既存挙動維持）。
    time.sleep(60 * 15)
    engagement_text = generate_engagement_reply(account)
    if engagement_text:
        try:
            reply_id = post_reply(engagement_text, tweet_id, account)
            notify_account(f"💬 セルフリプライ（会話誘発）完了: {engagement_text}\n(tweet_id={reply_id})", account)
        except Exception as e:
            notify_account(f"🔴 **{account}** セルフリプライ（会話誘発）失敗\nエラー: {e}", account)

    # セルフリプライ②: アフィリエイトリンク（さらに5分後、投稿から計20分後）
    if affiliate_link:
        time.sleep(60 * 5)
        try:
            reply_id = post_reply(f"リンクこちらです👇\n{affiliate_link}", tweet_id, account)
            notify_account(f"🔗 セルフリプライ（リンク）完了 (tweet_id={reply_id})", account)
        except Exception as e:
            notify_account(f"🔴 **{account}** セルフリプライ（リンク）失敗\nエラー: {e}", account)
            raise


if __name__ == "__main__":
    run()
