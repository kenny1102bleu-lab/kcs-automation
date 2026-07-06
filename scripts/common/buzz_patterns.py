"""
バズ投稿型リサーチ：X recent search でガジェット系の高エンゲージ投稿を取得し、
Gemini に「型・切り口・書き出しパターン」だけを抽出させてプロンプト注入用の文字列を返す。

失敗時は None を返す → 呼び出し側は注入をスキップして既存挙動を維持。
"""
import os
import tweepy
import google.generativeai as genai


DEFAULT_QUERY = "(ガジェット OR コスパ OR 充電器 OR モバイルバッテリー OR Anker OR イヤホン) lang:ja -is:retweet -is:reply"

EXTRACTOR_PROMPT = """あなたはSNS投稿の構造分析家です。
渡された複数のバズツイートから、共通する「型」「書き出しパターン」「フック」だけを抽出します。

【厳守】
- 投稿の中身（商品名・固有名詞・具体的数値）はコピーしない。構造のみ抽出する。
- 3〜5個の「型」を箇条書きで返す。各型は1行・40文字以内。
- 例：「数字×意外性で始める（例：3年使って気付いた○○）」「失敗談→解決の流れ」「リスト型 (○選)」
- 出力は箇条書きのみ。前置き・後書き不要。"""


def _x_client(account: str = "SUNAKUN") -> tweepy.Client | None:
    """OAuth1優先、なければBearer。両方なければNone。"""
    prefix = account.upper()
    ck = os.environ.get(f"{prefix}_TWITTER_API_KEY")
    cs = os.environ.get(f"{prefix}_TWITTER_API_SECRET")
    at = os.environ.get(f"{prefix}_TWITTER_ACCESS_TOKEN")
    ats = os.environ.get(f"{prefix}_TWITTER_ACCESS_SECRET")
    if ck and cs and at and ats:
        return tweepy.Client(
            consumer_key=ck, consumer_secret=cs,
            access_token=at, access_token_secret=ats,
        )
    bearer = os.environ.get(f"{prefix}_TWITTER_BEARER_TOKEN") or os.environ.get("X_BEARER_TOKEN")
    if bearer:
        return tweepy.Client(bearer_token=bearer)
    return None


def fetch_top_tweets(query: str = DEFAULT_QUERY, max_results: int = 30, min_likes: int = 100) -> list[str]:
    client = _x_client()
    if client is None:
        return []
    try:
        res = client.search_recent_tweets(
            query=query,
            max_results=max_results,
            tweet_fields=["public_metrics", "lang"],
        )
    except Exception:
        return []
    tweets = res.data or []
    scored = []
    for t in tweets:
        m = t.public_metrics or {}
        likes = m.get("like_count", 0)
        if likes < min_likes:
            continue
        engagement = likes + m.get("retweet_count", 0) * 3 + m.get("reply_count", 0)
        scored.append((engagement, t.text))
    scored.sort(reverse=True)
    return [text for _, text in scored[:10]]


def get_buzz_summary(query: str = DEFAULT_QUERY) -> str | None:
    """バズ投稿の構造サマリを返す。X API未設定・該当なし・失敗時は None。"""
    from scripts.common.env_clean import clean_env
    api_key = clean_env("GEMINI_API_KEY")
    if not api_key:
        return None
    samples = fetch_top_tweets(query=query)
    if not samples:
        return None
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash", system_instruction=EXTRACTOR_PROMPT)
        joined = "\n---\n".join(samples)
        response = model.generate_content(f"以下のバズツイートから型を抽出してください：\n\n{joined}")
        text = (response.text or "").strip()
        return text or None
    except Exception:
        return None
