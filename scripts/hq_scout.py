"""トピックデスクの「スカウト」（WF-19）: ニュース・トレンド・季節の話題を集めて、Firestore の topics に書く。
予算0円（AIを使わない）。設計: kcs-hq/departments/clients/planning_meeting_design.md。

- 取り先は scripts/common/topic_scout.py の SOURCES。**Yahoo!ニュースの RSS は「個人利用にのみ」の条件があるため、既定では使わない**（KCS_SCOUT_YAHOO=1 のときだけ）。1つ取れなくても止めない（ほかを使う）。全部取れなければ失敗にする。
- topics/{id}: 見出し・出どころ・カテゴリ・熱量・安全の印・人物の印・有効期限。同じ話題は、出どころが違っても1件にまとめる。
- 有効期限を3日過ぎたものは消す（最大200件ずつ）。
- 公開リポジトリのログには、件数だけを出す（見出しは出さない）。

    python scripts/hq_scout.py [--dry-run]
"""
import argparse
import datetime as dt
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import hq_discord as hd  # noqa: E402
from scripts.common import topic_scout as ts  # noqa: E402

UA = "Mozilla/5.0 (compatible; KCS-scout/1.0; +https://github.com/kenny1102bleu-lab/kcs-automation)"
KEEP_DAYS = 3


def fetch(url: str, timeout: int = 20) -> str:
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "ja"}, timeout=timeout)
    r.raise_for_status()
    return r.text


def collect(now: dt.datetime, get=None) -> tuple[list[dict], dict]:
    """全部の取り先から集めて、話題にする。(話題, 取り先ごとの結果 {source: 件数 or -1}) を返す。"""
    get = get or fetch
    items, result = [], {}
    for source, url, category in ts.active_sources(os.environ.get("KCS_SCOUT_YAHOO", "")):
        try:
            got = ts.parse_rss(get(url), source, category)
            items += got
            result[source] = len(got)
        except Exception:  # 通信の失敗は、種類だけ残す（URL や本文は出さない）
            result[source] = -1
    topics = ts.build_topics(items, now) + ts.calendar_topics(now)
    return topics, result


def write(db, topics: list[dict], now: dt.datetime) -> dict:
    col = db.collection("topics")
    new = 0
    for t in topics:
        ref = col.document(t["id"])
        snap = ref.get()
        data = {k: v for k, v in t.items() if k != "id"}
        data.update({"entityId": "corp", "businessId": "company_ops"})
        if snap.exists:
            old = snap.to_dict() or {}
            data["firstSeenAt"] = old.get("firstSeenAt") or old.get("collectedAt") or now.isoformat()
            data["sources"] = sorted(set(old.get("sources", [])) | set(data["sources"]))
            data["heat"] = max(int(old.get("heat") or 0), data["heat"])
        else:
            data["firstSeenAt"] = now.isoformat()
            new += 1
        ref.set(data, merge=True)
    # 古いものを消す
    cutoff = (now - dt.timedelta(days=KEEP_DAYS)).isoformat()
    removed = 0
    for d in col.where("expiresAt", "<", cutoff).limit(200).stream():
        col.document(d.id).delete()
        removed += 1
    return {"new": new, "removed": removed}


def run(db, dry_run: bool = False, now=None, get=None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    topics, result = collect(now, get)
    ok = sum(1 for v in result.values() if v >= 0)
    total_news = sum(v for v in result.values() if v > 0)
    unsafe = sum(1 for t in topics if not t["safe"])
    print(
        f"[スカウト] 取り先 {ok}/{len(result)} 件が取れた・見出し {total_news}件 → 話題 {len(topics)}件"
        f"（乗らない印 {unsafe}件）{'（dry-run: 書きません）' if dry_run else ''}"
    )
    if ok == 0:
        print("[スカウト] どの取り先も取れませんでした")
        return {"ok": False, "topics": len(topics)}
    if dry_run:
        return {"ok": True, "topics": len(topics), "new": 0, "removed": 0}
    res = write(db, topics, now)
    print(f"[スカウト] 新しい話題 {res['new']}件・期限が切れて消したもの {res['removed']}件")
    return {"ok": True, "topics": len(topics), **res}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    db = None if args.dry_run else hd.firestore_client()
    if db is None and not args.dry_run:
        print("[スカウト] FIREBASE_SERVICE_ACCOUNT_JSON がありません")
        return 1
    res = run(db, dry_run=args.dry_run)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
