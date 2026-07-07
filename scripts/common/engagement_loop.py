"""
engagement分析ループ：すな君（または指定アカウント）の直近投稿のpublic_metricsを取得し、
Gemini に「伸びた投稿の共通点・伸びなかった共通点・次回への改善提案」を抽出させる。

失敗時は None を返す → 呼び出し側は注入をスキップして既存挙動を維持。
"""
import os
import tweepy
import google.generativeai as genai

from scripts.common.env_clean import clean_env


WIN_PATTERN_PROMPT = """あなたはSNS運用アナリストです。
渡された投稿リストとそのエンゲージメント数値から、勝ちパターンと負けパターンを抽出します。

【厳守】
- 「勝ち型」「負け型」「次回への改善提案」の3ブロック構成。
- 各ブロック2〜3行・合計150文字以内。
- 具体的な商品名・固有名詞は書かない。構造・切り口・時間帯・文体の話に絞る。
- 出力は構成のみ。前置き・後書き不要。"""


def _x_client(account: str) -> tweepy.Client | None:
    """OAuth1優先、なければBearer。両方なければNone。"""
    prefix = account.upper()
    ck = clean_env(f"{prefix}_TWITTER_API_KEY")
    cs = clean_env(f"{prefix}_TWITTER_API_SECRET")
    at = clean_env(f"{prefix}_TWITTER_ACCESS_TOKEN")
    ats = clean_env(f"{prefix}_TWITTER_ACCESS_SECRET")
    if ck and cs and at and ats:
        return tweepy.Client(
            consumer_key=ck, consumer_secret=cs,
            access_token=at, access_token_secret=ats,
        )
    bearer = clean_env(f"{prefix}_TWITTER_BEARER_TOKEN") or clean_env("X_BEARER_TOKEN")
    if bearer:
        return tweepy.Client(bearer_token=bearer)
    return None


def _resolve_user_id(client: tweepy.Client, account: str) -> str | None:
    explicit = os.environ.get(f"{account.upper()}_X_USER_ID")
    if explicit:
        return explicit
    username = os.environ.get(f"{account.upper()}_X_USERNAME")
    if not username:
        return None
    try:
        res = client.get_user(username=username)
        return str(res.data.id) if res.data else None
    except Exception:
        return None


def fetch_recent_post_stats(account: str = "SUNAKUN", days: int = 7, max_results: int = 30) -> list[dict]:
    client = _x_client(account)
    if client is None:
        return []
    user_id = _resolve_user_id(client, account)
    if not user_id:
        return []
    try:
        res = client.get_users_tweets(
            id=user_id,
            max_results=max(5, min(max_results, 100)),
            tweet_fields=["public_metrics", "created_at"],
            exclude=["replies", "retweets"],
        )
    except Exception:
        return []
    tweets = res.data or []
    out = []
    for t in tweets:
        m = t.public_metrics or {}
        out.append({
            "text": t.text,
            "impressions": m.get("impression_count", 0),
            "likes": m.get("like_count", 0),
            "retweets": m.get("retweet_count", 0),
            "replies": m.get("reply_count", 0),
        })
    return out


def get_win_patterns(account: str = "SUNAKUN", days: int = 7) -> str | None:
    """直近投稿の勝ち/負けパターンサマリを返す。データ不足・失敗時は None。"""
    from scripts.common.env_clean import clean_env
    api_key = clean_env("GEMINI_API_KEY")
    if not api_key:
        return None
    stats = fetch_recent_post_stats(account=account, days=days)
    if len(stats) < 5:
        return None
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash", system_instruction=WIN_PATTERN_PROMPT)
        lines = []
        for s in stats:
            lines.append(
                f"[imp:{s['impressions']} like:{s['likes']} rt:{s['retweets']} reply:{s['replies']}]\n{s['text']}"
            )
        joined = "\n---\n".join(lines)
        response = model.generate_content(f"直近{days}日の投稿データから勝ち/負けパターンを抽出してください：\n\n{joined}")
        text = (response.text or "").strip()
        return text or None
    except Exception:
        return None
