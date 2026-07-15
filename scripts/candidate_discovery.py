"""
早期リプライ戦略の監視対象アカウント候補発見スクリプト。

ジャンル関連キーワードで recent search → 投稿者を集計 → 上位アカウントの
public_metrics/bioを取得 → フォロワー1万〜10万を優先表示したMarkdownテーブルを出力。

認証はSUNAKUN/HALそれぞれ既存の {PREFIX}_TWITTER_BEARER_TOKEN を優先し、
無ければ共通の X_BEARER_TOKEN にフォールバックする
（scripts/common/buzz_patterns.py と同じ方式）。

使い方:
    python -m scripts.candidate_discovery --account sunakun
    python -m scripts.candidate_discovery --account hal
"""
import argparse
import sys
import time
from collections import Counter
from datetime import datetime, timezone, timedelta

import tweepy

from scripts.common.env_clean import clean_env

JST = timezone(timedelta(hours=9))

QUERIES = {
    "sunakun": [
        "ガジェット セール lang:ja -is:retweet",
        "Amazon お得 買い物 lang:ja -is:retweet",
        "楽天 セール ガジェット lang:ja -is:retweet",
        "モバイルバッテリー レビュー lang:ja -is:retweet",
        "キャンプ ギア アウトドア lang:ja -is:retweet",
        "アウトドア ガジェット おすすめ lang:ja -is:retweet",
    ],
    "hal": [
        "K-POP 沼 lang:ja -is:retweet",
        "17LIVE 配信 応援 lang:ja -is:retweet",
        "モデル オーディション 応援 lang:ja -is:retweet",
        "コスメ 新作 レビュー lang:ja -is:retweet",
        "ディズニー ランド シー 楽しい lang:ja -is:retweet",
        "台湾 ハーフ 日本 lang:ja -is:retweet",
    ],
}

FOLLOWER_MIN = 10_000
FOLLOWER_MAX = 100_000

api_call_count = 0


def log(msg: str) -> None:
    ts = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr)


def build_client(account: str) -> tweepy.Client:
    """OAuth1優先、なければBearer。scripts/common/buzz_patterns.py と同じ方式。

    2026-07-08時点、GitHub SecretsにはSUNAKUN/HAL共に
    {PREFIX}_TWITTER_API_KEY 等のOAuth1フルクレデンシャルのみが存在し、
    {PREFIX}_TWITTER_BEARER_TOKEN / X_BEARER_TOKEN は未設定。
    指示書の「Bearer Token取得済み」前提とは異なるため、既存の
    OAuth1クレデンシャルを優先して使う。
    """
    prefix = account.upper()
    ck = clean_env(f"{prefix}_TWITTER_API_KEY")
    cs = clean_env(f"{prefix}_TWITTER_API_SECRET")
    at = clean_env(f"{prefix}_TWITTER_ACCESS_TOKEN")
    ats = clean_env(f"{prefix}_TWITTER_ACCESS_SECRET")
    if ck and cs and at and ats:
        return tweepy.Client(
            consumer_key=ck, consumer_secret=cs,
            access_token=at, access_token_secret=ats,
            wait_on_rate_limit=False,
        )
    bearer = clean_env(f"{prefix}_TWITTER_BEARER_TOKEN") or clean_env("X_BEARER_TOKEN")
    if bearer:
        return tweepy.Client(bearer_token=bearer, wait_on_rate_limit=False)
    raise RuntimeError(
        f"{prefix}_TWITTER_API_KEY 一式も {prefix}_TWITTER_BEARER_TOKEN も "
        "X_BEARER_TOKEN も未設定です。環境変数を確認してください。"
    )


def call_api(label: str, fn, **kwargs):
    global api_call_count
    api_call_count += 1
    log(f"API call #{api_call_count}: {label}")
    try:
        return fn(**kwargs)
    except tweepy.TooManyRequests as e:
        log(f"429 レート制限: {label} - {e}")
        raise
    except tweepy.Unauthorized as e:
        log(f"401 認証エラー: {label} - Bearer Tokenが無効か期限切れの可能性 - {e}")
        raise
    except tweepy.Forbidden as e:
        log(f"403 権限エラー: {label} - APIプランでこのエンドポイントが許可されていない可能性 - {e}")
        raise
    except tweepy.TweepyException as e:
        log(f"X APIエラー: {label} - {e}")
        raise


