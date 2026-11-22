"""X への投稿（WF-24）: 秘書室で謙一が承認した下書き（approvals の kind=x_post）を、投稿予定日に X へ出す。

- スイッチ: リポジトリの変数 KCS_X_POST_ACCOUNTS（カンマ区切りのアカウントID）。入っていないアカウントは投稿しない。
  最初は空（＝どこにも投稿しない）。入れるのは、謙一がそのアカウントの X の鍵と費用を確かめてから。
- 鍵: <接頭辞>_TWITTER_API_KEY / _API_SECRET / _ACCESS_TOKEN / _ACCESS_SECRET（接頭辞はアカウントIDの大文字。
  元データの xKeyPrefix で変えられる）。鍵が無いアカウントは、スイッチが入っていても飛ばす。
- 決まり: 1アカウント1日4件まで・前の投稿から2時間あける・1回の実行で1アカウント1件・夜（23時〜7時）は出さない。
  投稿予定日を過ぎたもの（expiresAt を過ぎたもの）は出さずに「期限切れ」にする。
- 出す直前に、もう一度コードの審査（URL・NGの表現・長さなど）を通す。通らなければ「差し止め」。
- リプ欄の一言があるものは、リンク（approvals の link）が入るまで出さない（本文が「リプ欄に置いておくね」と言っているため）。
- 二度出さない: 出す前に「投稿中」と書く。投稿中のまま残ったもの（出したかどうか分からないもの）は、自動でやり直さず知らせる。
- 投稿した記録は x_posts（非公開）。結果は担当の Discord チャンネルへ。
- このリポジトリは公開。ログに出すのは件数だけ（本文・題・アカウントの中身は出さない）。

    python scripts/hq_post.py [--dry-run]
"""
import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import hq_discord as hd  # noqa: E402
from scripts.common import post_writer as pw  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
MAX_PER_DAY = 4
MIN_GAP_HOURS = 2
QUIET = (23, 7)  # この時間（日本時間）は出さない: 23時〜翌7時
MAX_TRIES = 3
API = "https://api.twitter.com/2/tweets"
KEY_NAMES = ("API_KEY", "API_SECRET", "ACCESS_TOKEN", "ACCESS_SECRET")
# これ以上は動かさない状態（投稿中は別に扱う: 出したかどうか分からないので知らせる）
FINISHED = ("投稿済み", "期限切れ", "差し止め", "失敗")


def enabled_accounts() -> set[str]:
    return {a.strip() for a in os.environ.get("KCS_X_POST_ACCOUNTS", "").split(",") if a.strip()}


def key_prefix(account: str, plan: dict) -> str:
    return str(plan.get("xKeyPrefix") or account).upper()


def has_keys(prefix: str) -> bool:
    from scripts.common.env_clean import clean_env

    return all(clean_env(f"{prefix}_TWITTER_{k}") for k in KEY_NAMES)


def x_post(prefix: str, text: str, reply_to: str = "") -> str:
    """X に1件出して、投稿のIDを返す。失敗は例外（中身は HTTP の番号だけにする）。"""
    from scripts.common.x_api_raw import oauth1_session

    session = oauth1_session(prefix)
    if session is None:
        raise RuntimeError("鍵がありません")
    body = {"text": text}
    if reply_to:
        body["reply"] = {"in_reply_to_tweet_id": reply_to}
    r = session.post(API, json=body, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"HTTP {r.status_code}")
    return str(r.json()["data"]["id"])


def final_check(a: dict, plan: dict) -> list[str]:
    """出す直前の審査。日付・時刻の情報接地は、書いたとき（WF-21）に確かめ済みなので、ここでは見ない。"""
    post = {"text": a.get("text", ""), "reply": a.get("reply", "")}
    problems = pw.review(post, plan, facts_text=f"{post['text']}\n{post['reply']}")
    link = str(a.get("link") or "").strip()
    if link and not link.startswith("https://"):
        problems.append("リンクが https で始まっていない")
    return problems


