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

403エラーハンドリング（2026-07-12追加）:
- 重複ツイート (error code 327): ゼロ幅スペースを付与して自動リトライ
- 権限エラー: X Developer Portalでトークン再生成を促すメッセージを表示
"""
import os
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

def _is_duplicate_error(e: tweepy.errors.Forbidden) -> bool:
        """X API v2の重複ツイートエラー (code 327) かどうかを判定"""
        try:
                    # tweepy v4: e.api_codes はエラーコードのリスト
                    if hasattr(e, "api_codes") and 327 in (e.api_codes or []):
                                    return True
                                # フォールバック: エラー文字列で判定
                                err_str = str(e).lower()
                    return "duplicate" in err_str or "already tweeted" in err_str
except Exception:
        return False

def _handle_403(e: tweepy.errors.Forbidden) -> None:
        """403エラーの診断メッセージを出力"""
        if _is_duplicate_error(e):
                    print(f"[twitter_client] 重複ツイート検知 (327): {e}")
else:
        print(
                        f"[twitter_client] 403 Forbidden: {e}\n"
                        "【対処法】X Developer Portal でアクセストークンを再生成してください:\n"
                        "1. https://developer.x.com/en/portal/projects-and-apps\n"
                        "2. アプリ → Edit → User authentication settings → Read and Write に変更\n"
                        "3. Keys and tokens → Access Token and Secret → Regenerate\n"
                        "4. GitHub Secrets の HAL_TWITTER_ACCESS_TOKEN / HAL_TWITTER_ACCESS_SECRET を更新"
        )

def post_tweet(text: str, account: str = "HAL") -> str:
        prefix = account.upper()
        try:
                    response = _v2_client(prefix).create_tweet(text=text)
                    return str(response.data["id"])
except tweepy.errors.Forbidden as e:
        _handle_403(e)
        if _is_duplicate_error(e):
                        # 重複の場合: ゼロ幅スペースを末尾に追加してリトライ
                        print("[twitter_client] 重複検知 → ゼロ幅スペース付与してリトライ")
                        response = _v2_client(prefix).create_tweet(text=text + "​")
                        return str(response.data["id"])
                    raise

def post_tweet_with_media(text: str, media_path: str, account: str = "HAL") -> str:
        prefix = account.upper()
        if not media_path or not os.path.exists(media_path):
                    return post_tweet(text, account)
                media = _v1_api(prefix).media_upload(filename=media_path)
    try:
                response = _v2_client(prefix).create_tweet(text=text, media_ids=[media.media_id_string])
                return str(response.data["id"])
except tweepy.errors.Forbidden as e:
        _handle_403(e)
        if _is_duplicate_error(e):
                        # 重複の場合: ゼロ幅スペースを末尾に追加してリトライ（メディアIDは再利用可）
                        print("[twitter_client] 重複検知 → ゼロ幅スペース付与してリトライ")
                        response = _v2_client(prefix).create_tweet(
                            text=text + "​", media_ids=[media.media_id_string]
                        )
                        return str(response.data["id"])
                    raise

def post_reply(text: str, in_reply_to_tweet_id: str, account: str = "HAL") -> str:
        prefix = account.upper()
    response = _v2_client(prefix).create_tweet(
                text=text,
                in_reply_to_tweet_id=in_reply_to_tweet_id,
    )
    return str(response.data["id"])
