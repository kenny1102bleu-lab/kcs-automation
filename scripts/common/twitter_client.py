"""
X(Twitter) クライアント。
- post_tweet: text のみ
- post_tweet_with_media: 画像/動画付き (v1.1 API でmedia upload → v2でtweet)
- post_reply: セルフリプライ（engagementTick用）

認証情報はclean_env()でBOM/ゼロ幅文字を除去してから使う。素のos.environ[...]
のままだとOAuth1.0a署名が壊れ、Twitter API側で
"215 - Bad Authentication data" として拒否される（ANTHROPIC_API_KEY /
DISCORD_WEBHOOK_URL(S) / GEMINI_API_KEY で既に発生した同じ原因のバグが
ここにも残っていたため2026-07-08に修正）。
"""
import tweepy

from scripts.common.env_clean import clean_env


def _v2_client(prefix: str) -> tweepy.Client:
    return tweepy.Client(
        consumer_key=clean_env(f"{prefix}_TWITTER_API_KEY"),
        consumer_secret=clean_env(f"{prefix}_TWITTER_API_SECRET"),
        access_token=clean_env(f"{prefix}_TWITTER_ACCESS_TOKEN"),
        access_token_secret=clean_env(f"{prefix}_TWITTER_ACCESS_SECRET"),
    )


def _v1_api(prefix: str) -> tweepy.API:
    auth = tweepy.OAuth1UserHandler(
        clean_env(f"{prefix}_TWITTER_API_KEY"),
        clean_env(f"{prefix}_TWITTER_API_SECRET"),
        clean_env(f"{prefix}_TWITTER_ACCESS_TOKEN"),
        clean_env(f"{prefix}_TWITTER_ACCESS_SECRET"),
    )
    return tweepy.API(auth)


def post_tweet(text: str, account: str = "HAL") -> str:
    prefix = account.upper()
    response = _v2_client(prefix).create_tweet(text=text)
    return str(response.data["id"])


def post_tweet_with_media(text: str, media_path: str, account: str = "HAL") -> str:
    prefix = account.upper()
    if not media_path or not os.path.exists(media_path):
        return post_tweet(text, account)
    media = _v1_api(prefix).media_upload(filename=media_path)
    response = _v2_client(prefix).create_tweet(text=text, media_ids=[media.media_id_string])
    return str(response.data["id"])


def post_reply(text: str, in_reply_to_tweet_id: str, account: str = "HAL") -> str:
    prefix = account.upper()
    response = _v2_client(prefix).create_tweet(
        text=text,
        in_reply_to_tweet_id=in_reply_to_tweet_id,
    )
    return str(response.data["id"])
