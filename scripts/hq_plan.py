"""企画会議（WF-20）: アカウントごとに、話題 × 世界観 × 形式 × 目的の掛け合わせで案を出し、選んで、Firestore の ideas に書く。

設計: kcs-hq/departments/clients/planning_meeting_design.md（2026-11-20 謙一 決定）。
- 投稿はしない（案を出して記録するだけ）。完成した文章・承認・投稿は、次の段階。
- アカウントの世界観・決まりは、Firestore の knowledge/plan_<id>（非公開）から読む。話題は topics（無ければ、その場で集める）。
- AIは Claude（既定 Opus 5.5・発想は高いモデル）。費用は usage/plan-YYYY-MM に足し、月の枠（既定30ドル・個人の費用）の80%で警告・100%で止める。
- このリポジトリは公開。ログに出すのは件数と費用だけ（案・話題・世界観は出さない）。

    python scripts/hq_plan.py --account uranai [--dry-run]
"""
import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import hq_discord as hd  # noqa: E402
from scripts import hq_scout as hs  # noqa: E402
from scripts.common import ai_budget, planner  # noqa: E402
from scripts.hq_run_orders import call_claude  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_BUDGET_USD = 30.0


def plan_budget() -> float:
    raw = os.environ.get("KCS_PLAN_BUDGET_USD", "").strip()
    try:
        v = float(raw)
        return v if v > 0 else DEFAULT_BUDGET_USD
    except ValueError:
        return DEFAULT_BUDGET_USD


def load_topics(db, now: dt.datetime, get=None) -> list[dict]:
    """topics（スカウトが書いたもの）。1件も無ければ、その場で集める（書かない）。"""
    rows = [{**(d.to_dict() or {}), "id": d.id} for d in db.collection("topics").stream()]
    if rows:
        return rows
    topics, _ = hs.collect(now, get)
    return topics


def run_account(db, account: str, dry_run: bool = False, now=None, call=None, get=None, n: int = planner.N_IDEAS) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    snap = db.collection("knowledge").document(f"plan_{account}").get()
    if not snap.exists:
        print(f"[企画] {account}: 元データ（knowledge/plan_{account}）がありません。中枢アプリで「企画の元データを入れる」を押してください")
        return {"ok": False, "reason": "no_plan"}
    plan = snap.to_dict() or {}
    month_key = f"plan-{ai_budget.month_key(now)}"
    usage_ref = db.collection("usage").document(month_key)
    usage_snap = usage_ref.get()
    usage = (usage_snap.to_dict() or {}) if usage_snap.exists else {}
    spent = float(usage.get("costUsd") or 0)
    budget = plan_budget()
    level = ai_budget.level(spent, budget)

    topics = planner.eligible_topics(load_topics(db, now, get), plan.get("topicPolicy") or {}, now.isoformat())
    cutoff = (now - dt.timedelta(days=planner.RECENT_DAYS)).isoformat()
    recent = [
        d.to_dict() or {} for d in db.collection("ideas").where("accountId", "==", account).stream()
        if str((d.to_dict() or {}).get("createdAt") or "") >= cutoff
    ]
    recent_formats = [r.get("format", "") for r in recent]
    tail = "（dry-run: AIは呼びません）" if dry_run else ""
    print(f"[企画] {account}: 使える話題 {len(topics)}件・直近の案 {len(recent)}件・今月の費用 ${spent:.2f} / 枠 ${budget:.0f}（{level}）{tail}")
    if dry_run:
        return {"ok": True, "topics": len(topics), "recent": len(recent), "level": level}
    if level == "stop":
        print("[企画] 今月の枠に達しているので、実行しません")
        return {"ok": False, "reason": "budget"}

    system, user = planner.build_prompt(plan, topics, recent, n)
    model = os.environ.get("KCS_PLAN_MODEL", DEFAULT_MODEL)
    call = call or (lambda m, s, u: call_claude(m, s, u))
    res = call(model, system, user)
    cost = ai_budget.cost_usd(model, res["input_tokens"], res["output_tokens"])
    ideas = planner.parse_ideas(res["text"], plan, {t["id"] for t in topics})
    chosen = planner.select(ideas, plan, recent_formats)
    date = now.astimezone(JST).strftime("%Y%m%d")
    for i, idea in enumerate(chosen):
        db.collection("ideas").document(f"idea_{date}_{account}_{i:02d}").set({
            **idea, "accountId": account, "date": date, "rank": i, "model": model,
            "topic": next((t.get("title", "") for t in topics if t["id"] == idea["topicId"]), ""),
            "entityId": plan.get("entityId") or "corp", "businessId": plan.get("businessId") or "company_ops",
            "createdAt": now.isoformat(),
        })
    usage_ref.set({"costUsd": round(spent + cost, 4), "runs": int(usage.get("runs") or 0) + 1, "updatedAt": now.isoformat()}, merge=True)
    counts = {s: sum(1 for c in chosen if c["status"] == s) for s in ("選ばれた", "実験枠", "候補", "却下")}
    print(
        f"[企画] {account}: 案 {len(ideas)}件 → 選ばれた {counts['選ばれた']}・実験枠 {counts['実験枠']}・候補 {counts['候補']}・却下 {counts['却下']}"
        f"（費用 ${cost:.3f}・今月 ${spent + cost:.2f} / ${budget:.0f}）"
    )
    return {"ok": True, "ideas": len(ideas), "cost": cost, "counts": counts}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", default="uranai,emma", help="アカウントのID（カンマ区切り）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    db = hd.firestore_client()
    if db is None:
        print("[企画] FIREBASE_SERVICE_ACCOUNT_JSON がありません")
        return 1
    ok = True
    for account in [a.strip() for a in args.account.split(",") if a.strip()]:
        ok = run_account(db, account, dry_run=args.dry_run)["ok"] and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
