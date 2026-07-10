"""
フォロワー数を1日1回記録し、7日前・30日前との差分を返す。

X APIは2026-02-06〜従量課金(user read $0.010/件)のため、同日内の重複取得は
避け、1アカウントにつき1日1回のみ記録する（record_and_get_delta呼び出し側が
1日1回のワークフローからのみ呼ばれる前提）。
"""
import json
import datetime
import pathlib
import tweepy

from scripts.common.env_clean import clean_env

DATA_PATH = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "follower_history.json"


def _client(account: str) -> tweepy.Client | None:
    prefix = account.upper()
    ck = clean_env(f"{prefix}_TWITTER_API_KEY")
    cs = clean_env(f"{prefix}_TWITTER_API_SECRET")
    at = clean_env(f"{prefix}_TWITTER_ACCESS_TOKEN")
    ats = clean_env(f"{prefix}_TWITTER_ACCESS_SECRET")
    if ck and cs and at and ats:
        return tweepy.Client(consumer_key=ck, consumer_secret=cs, access_token=at, access_token_secret=ats)
    return None


def fetch_current_followers(account: str) -> int | None:
    client = _client(account)
    if client is None:
        return None
    try:
        res = client.get_me(user_fields=["public_metrics"])
        metrics = (res.data.public_metrics or {}) if res.data else {}
        return metrics.get("followers_count")
    except Exception:
        return None


def _load() -> dict:
    if DATA_PATH.exists():
        try:
            return json.loads(DATA_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(history: dict) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def record_and_get_delta(account: str) -> dict:
    """今日のフォロワー数を記録し、7日前・30日前との差分を返す。
    取得失敗時は {"error": ...} を返す。差分の基準日データが無い項目はNone。"""
    current = fetch_current_followers(account)
    if current is None:
        return {"error": "X API取得失敗（認証情報未設定または権限不足）"}

    history = _load()
    entries = history.get(account, [])
    today = datetime.date.today().isoformat()

    if not entries or entries[-1]["date"] != today:
        entries.append({"date": today, "followers": current})
        entries = entries[-90:]
        history[account] = entries
        _save(history)

    def _closest(days_ago: int):
        target = (datetime.date.today() - datetime.timedelta(days=days_ago)).isoformat()
        candidates = [e for e in entries if e["date"] <= target]
        return candidates[-1]["followers"] if candidates else None

    d7 = _closest(7)
    d30 = _closest(30)

    return {
        "current": current,
        "delta_7d": (current - d7) if d7 is not None else None,
        "delta_30d": (current - d30) if d30 is not None else None,
    }
