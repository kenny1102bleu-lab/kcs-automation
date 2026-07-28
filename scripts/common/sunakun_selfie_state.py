"""
すなくんの週1自撮り投稿の頻度管理。前回投稿日から7日以上経過していれば
次回実行で1回だけ投稿対象にする（follower_tracker.pyと同じ「シート/JSONを
真実の源とする」規約）。
"""
import json
import datetime
import pathlib

STATE_PATH = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "sunakun_selfie_history.json"


def _load() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def should_post_today(min_interval_days: int = 7) -> bool:
    state = _load()
    last = state.get("last_posted_date")
    if not last:
        return True
    last_date = datetime.date.fromisoformat(last)
    return (datetime.date.today() - last_date).days >= min_interval_days


def record_posted() -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps({"last_posted_date": datetime.date.today().isoformat()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
