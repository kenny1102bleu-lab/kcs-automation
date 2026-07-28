"""
tweepy.Client の一部v2エンドポイント（get_users_tweets/search_recent_tweets/get_users等）が、
正しいOAuth1認証情報でも毎回401 Unauthorizedを返す既知の問題（2026-07-28判明、
diag_twitter_access.pyで実証済み。tweepy側の認証処理の癖と推定、生のHTTPリクエストは
同一認証情報で成功する）への対応。raw OAuth1Sessionで直接X API v2を叩く共通ヘルパー。

engagement_loop.py/candidate_discovery.py/auto_engage.py/buzz_patterns.py で
tweepy.Client読み取り呼び出しの代わりに使う。
"""
from requests_oauthlib import OAuth1Session

from scripts.common.env_clean import clean_env

BASE = "https://api.twitter.com/2"


def oauth1_session(account: str) -> OAuth1Session | None:
    prefix = account.upper()
    ck = clean_env(f"{prefix}_TWITTER_API_KEY")
    cs = clean_env(f"{prefix}_TWITTER_API_SECRET")
    at = clean_env(f"{prefix}_TWITTER_ACCESS_TOKEN")
    ats = clean_env(f"{prefix}_TWITTER_ACCESS_SECRET")
    if ck and cs and at and ats:
        return OAuth1Session(ck, cs, at, ats)
    return None


def x_get(session: OAuth1Session, path: str, params: dict) -> dict:
    """X API v2 GETをそのまま叩く。HTTPエラーは例外をraiseする（呼び出し側で処理する）。"""
    res = session.get(f"{BASE}{path}", params=params)
    res.raise_for_status()
    return res.json()
