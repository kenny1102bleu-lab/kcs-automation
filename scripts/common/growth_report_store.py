"""
ソラ(growth_report.py)の分析結果を永続化し、翌日のジュン専務
(morning_briefing.py / daily_report.py)が読めるようにする。

従来はDiscord通知して終わりで、ディスクに一切残らず翌日の戦略判断に
反映されていなかった（2026-07-12発覚）。follower_tracker.pyと同じ
load/save規約（存在しない/壊れていればNone、書き込み時はmkdir(parents=True)）。
"""
import json
import datetime
import pathlib

REPORT_PATH = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "growth_report_latest.json"


def save(report_text: str, follower_snapshot: dict) -> None:
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    data = {
        "date": now.date().isoformat(),
        "generated_at": now.isoformat(),
        "report_text": report_text,
        "follower_snapshot": follower_snapshot,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load() -> dict | None:
    if not REPORT_PATH.exists():
        return None
    try:
        return json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
