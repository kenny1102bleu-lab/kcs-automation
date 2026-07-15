"""
scripts/auto_engage.py
自動エンゲージメント：いいね + フォロー

WF-10 (10_auto_engage.yml) から1日2回呼ばれる。
各アカウントで LIKES_PER_RUN 件のいいね + FOLLOWS_PER_RUN 件のフォローを実行。
"""

import sys
import os
import time
import random
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import tweepy

from scripts.candidate_discovery import QUERIES, build_client, log

JST = timezone(timedelta(hours=9))

# 1回の実行あたりの上限（1日2回 × これで合計制限内に収める）
LIKES_PER_RUN   = 20   # 1日2回 → 合計40いいね
FOLLOWS_PER_RUN = 10   # 1日2回 → 合計20フォロー
MIN_FOLLOWER    = 100  # フォロワー100未満のアカウントはスキップ


def get_my_id(client: tweepy.Client) -> str:
    """認証ユーザーのIDを取得"""
    resp = client.get_me()
    return str(resp.data.id)


def run_likes(client: tweepy.Client, account: str, my_id: str, limit: int) -> int:
    """いいね実行。limit 件まで。"""
    done = 0
    for query in QUERIES.get(account.lower(), []):
        if done >= limit:
            break
        try:
            resp = client.search_recent_tweets(
                query=query,
                max_results=10,
                tweet_fields=["author_id"],
            )
            if not resp.data:
                continue
            for tweet in resp.data:
                if done >= limit:
                    break
                try:
                    client.like(my_id, tweet.id)
                    log(f"[{account}] liked tweet_id={tweet.id}")
                    done += 1
                    time.sleep(random.uniform(3, 8))
                except tweepy.errors.TooManyRequests:
                    log(f"[{account}] like: rate limit hit, stopping")
                    return done
                except tweepy.errors.Forbidden as e:
                    log(f"[{account}] like: forbidden (already liked?) tweet={tweet.id}: {e}")
                except Exception as e:
                    log(f"[{account}] like: error tweet={tweet.id}: {e}")
        except Exception as e:
            log(f"[{account}] search error query='{query}': {e}")
        time.sleep(random.uniform(1, 3))
    return done


def run_follows(client: tweepy.Client, account: str, my_id: str, limit: int) -> int:
    """フォロー実行。limit 件まで。"""
    done = 0
    seen = set()
    for query in QUERIES.get(account.lower(), []):
        if done >= limit:
            break
        try:
            resp = client.search_recent_tweets(
                query=query,
                max_results=10,
                tweet_fields=["author_id"],
                expansions=["author_id"],
                user_fields=["public_metrics", "username"],
            )
            if not resp.data or not (resp.includes or {}).get("users"):
                continue
            for user in resp.includes["users"]:
                if done >= limit:
                    break
                uid = str(user.id)
                if uid in seen or uid == my_id:
                    continue
                seen.add(uid)
                followers = (user.public_metrics or {}).get("followers_count", 0)
                if followers < MIN_FOLLOWER:
                    continue
                try:
                    client.follow_user(my_id, uid)
                    log(f"[{account}] followed @{user.username} (followers={followers})")
                    done += 1
                    time.sleep(random.uniform(5, 12))
                except tweepy.errors.TooManyRequests:
                    log(f"[{account}] follow: rate limit hit, stopping")
                    return done
                except tweepy.errors.Forbidden as e:
                    log(f"[{account}] follow: forbidden (already following?) @{user.username}: {e}")
                except Exception as e:
                    log(f"[{account}] follow: error @{user.username}: {e}")
        except Exception as e:
            log(f"[{account}] search error query='{query}': {e}")
        time.sleep(random.uniform(1, 3))
    return done


def main(account: str) -> None:
    log(f"=== auto_engage START [{account}] likes={LIKES_PER_RUN} follows={FOLLOWS_PER_RUN} ===")
    client = build_client(account)
    my_id  = get_my_id(client)
    log(f"[{account}] my_id={my_id}")

    liked    = run_likes(client, account, my_id, LIKES_PER_RUN)
    followed = run_follows(client, account, my_id, FOLLOWS_PER_RUN)

    log(f"=== auto_engage END [{account}] liked={liked} followed={followed} ===")


if __name__ == "__main__":
    account = sys.argv[1].upper() if len(sys.argv) > 1 else "HAL"
    main(account)

