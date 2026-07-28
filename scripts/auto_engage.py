"""
scripts/auto_engage.py
自動エンゲージメント：いいね + フォロー

WF-10 (10_auto_engage.yml) から1日2回呼ばれる。
各アカウントで LIKES_PER_RUN 件のいいね + FOLLOWS_PER_RUN 件のフォローを実行。

2026-07-28判明: tweepy.Client経由のsearch_recent_tweetsはOAuth1認証情報が正しくても
401 Unauthorizedを返す既知の問題があるため、検索(読み取り)は raw OAuth1Session
(scripts/common/x_api_raw.py)を使い、like/follow_user(書き込み)は引き続きtweepy.Clientを使う
（書き込み系は元々成功していたため触らない）。
"""

import sys
import os
import time
import random
from datetime import timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import tweepy

from scripts.candidate_discovery import QUERIES, log
from scripts.common.env_clean import clean_env
from scripts.common.x_api_raw import oauth1_session, x_get

JST = timezone(timedelta(hours=9))

# 1回の実行あたりの上限（1日2回 × これで合計制限内に収める）
LIKES_PER_RUN   = 20   # 1日2回 → 合計40いいね
FOLLOWS_PER_RUN = 10   # 1日2回 → 合計20フォロー
MIN_FOLLOWER    = 100  # フォロワー100未満のアカウントはスキップ


def _tweepy_client(account: str) -> tweepy.Client:
    """like/follow_user等の書き込み専用。読み取りにはこれを使わない（401問題）。"""
    prefix = account.upper()
    return tweepy.Client(
        consumer_key=clean_env(f"{prefix}_TWITTER_API_KEY"),
        consumer_secret=clean_env(f"{prefix}_TWITTER_API_SECRET"),
        access_token=clean_env(f"{prefix}_TWITTER_ACCESS_TOKEN"),
        access_token_secret=clean_env(f"{prefix}_TWITTER_ACCESS_SECRET"),
    )


def get_my_id(client: tweepy.Client) -> str:
    """認証ユーザーのIDを取得（get_me()はtweepy経由でも成功することを確認済み）"""
    resp = client.get_me()
    return str(resp.data.id)


def run_likes(session, client: tweepy.Client, account: str, my_id: str, limit: int) -> int:
    """いいね実行。limit 件まで。"""
    done = 0
    for query in QUERIES.get(account.lower(), []):
        if done >= limit:
            break
        try:
            data = x_get(session, "/tweets/search/recent",
                         {"query": query, "max_results": 10, "tweet.fields": "author_id"})
            tweets = data.get("data") or []
            for tweet in tweets:
                if done >= limit:
                    break
                tid = tweet["id"]
                try:
                    client.like(my_id, tid)
                    log(f"[{account}] liked tweet_id={tid}")
                    done += 1
                    time.sleep(random.uniform(3, 8))
                except tweepy.errors.TooManyRequests:
                    log(f"[{account}] like: rate limit hit, stopping")
                    return done
                except tweepy.errors.Forbidden as e:
                    log(f"[{account}] like: forbidden (already liked?) tweet={tid}: {e}")
                except Exception as e:
                    log(f"[{account}] like: error tweet={tid}: {e}")
        except Exception as e:
            log(f"[{account}] search error query='{query}': {e}")
        time.sleep(random.uniform(1, 3))
    return done


def run_follows(session, client: tweepy.Client, account: str, my_id: str, limit: int) -> int:
    """フォロー実行。limit 件まで。"""
    done = 0
    seen = set()
    for query in QUERIES.get(account.lower(), []):
        if done >= limit:
            break
        try:
            data = x_get(session, "/tweets/search/recent", {
                "query": query, "max_results": 10, "tweet.fields": "author_id",
                "expansions": "author_id", "user.fields": "public_metrics,username",
            })
            users = (data.get("includes") or {}).get("users") or []
            if not data.get("data") or not users:
                continue
            for user in users:
                if done >= limit:
                    break
                uid = str(user["id"])
                if uid in seen or uid == my_id:
                    continue
                seen.add(uid)
                followers = (user.get("public_metrics") or {}).get("followers_count", 0)
                if followers < MIN_FOLLOWER:
                    continue
                username = user.get("username", "")
                try:
                    client.follow_user(my_id, uid)
                    log(f"[{account}] followed @{username} (followers={followers})")
                    done += 1
                    time.sleep(random.uniform(5, 12))
                except tweepy.errors.TooManyRequests:
                    log(f"[{account}] follow: rate limit hit, stopping")
                    return done
                except tweepy.errors.Forbidden as e:
                    log(f"[{account}] follow: forbidden (already following?) @{username}: {e}")
                except Exception as e:
                    log(f"[{account}] follow: error @{username}: {e}")
        except Exception as e:
            log(f"[{account}] search error query='{query}': {e}")
        time.sleep(random.uniform(1, 3))
    return done


def main(account: str) -> None:
    log(f"=== auto_engage START [{account}] likes={LIKES_PER_RUN} follows={FOLLOWS_PER_RUN} ===")
    client = _tweepy_client(account)
    session = oauth1_session(account)
    my_id = get_my_id(client)
    log(f"[{account}] my_id={my_id}")

    if session is None:
        log(f"[{account}] OAuth1認証情報が無いため検索(いいね/フォロー対象探索)をスキップ")
        return

    liked    = run_likes(session, client, account, my_id, LIKES_PER_RUN)
    followed = run_follows(session, client, account, my_id, FOLLOWS_PER_RUN)

    log(f"=== auto_engage END [{account}] liked={liked} followed={followed} ===")


if __name__ == "__main__":
    account = sys.argv[1].upper() if len(sys.argv) > 1 else "HAL"
    main(account)
