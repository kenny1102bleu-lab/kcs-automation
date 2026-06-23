import os
import tweepy

def get_client(
    api_key_env="TWITTER_API_KEY",
    api_secret_env="TWITTER_API_SECRET",
    access_token_env="TWITTER_ACCESS_TOKEN",
    access_secret_env="TWITTER_ACCESS_SECRET",
) -> tweepy.Client:
    return tweepy.Client(
        consumer_key=os.environ[api_key_env],
        consumer_secret=os.environ[api_secret_env],
        access_token=os.environ[access_token_env],
        access_token_secret=os.environ[access_secret_env],
    )

def post_tweet(text: str, account: str = "HAL") -> str:
    """account に応じて環境変数プレフィックスを切り替える。戻り値はtweet_id。"""
    prefix = account.upper()
    client = get_client(
        api_key_env=f"{prefix}_TWITTER_API_KEY",
        api_secret_env=f"{prefix}_TWITTER_API_SECRET",
        access_token_env=f"{prefix}_TWITTER_ACCESS_TOKEN",
        access_secret_env=f"{prefix}_TWITTER_ACCESS_SECRET",
    )
    response = client.create_tweet(text=text)
    return str(response.data["id"])