def quiet_hour(now_jst: dt.datetime) -> bool:
    start, end = QUIET
    return now_jst.hour >= start or now_jst.hour < end


def message_of(kind: str, a: dict, plan: dict, detail: str = "") -> str:
    name = plan.get("name") or a.get("accountId", "")
    head = {
        "posted": f"**X に投稿しました**（{name}）",
        "link": f"**リンク待ち**（{name}）: リプ欄に置くリンクが無いので、まだ出していません",
        "blocked": f"**差し止め**（{name}）: 出す直前の審査に通りませんでした",
        "failed": f"**投稿の失敗**（{name}）: {MAX_TRIES}回やって出せませんでした",
        "unknown": f"**確認が必要**（{name}）: 投稿の途中で止まりました。X に出ているか見てください",
    }[kind]
    return "\n".join(x for x in (head, a.get("title", ""), detail) if x)


def run(db, dry_run: bool = False, post=None, send=None, now=None, accounts=None, keys=None) -> dict:
    post = post or x_post
    send = send or hd.send_to_channel
    now = now or dt.datetime.now(dt.timezone.utc)
    now_jst = now.astimezone(JST)
    today = now_jst.date().isoformat()
    accounts = enabled_accounts() if accounts is None else set(accounts)
    keys = keys or has_keys
    stats = dict.fromkeys(("posted", "waiting", "expired", "link", "blocked", "failed", "unknown", "off", "nokey", "limit"), 0)

    approved = [(d.id, d.to_dict() or {}) for d in db.collection("approvals").where("status", "==", "承認").stream()]
    rows = [(i, a) for i, a in approved if a.get("kind") == "x_post" and a.get("postStatus") not in FINISHED
            and not (a.get("postStatus") == "投稿中" and a.get("unknownNotifiedAt"))]
    if not rows:
        print("[投稿] 出すものはありません")
        return {"ok": True, **stats}

    doc = db.collection(hd.DOC[0]).document(hd.DOC[1]).get()
    channels = hd.effective_channels(doc.to_dict() or {}) if doc.exists else {}
    businesses = {d.id: d.to_dict() for d in db.collection("businesses").stream()}
    plans, posted_log = {}, {}

    def plan_of(account):
        if account not in plans:
            snap = db.collection("knowledge").document(f"plan_{account}").get()
            plans[account] = (snap.to_dict() or {}) if snap.exists else {}
        return plans[account]

    def log_of(account):
        if account not in posted_log:
            posted_log[account] = [d.to_dict() or {} for d in db.collection("x_posts").where("accountId", "==", account).stream()]
        return posted_log[account]

    def tell(a, kind, detail=""):
        if dry_run:
            return True
        channel = hd.channel_for(a, businesses, channels)
        return bool(channel) and send(channel, message_of(kind, a, plan_of(a.get("accountId", "")), detail))

    def mark(doc_id, data):
        if not dry_run:
            db.collection("approvals").document(doc_id).set(data, merge=True)

    rows.sort(key=lambda r: (str(r[1].get("plannedDate") or ""), str(r[1].get("createdAt") or "")))
    done_this_run = set()
    quiet = quiet_hour(now_jst)
    for doc_id, a in rows:
        account = a.get("accountId", "")
        if a.get("postStatus") == "投稿中":
            # 前の実行が、出したかどうか分からないまま止まった。二度出さないよう、自動ではやり直さない
            stats["unknown"] += 1
            if tell(a, "unknown"):
                mark(doc_id, {"unknownNotifiedAt": now.isoformat()})
            continue
        if str(a.get("expiresAt") or "9999") < now.isoformat():
            stats["expired"] += 1
            mark(doc_id, {"postStatus": "期限切れ", "postCheckedAt": now.isoformat()})
            continue
        if str(a.get("plannedDate") or "") > today or quiet:
            stats["waiting"] += 1
            continue
        if account not in accounts:
            stats["off"] += 1
            continue
        plan = plan_of(account)
        prefix = key_prefix(account, plan)
        if not keys(prefix):
            stats["nokey"] += 1
            continue
        if account in done_this_run:
            stats["waiting"] += 1
            continue
        log = log_of(account)
        if sum(1 for p in log if p.get("date") == today) >= MAX_PER_DAY:
            stats["limit"] += 1
            continue
        last = max((str(p.get("postedAt") or "") for p in log), default="")
        if last and last > (now - dt.timedelta(hours=MIN_GAP_HOURS)).isoformat():
            stats["waiting"] += 1
            continue
        problems = final_check(a, plan)
        if problems:
            stats["blocked"] += 1
            mark(doc_id, {"postStatus": "差し止め", "postProblems": problems, "postCheckedAt": now.isoformat()})
            tell(a, "blocked", "理由: " + " / ".join(problems))
            continue
        link = str(a.get("link") or "").strip()
        if a.get("reply") and not link:
            stats["link"] += 1
            if not a.get("linkNotifiedAt") and tell(a, "link", "→ 秘書室の「承認したもの」で、リンクを入れてください"):
                mark(doc_id, {"postStatus": "リンク待ち", "linkNotifiedAt": now.isoformat()})
            continue
        if dry_run:
            stats["posted"] += 1
            done_this_run.add(account)
            continue

        mark(doc_id, {"postStatus": "投稿中", "postStartedAt": now.isoformat()})
        try:
            tweet_id = post(prefix, a["text"])
        except Exception as e:  # noqa: BLE001  中身は HTTP の番号だけ（鍵や本文は出さない）
            tries = int(a.get("postTries") or 0) + 1
            final = tries >= MAX_TRIES
            stats["failed"] += 1
            mark(doc_id, {"postStatus": "失敗" if final else "再試行待ち", "postTries": tries, "postError": str(e)[:80]})
            if final:
                tell(a, "failed", f"最後のエラー: {str(e)[:80]}")
            done_this_run.add(account)
            continue
        reply_id, reply_error = "", ""
        if a.get("reply"):
            try:
                reply_id = post(prefix, f"{a['reply']}\n{link}", reply_to=tweet_id)
            except Exception as e:  # noqa: BLE001
                reply_error = str(e)[:80]
        record = {"accountId": account, "date": today, "approvalId": doc_id, "ideaId": a.get("ideaId", ""),
                  "tweetId": tweet_id, "replyId": reply_id, "postedAt": now.isoformat(),
                  "entityId": a.get("entityId", ""), "businessId": a.get("businessId", "")}
        db.collection("x_posts").document(doc_id).set(record)
        log.append(record)
        mark(doc_id, {"postStatus": "投稿済み", "postedAt": now.isoformat(), "tweetId": tweet_id, "replyId": reply_id,
                      "replyError": reply_error})
        if a.get("ideaId"):
            db.collection("ideas").document(a["ideaId"]).set({"postedAt": now.isoformat(), "tweetId": tweet_id}, merge=True)
        detail = f"https://x.com/i/web/status/{tweet_id}" + (f"\nリプ欄の一言は出せませんでした（{reply_error}）" if reply_error else "")
        tell(a, "posted", detail)
        stats["posted"] += 1
        done_this_run.add(account)

    tail = "（dry-run: 出していません）" if dry_run else ""
    night = "・夜なので出さない" if quiet else ""
    print(
        f"[投稿] 承認済み {len(rows)}件 → 投稿 {stats['posted']}・予定日待ち/間隔待ち {stats['waiting']}・期限切れ {stats['expired']}"
        f"・リンク待ち {stats['link']}・差し止め {stats['blocked']}・失敗 {stats['failed']}・確認が必要 {stats['unknown']}"
        f"・スイッチ切 {stats['off']}・鍵なし {stats['nokey']}・1日の上限 {stats['limit']}{night}{tail}"
    )
    return {"ok": True, **stats}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    db = hd.firestore_client()
    if db is None:
        print("[投稿] FIREBASE_SERVICE_ACCOUNT_JSON がありません")
        return 1
    print(f"[投稿] スイッチが入っているアカウント: {len(enabled_accounts())}件")
    return 0 if run(db, dry_run=args.dry_run)["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