def collect_author_mentions(client: tweepy.Client, queries: list[str], max_results: int = 50) -> Counter:
    mentions = Counter()
    for q in queries:
        try:
            resp = call_api(
                f"search_recent_tweets query='{q}'",
                client.search_recent_tweets,
                query=q,
                max_results=max_results,
                tweet_fields=["author_id"],
            )
        except tweepy.TweepyException:
            log(f"クエリをスキップします: {q}")
            continue
        for tweet in resp.data or []:
            mentions[tweet.author_id] += 1
        time.sleep(1)
    return mentions


def collect_candidate_tweets(client: tweepy.Client, queries: list[str], max_results: int = 15) -> list[dict]:
    """collect_author_mentions()の集計版と違い、具体的なtweet_id/text/created_atを
    保持したまま返す（早期リプライ機能が実際に返信する1件を選ぶために必要）。
    早期リプライ用途のクエリ拡張(-is:reply -has:links等)は呼び出し側(early_reply.py)
    がQUERIESの値を書き換えずローカルで合成する（このモジュールの既存挙動を変えない）。"""
    seen_ids = set()
    tweets = []
    for q in queries:
        try:
            resp = call_api(
                f"search_recent_tweets(candidate_tweets) query='{q}'",
                client.search_recent_tweets,
                query=q,
                max_results=max_results,
                tweet_fields=["author_id", "created_at"],
            )
        except tweepy.TweepyException:
            log(f"クエリをスキップします: {q}")
            continue
        for t in resp.data or []:
            if t.id in seen_ids:
                continue
            seen_ids.add(t.id)
            tweets.append({
                "tweet_id": str(t.id),
                "author_id": str(t.author_id),
                "text": t.text,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            })
        time.sleep(1)
    return tweets


def fetch_user_info(client: tweepy.Client, user_ids: list[str]) -> dict:
    if not user_ids:
        return {}
    try:
        resp = call_api(
            f"get_users ids={len(user_ids)}件",
            client.get_users,
            ids=user_ids,
            user_fields=["public_metrics", "username", "description"],
        )
    except tweepy.TweepyException:
        return {}
    return {str(u.id): u for u in (resp.data or [])}


def discover(account: str) -> list[dict]:
    queries = QUERIES.get(account.lower())
    if queries is None:
        raise ValueError(f"未対応のaccount: {account} (sunakun/halのいずれか)")

    client = build_client(account)
    log(f"account={account} で {len(queries)}件のキーワード検索を開始します")

    mentions = collect_author_mentions(client, queries)
    log(f"投稿者ユニーク数: {len(mentions)}")

    top_ids = [uid for uid, _ in mentions.most_common(20)]
    users = fetch_user_info(client, top_ids)

    candidates = []
    for uid in top_ids:
        user = users.get(str(uid))
        if user is None:
            continue
        metrics = user.public_metrics or {}
        followers = metrics.get("followers_count", 0)
        candidates.append({
            "username": user.username,
            "followers": followers,
            "mentions_in_search": mentions[uid],
            "bio": (user.description or "").replace("\n", " ").replace("|", "/"),
            "in_target_range": FOLLOWER_MIN <= followers <= FOLLOWER_MAX,
        })

    candidates.sort(key=lambda c: (not c["in_target_range"], -c["followers"]))
    return candidates


def to_markdown(candidates: list[dict], account: str) -> str:
    lines = [
        f"# 監視対象アカウント候補 ({account})",
        "",
        f"生成日時: {datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')}",
        f"優先レンジ: フォロワー{FOLLOWER_MIN:,}〜{FOLLOWER_MAX:,}人",
        f"APIコール回数: {api_call_count}回",
        "",
        "| 優先 | @handle | フォロワー数 | 検索ヒット回数 | bio |",
        "|---|---|---|---|---|",
    ]
    for c in candidates:
        mark = "★" if c["in_target_range"] else ""
        bio = c["bio"][:60]
        lines.append(f"| {mark} | @{c['username']} | {c['followers']:,} | {c['mentions_in_search']} | {bio} |")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", required=True, choices=["sunakun", "hal"])
    parser.add_argument("--out", default=None, help="出力先ファイルパス(省略時はdata/配下に自動生成)")
    args = parser.parse_args()

    try:
        candidates = discover(args.account)
    except (RuntimeError, ValueError) as e:
        log(f"致命的エラー: {e}")
        sys.exit(1)

    md = to_markdown(candidates, args.account)
    print(md)
    log(f"合計APIコール回数: {api_call_count}回")

    out_path = args.out or f"data/candidate_accounts_{args.account}_{datetime.now(JST).strftime('%Y%m%d')}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md + "\n")
    log(f"結果を保存しました: {out_path}")


if __name__ == "__main__":
    main()
