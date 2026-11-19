"""新しい承認待ち（Firestore の approvals）を、担当マネージャーの Discord チャンネルへ送る。

- 承認待ちは事業（businessId）を持つ。事業には担当マネージャー（managerId）が付いている。
  → その担当のチャンネルへ。対応表が無い・担当が無いときは「総合」へ。
- 送ったものには notifiedAt を付け、二度送らない。
- 対応表（knowledge/discord）がまだ無ければ、何もしない（チャンネルを作るのが先）。
- ログに出すのは件数だけ。承認待ちの題名・取引先名・金額は、公開リポジトリのログに出さない（Discord にだけ送る）。

    python scripts/hq_notify_approvals.py [--dry-run]
"""
import argparse
import datetime as dt
import os
import sys

# `python scripts/xxx.py` で起動すると、リポジトリの直下が import の探す場所に入らない。先に足す（secretary_morning.py と同じ）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import hq_discord as hd  # noqa: E402


def message_of(a: dict) -> str:
    dept = a.get("department") or ""
    return (
        f"**承認待ち**（{dept}）\n{a.get('title', '')}\n"
        "→ 中枢アプリの「秘書室」で、承認・却下してください"
    )


def run(db, dry_run: bool = False, send=None, now=None) -> dict:
    send = send or hd.send_to_channel
    now = now or dt.datetime.now(dt.timezone.utc)
    doc = db.collection(hd.DOC[0]).document(hd.DOC[1]).get()
    if not doc.exists:
        print("[通知] Discord の対応表がまだありません（チャンネル作成が先）。何もしません")
        return {"skipped": True, "sent": 0, "failed": 0, "new": 0}
    channels = (doc.to_dict() or {}).get("channels", {})
    businesses = {d.id: d.to_dict() for d in db.collection("businesses").stream()}
    waiting = [
        d for d in db.collection("approvals").where("status", "==", "待ち").stream()
    ]
    fresh = [d for d in waiting if not (d.to_dict() or {}).get("notifiedAt")]
    sent = failed = 0
    for d in fresh:
        a = d.to_dict() or {}
        channel_id = hd.channel_for(a, businesses, channels)
        if not channel_id:
            failed += 1
            continue
        if dry_run:
            sent += 1
            continue
        if send(channel_id, message_of(a)):
            db.collection("approvals").document(d.id).update({"notifiedAt": now.isoformat()})
            sent += 1
        else:
            failed += 1
    tail = "（dry-run: 送っていません）" if dry_run else ""
    print(f"[通知] 承認待ち {len(waiting)}件のうち新しいもの {len(fresh)}件 → 送信 {sent}件・失敗 {failed}件{tail}")
    return {"skipped": False, "sent": sent, "failed": failed, "new": len(fresh)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    db = hd.firestore_client()
    if db is None:
        print("[通知] FIREBASE_SERVICE_ACCOUNT_JSON がありません")
        return 1
    run(db, dry_run=args.dry_run)
    return 0  # 失敗の件数はログに出る。30分ごとに失敗メールが飛ばないよう、終了コードは 0（鍵の問題などは上で 1）


if __name__ == "__main__":
    sys.exit(main())
