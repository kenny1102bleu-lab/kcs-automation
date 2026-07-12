"""
早期リプライ戦略（scripts/early_reply.py）のリプライ履歴を永続化する。

follower_tracker.pyと同じload/save規約。同一著者への短期間の再リプライを
防ぐための重複チェックに使う（HAL・すなくん横断で見る＝2ペルソナが同じ
相手に短期間で群がるのを防ぐ）。
"""
import json
import datetime
import pathlib

DATA_PATH = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "early_reply_history.json"

DEDUP_WINDOW_DAYS = 30
HISTORY_KEEP = 60


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


def recent_author_ids(window_days: int = DEDUP_WINDOW_DAYS) -> set[str]:
    """全アカウント横断で、直近window_days以内にリプライ済みの著者IDを返す。"""
    history = _load()
    cutoff = (datetime.date.today() - datetime.timedelta(days=window_days)).isoformat()
    ids = set()
    for entries in history.values():
        for e in entries:
            if e.get("date", "") >= cutoff:
                ids.add(e.get("target_author_id"))
    return ids


def all_time_tweet_ids() -> set[str]:
    history = _load()
    ids = set()
    for entries in history.values():
        for e in entries:
            ids.add(e.get("target_tweet_id"))
    return ids


def record(account: str, candidate: dict, reply_id: str, reply_text: str) -> None:
    history = _load()
    entries = history.get(account, [])
    entries.append({
        "date": datetime.date.today().isoformat(),
        "target_tweet_id": candidate["tweet_id"],
        "target_author_id": candidate["author_id"],
        "target_author_username": candidate["author_username"],
        "reply_tweet_id": reply_id,
        "reply_text": reply_text,
    })
    history[account] = entries[-HISTORY_KEEP:]
    _save(history)
