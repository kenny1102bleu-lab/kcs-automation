"""
WF-05: 承認後X投稿スクリプト
社長が Discord で !承認 コマンドを送った後、Bot が GitHub Actions を叩いて起動。
環境変数 POST_TEXT と ACCOUNT を受け取ってX投稿を実行する。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.common.twitter_client import post_tweet
from scripts.common.discord_notify import notify


def run():
    post_text = os.environ["POST_TEXT"]
    account = os.environ["ACCOUNT"]

    tweet_id = post_tweet(post_text, account)

    notify(f"✅ **{account}** 投稿完了！\nhttps://x.com/i/web/status/{tweet_id}")
    print(f"投稿完了: tweet_id={tweet_id}")


if __name__ == "__main__":
    run()
