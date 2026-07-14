"""
X(Twitter) クライアント。
- post_tweet: text のみ
- post_tweet_with_media: 画像/動画付き (v1.1 API でmedia upload → v1.1でtweet)
- post_reply: セルフリプライ（engagementTick用）


認証情報はclean_env()でBOM/ゼロ幅文字を除去してから使う。素のos.environ[...]
のままだとOAuth1.0a署名が壊れ、Twitter API側で
"215 - Bad Authentication data" として拒否される（ANTHROPIC_API_KEY /
DISCORD_WEBHOOK_URL(S) / GEMINI_API_KEY で既に発生した同じ原因のバグが
ここにも残っていたため2026-07-08に修正）。


403エラーハンドリング（2026-07-12追加）:
- 重複ツイート (error code 187/327): ゼロ幅スペースを付与して自動リトライ
- 権限エラー: X Developer Portalでトークン再生成を促すメッセージを表示

2026-07-14: v2 create_tweet の403エラー回避のため全投稿をv1.1 update_statusに切り替え
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


def _is_duplicate_error(e) -> bool:
    """重複ツイートエラーかどうかを判定（v1.1 code 187 / v2 code 327 両対応）"""
    try:
        if hasattr(e, "api_codes") and 327 in (e.api_codes or []):
            return True
        if hasattr(e, "api_code") and e.api_code == 187:
            return True
        err_str = str(e).lower()
        return "duplicate" in err_str or "already tweeted" in err_str
    except Exception:
        return False


def _handle_403(e) -> None:
    """403エラーの診断メッセージを出力"""
    print(f"[twitter_client] DEBUG api_codes: {getattr(e, 'api_codes', None)}")
    print(f"[twitter_client] DEBUG api_errors: {getattr(e, 'api_errors', None)}")
    if hasattr(e, "response") and e.response is not None:
        try:
            print(f"[twitter_client] DEBUG response.text: {e.response.text[:500]}")
        except Exception as ex:
            print(f"[twitter_client] DEBUG response.text error: {ex}")
    if _is_duplicate_error(e):
        print(f"[twitter_client] 重複ツイート検知 (187/327): {e}")
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
        status = _v1_api(prefix).update_status(status=text)
        return str(status.id)
    except tweepy.errors.Forbidden as e:
        _handle_403(e)
        if _is_duplicate_error(e):
            print("[twitter_client] 重複検知 → ゼロ幅スペース付与してリトライ")
            status = _v1_api(prefix).update_status(status=text + "\u200b")
            return str(status.id)
        raise


def post_tweet_with_media(text: str, media_path: str, account: str = "HAL") -> str:
    prefix = account.upper()
    if not media_path or not os.path.exists(media_path):
        return post_tweet(text, account)
    api = _v1_api(prefix)
    media = api.media_upload(filename=media_path)
    try:
        status = api.update_status(status=text, media_ids=[media.media_id_string])
        return str(status.id)
    except tweepy.errors.Forbidden as e:
        _handle_403(e)
        if _is_duplicate_error(e):
            print("[twitter_client] 重複検知 → ゼロ幅スペース付与してリトライ")
            status = api.update_status(status=text + "\u200b", media_ids=[media.media_id_string])
            return str(status.id)
        raise


def post_reply(text: str, in_reply_to_tweet_id: str, account: str = "HAL") -> str:
    prefix = account.upper()
    status = _v1_api(prefix).update_status(
        status=text,
        in_reply_to_status_id=in_reply_to_tweet_id,
        auto_populate_reply_metadata=True,
    )
    return str(status.id)
