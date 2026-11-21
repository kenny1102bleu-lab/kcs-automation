"""ライター（WF-21）: 企画会議で選ばれた案を、そのキャラの完成した1投稿にし、審査を通ったものを承認待ちに出す。

設計: kcs-hq/departments/clients/planning_meeting_design.md（作る順番の4・2026-11-21）。
- 投稿はしない。審査（語の規則・情報接地）を通った下書きだけを approvals に「待ち」で出す。
  → WF-16 が担当の Discord チャンネルへ知らせ、謙一が中枢アプリの「秘書室」で承認・却下する。
- 通らなかった下書きは「差し戻し」として、理由つきで ideas の draft に残す（承認待ちには出さない）。
- 対象は、いちばん新しい日の「選ばれた」「実験枠」の案で、まだ下書きの無いもの（1アカウント最大4つ＝1日4投稿まで）。
- AI は Claude（既定 Opus 5.5）。1アカウント1回の呼び出しで、まとめて書く。費用は企画会議と同じ枠（usage/plan-YYYY-MM）。
- このリポジトリは公開。ログに出すのは件数と費用だけ（下書き・案・世界観は出さない）。

    python scripts/hq_write.py --account uranai [--dry-run]
"""
import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import hq_discord as hd  # noqa: E402
from scripts import hq_plan as hp  # noqa: E402
from scripts.common import ai_budget, planner  # noqa: E402
from scripts.common import post_writer as pw  # noqa: E402

MAX_POSTS = 4  # 1日4投稿まで
PICK = ("選ばれた", "実験枠")


def pick_ideas(db, account: str) -> list[tuple[str, dict]]:
    """いちばん新しい日の、選ばれた・実験枠の案で、まだ下書きの無いもの。(ドキュメントID, 案)。"""
    rows = [(d.id, d.to_dict() or {}) for d in db.collection("ideas").where("accountId", "==", account).stream()]
    if not rows:
        return []
    latest = max(str(r.get("date") or "") for _, r in rows)
    out = [(i, r) for i, r in rows if str(r.get("date") or "") == latest and r.get("status") in PICK and not r.get("draft")]
    out.sort(key=lambda x: (PICK.index(x[1]["status"]), x[1].get("rank", 99)))
    return out[:MAX_POSTS]


def run_account(db, account: str, dry_run: bool = False, now=None, call=None, get=None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(hp.JST).date()
    snap = db.collection("knowledge").document(f"plan_{account}").get()
    if not snap.exists:
        print(f"[ライター] {account}: 元データ（knowledge/plan_{account}）がありません")
        return {"ok": False, "reason": "no_plan"}
    plan = snap.to_dict() or {}
    month_key = f"plan-{ai_budget.month_key(now)}"
    usage_ref = db.collection("usage").document(month_key)
    usage_snap = usage_ref.get()
    usage = (usage_snap.to_dict() or {}) if usage_snap.exists else {}
    spent = float(usage.get("costUsd") or 0)
    budget = hp.plan_budget()
    level = ai_budget.level(spent, budget)

    picked = pick_ideas(db, account)
    tail = "（dry-run: AIは呼びません）" if dry_run else ""
    print(f"[ライター] {account}: 書く案 {len(picked)}件・今月の費用 ${spent:.2f} / 枠 ${budget:.0f}（{level}）{tail}")
    if dry_run or not picked:
        return {"ok": True, "picked": len(picked), "level": level}
    if level == "stop":
        print("[ライター] 今月の枠に達しているので、実行しません")
        return {"ok": False, "reason": "budget"}

    # 使ってよい事実: いま有効で、このアカウントが乗ってよい話題の題（＋案の話題）
    topics = planner.eligible_topics(hp.load_topics(db, now, get), plan.get("topicPolicy") or {}, now.isoformat(), plan)
    facts = [t.get("title", "") for t in topics if t.get("title")]
    for _, idea in picked:
        if idea.get("topic") and idea["topic"] not in facts:
            facts.append(idea["topic"])
    items = [{"index": n, "idea": idea, "plannedDate": pw.planned_date(idea.get("topic", ""), today)} for n, (_, idea) in enumerate(picked)]

    system, user = pw.build_prompt(plan, items, facts)
    model = os.environ.get("KCS_WRITE_MODEL", hp.DEFAULT_MODEL)
    call = call or hp.call_claude
    res = call(model, system, user)
    cost = ai_budget.cost_usd(model, res["input_tokens"], res["output_tokens"])
    posts = pw.parse_posts(res["text"])
    facts_text = "\n".join(facts)

    sent = held = 0
    for it, (doc_id, idea) in zip(items, picked):
        post = posts.get(it["index"])
        problems = pw.review(post, plan, facts_text) if post else ["AIの返事に、この案の下書きが無い"]
        planned = it["plannedDate"]
        draft = {
            "text": (post or {}).get("text", ""), "reply": (post or {}).get("reply", ""), "problems": problems,
            "plannedDate": planned.isoformat(), "model": model, "writtenAt": now.isoformat(),
        }
        approval_id = ""
        if not problems:
            approval_id = f"post_{doc_id}"
            body = draft["text"] + (f"\n\n［リプ欄］{draft['reply']}" if draft["reply"] else "")
            # 有効期限: 投稿予定日の終わり（日本時間）。過ぎたら投稿しない
            expires = dt.datetime(planned.year, planned.month, planned.day, 23, 59, tzinfo=hp.JST)
            db.collection("approvals").document(approval_id).set({
                "title": f"X投稿（{plan.get('name') or account}・{planned.month}/{planned.day}）{idea.get('title', '')}",
                "body": body, "text": draft["text"], "reply": draft["reply"],
                "kind": "x_post", "accountId": account, "ideaId": doc_id, "plannedDate": planned.isoformat(),
                "department": "企画会議", "status": "待ち",
                "entityId": plan.get("entityId") or "corp", "businessId": plan.get("businessId") or "company_ops",
                "createdAt": now.isoformat(), "expiresAt": expires.isoformat(),
            })
            sent += 1
        else:
            held += 1
        db.collection("ideas").document(doc_id).set(
            {"draft": draft, "draftStatus": "承認待ち" if approval_id else "差し戻し", "approvalId": approval_id}, merge=True)

    usage_ref.set({"costUsd": round(spent + cost, 4), "runs": int(usage.get("runs") or 0) + 1, "updatedAt": now.isoformat()}, merge=True)
    print(
        f"[ライター] {account}: 下書き {len(picked)}件 → 承認待ち {sent}・差し戻し {held}"
        f"（費用 ${cost:.3f}・今月 ${spent + cost:.2f} / ${budget:.0f}・出力 {res['output_tokens']}トークン・終わり方 {res.get('stop_reason') or '不明'}）"
    )
    return {"ok": True, "picked": len(picked), "sent": sent, "held": held, "cost": cost}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", default="uranai", help="アカウントのID（カンマ区切り）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    db = hd.firestore_client()
    if db is None:
        print("[ライター] FIREBASE_SERVICE_ACCOUNT_JSON がありません")
        return 1
    ok = True
    for account in [a.strip() for a in args.account.split(",") if a.strip()]:
        ok = run_account(db, account, dry_run=args.dry_run)["ok"] and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
