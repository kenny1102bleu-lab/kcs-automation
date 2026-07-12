"""
WF-10: 早期リプライ戦略（1日1回/アカウント）
HAL/すなくんが同ジャンルの他アカウントの直近投稿に、キャラクターに沿った
自然な一言リプライを送り、その閲覧者に発見してもらう経路を作る。

承認ゲートなし（社長確認済み、2026-07-12: テキストのみの短い返信は通常投稿
ほどのリスクではないと判断）。マモル審査・NG監査・対象ツイートの安全性判定を
全段階通過したときのみ投稿し、それ以外は常にサイレントにスキップする
（scripts/common/self_reply.pyと同じ「既存挙動を絶対に劣化させない」思想）。

使い方:
    python -m scripts.early_reply             # HAL/すなくん両方
    python -m scripts.early_reply --account sunakun
"""
import sys
import os
import json
import argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.candidate_discovery import (
    QUERIES, build_client, fetch_user_info, collect_candidate_tweets,
    FOLLOWER_MIN, FOLLOWER_MAX,
)
from scripts.common.claude_client import call_claude
from scripts.common.discord_notify import notify_account
from scripts.common.mamoru import review
from scripts.common.ng_patterns import scan as ng_scan
from scripts.common.twitter_client import post_reply
from scripts.common.x_limits import truncate_to_fit
from scripts.common import early_reply_store

_JUDGE_PROMPTS = {
    "HAL": (
        "あなたはHAL。21歳、おっとり天然な口調。K-POP/17LIVE/モデル/コスメ界隈のアカウントです。\n"
        "見知らぬアカウントの投稿を見て、HALとして自然な一言リプライを書けるか判断してください。\n\n"
        "【appropriate=falseにすべきケース】\n"
        "- ネガティブ・攻撃的・政治的・下ネタ・センシティブ、または炎上/論争中の話題\n"
        "- 宣伝・広告・アフィリエイトっぽい投稿への相乗りに見えるもの\n"
        "- HALのジャンル（K-POP/17LIVE/モデル/コスメ）と無関係、または文脈が読み取れない投稿\n"
        "- 文章だけでは自然に反応できない投稿（画像前提の内容等）\n\n"
        "【reply_textのルール（appropriate=trueの場合のみ）】\n"
        "- 投稿の実際の内容に触れた、自然な一言（20〜40文字程度）\n"
        "- 宣伝・自己紹介・フォロー訴求は一切しない。あくまで感想・共感・質問\n"
        "- 「AI」「人工知能」「ボット」「自動」「中の人」等の存在否定ワードは絶対に使わない\n"
        "- 絵文字は0〜1個まで\n\n"
        "【出力】JSON形式のみ（説明文・前置き不要）:\n"
        '{"appropriate": true, "reply_text": "本文"}\n'
        "または\n"
        '{"appropriate": false, "skip_reason": "理由"}'
    ),
    "SUNAKUN": (
        "あなたはすなくん。26歳ガジェット愛好家、カジュアルでテンション高めな口調。"
        "ガジェット/Amazon/楽天/モバイルバッテリー界隈のアカウントです。\n"
        "見知らぬアカウントの投稿を見て、すなくんとして自然な一言リプライを書けるか判断してください。\n\n"
        "【appropriate=falseにすべきケース】\n"
        "- ネガティブ・攻撃的・政治的・下ネタ・センシティブ、または炎上/論争中の話題\n"
        "- 宣伝・広告・アフィリエイトっぽい投稿への相乗りに見えるもの\n"
        "- すなくんのジャンル（ガジェット/Amazon/楽天/モバイルバッテリー）と無関係、または文脈が読み取れない投稿\n"
        "- 文章だけでは自然に反応できない投稿（画像前提の内容等）\n\n"
        "【reply_textのルール（appropriate=trueの場合のみ）】\n"
        "- 投稿の実際の内容に触れた、自然な一言（20〜40文字程度）\n"
        "- 宣伝・自己紹介・フォロー訴求は一切しない。あくまで感想・共感・質問\n"
        "- 「AI」「自動」「ボット」「中の人」等の存在否定ワードは絶対に使わない\n"
        "- 絵文字は0〜1個まで\n\n"
        "【出力】JSON形式のみ（説明文・前置き不要）:\n"
        '{"appropriate": true, "reply_text": "本文"}\n'
        "または\n"
        '{"appropriate": false, "skip_reason": "理由"}'
    ),
}


def log(msg: str) -> None:
    print(f"[early_reply] {msg}", file=sys.stderr)


def _augmented_queries(account: str) -> list[str]:
    """スレッドの横取り・リンク付き投稿への相乗りを避けるため、
    candidate_discovery.QUERIES自体は書き換えずローカルで拡張する。"""
    return [f"{q} -is:reply -has:links" for q in QUERIES[account.lower()]]


