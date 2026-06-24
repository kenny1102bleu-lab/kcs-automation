"""
WF-05: 承認後X投稿スクリプト
社長が Discord で !承認 コマンドを送った後、Bot が GitHub Actions を叩いて起動。
環境変数: POST_TEXT, ACCOUNT, MEDIA_PATH(optional), AFFILIATE_LINK(optional)
- メディアあれば添付投稿
- AFFILIATE_LINK あれば 20分後にセルフリプライ
"""
import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.common.twitter_client import post_tweet, post_tweet_with_media, post_reply
from scripts.common.discord_notify import notify


def run():
    post_text = os.environ["POST_TEXT"]
    account = os.environ["ACCOUNT"]
    media_path = os.environ.get("MEDIA_PATH", "").strip()
    affiliate_link = os.environ.get("AFFILIATE_LINK", "").strip()

    if media_path and os.path.exists(media_path):
        tweet_id = post_tweet_with_media(post_text, media_path, account)
        meta = f" (media: {os.path.basename(media_path)})"
    else:
        tweet_id = post_tweet(post_text, account)
        meta = ""

    notify(f"✅ **{account}** 投稿完了{meta}\nhttps://x.com/i/web/status/{tweet_id}")
    print(f"投稿完了: tweet_id={tweet_id}")

    # engagementTick: 20分後にアフィリリンクをセルフリプ
    if affiliate_link:
        time.sleep(60 * 20)
        reply_id = post_reply(f"リンクこちらです👇\n{affiliate_link}", tweet_id, account)
        notify(f"🔗 セルフリプライ完了 (tweet_id={reply_id})")


if __name__ == "__main__":
    run()