def _own_user_id(client) -> str | None:
    try:
        res = client.get_me()
        return str(res.data.id) if res.data else None
    except Exception:
        return None


def select_candidate(account: str, client, exclude_author_ids: set[str]) -> dict | None:
    tweets = collect_candidate_tweets(client, _augmented_queries(account), max_results=15)
    if not tweets:
        return None
    tweets.sort(key=lambda t: t["created_at"] or "", reverse=True)

    author_ids = list({t["author_id"] for t in tweets})
    users = fetch_user_info(client, author_ids)

    recent_ids = early_reply_store.recent_author_ids()
    seen_tweet_ids = early_reply_store.all_time_tweet_ids()

    for t in tweets:
        if t["tweet_id"] in seen_tweet_ids:
            continue
        if t["author_id"] in exclude_author_ids:
            continue
        if t["author_id"] in recent_ids:
            continue
        user = users.get(t["author_id"])
        if user is None:
            continue
        followers = (user.public_metrics or {}).get("followers_count", 0)
        if not (FOLLOWER_MIN <= followers <= FOLLOWER_MAX):
            continue
        return {
            "tweet_id": t["tweet_id"],
            "author_id": t["author_id"],
            "author_username": user.username,
            "text": t["text"],
            "followers": followers,
        }
    return None


def judge_and_generate(account: str, candidate: dict) -> dict | None:
    prompt = _JUDGE_PROMPTS.get(account.upper())
    if not prompt:
        return None
    user_message = (
        f"以下は見知らぬアカウント（@{candidate['author_username']}, "
        f"フォロワー{candidate['followers']}人）の投稿です:\n\n{candidate['text']}"
    )
    try:
        raw = call_claude(prompt, user_message).strip()
        if "```" in raw:
            raw = raw.split("```")[1].strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()
        obj, _ = json.JSONDecoder().raw_decode(raw)
    except Exception as e:
        log(f"{account}: 判定生成に失敗: {e}")
        return None
    return obj


def run(account: str, client, exclude_author_ids: set[str]) -> None:
    candidate = select_candidate(account, client, exclude_author_ids)
    if candidate is None:
        log(f"{account}: 本日の早期リプライ対象なし")
        return

    obj = judge_and_generate(account, candidate)
    if not obj or obj.get("appropriate") is not True:
        reason = obj.get("skip_reason") if obj else "生成失敗"
        log(f"{account}: 判定でスキップ ({reason})")
        return

    reply_text = str(obj.get("reply_text", "")).strip().strip("\"'「」\n ")
    if not reply_text:
        log(f"{account}: reply_textが空のためスキップ")
        return

    if ng_scan(reply_text):
        log(f"{account}: NG監査でスキップ")
        return

    for attempt in range(2):
        result = review(reply_text)
        if result["status"] == "approved":
            break
        if attempt == 0 and "fixed_text" in result:
            reply_text = result["fixed_text"]
        else:
            log(f"{account}: マモル審査不承認でスキップ ({result.get('reason', '')})")
            return

    reply_text = truncate_to_fit(reply_text)

    try:
        reply_id = post_reply(reply_text, candidate["tweet_id"], account)
    except Exception as e:
        notify_account(
            f"🔴 **{account}** 早期リプライ投稿失敗\n"
            f"対象: https://x.com/i/web/status/{candidate['tweet_id']}\nエラー: {e}",
            account,
        )
        return

    early_reply_store.record(account, candidate, reply_id, reply_text)

    notify_account(
        f"🐦 **{account}** 早期リプライ完了\n"
        f"対象: @{candidate['author_username']} (フォロワー{candidate['followers']:,}人)\n"
        f"内容: {reply_text}\n"
        f"https://x.com/i/web/status/{reply_id}",
        account,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", choices=["hal", "sunakun"], default=None,
                         help="対象アカウントを絞る（省略時はHAL/SUNAKUN両方）")
    args = parser.parse_args()

    run_accounts = [args.account.upper()] if args.account else ["HAL", "SUNAKUN"]

    # HAL⇔すなくん相互を候補から除外するため、実行対象に関わらず両方の
    # user_idを解決しておく。
    clients = {}
    own_ids = set()
    for a in ("HAL", "SUNAKUN"):
        try:
            clients[a] = build_client(a)
        except RuntimeError as e:
            if a in run_accounts:
                notify_account(f"🔴 **{a}** 早期リプライ: 設定エラー\n{e}", a)
            continue
        uid = _own_user_id(clients[a])
        if uid:
            own_ids.add(uid)

    for account in run_accounts:
        client = clients.get(account)
        if client is None:
            continue
        try:
            run(account, client, own_ids)
        except Exception as e:
            log(f"{account}: 想定外のエラー: {e}")
            notify_account(f"🔴 **{account}** 早期リプライで想定外のエラー\n{e}", account)


if __name__ == "__main__":
    main()
