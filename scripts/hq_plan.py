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

JST = dt.timezone(dt.timedelta(hours=9))
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_BUDGET_USD = 30.0
# 20案のJSONは日本語で長い（2026-11-21 の初回は 6,000 で切れて、案0件になった）。考える分も含む。1回の天井は約0.34ドル
MAX_OUTPUT_TOKENS = 16000


def call_claude(model: str, system: str, user: str) -> dict:
    from scripts.common.claude_client import client  # 鍵が無いと import で落ちるので、使うときだけ読む

    # 長い出力は、ストリーミングで受ける（時間切れを避ける）
    with client.messages.stream(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
        extra_body={"output_config": {"effort": "medium"}},
    ) as stream:
        r = stream.get_final_message()
    text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    return {"text": text, "input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens, "stop_reason": r.stop_reason}


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


def run_account(db, account: str, dry_run: bool = False, now=None, call=None, get=None, n: int = planner.N_IDEAS,
                skip_if_done: bool = False) -> dict:
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

    topics = planner.eligible_topics(load_topics(db, now, get), plan.get("topicPolicy") or {}, now.isoformat(), plan)
    cutoff = (now - dt.timedelta(days=planner.RECENT_DAYS)).isoformat()
    mine = [d.to_dict() or {} for d in db.collection("ideas").where("accountId", "==", account).stream()]
    recent = [r for r in mine if str(r.get("createdAt") or "") >= cutoff]
    # 毎朝の自動実行（WF-23）で、同じ日に2回お金を使わない（やり直し・手動実行と重なったとき）
    today = now.astimezone(JST).strftime("%Y%m%d")
    if skip_if_done and any(str(r.get("date") or "") == today for r in mine):
        print(f"[企画] {account}: 今日の案はもうあるので、飛ばします")
        return {"ok": True, "skipped": True}
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
    stop = res.get("stop_reason") or ""
    if not ideas:
        # お金を使った返事なので、捨てずに非公開の plan_debug に残す（ログには中身を出さない）
        db.collection("plan_debug").document(f"{date}_{account}").set({
            "accountId": account, "model": model, "stopReason": stop, "outputTokens": res["output_tokens"],
            "text": (res.get("text") or "")[:200000], "createdAt": now.isoformat(),
        })
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
        f"（費用 ${cost:.3f}・今月 ${spent + cost:.2f} / ${budget:.0f}・出力 {res['output_tokens']}トークン・終わり方 {stop or '不明'}）"
    )
    if not ideas:
        print("[企画] 案を読めませんでした。返事は Firestore の plan_debug に残しました")
    return {"ok": True, "ideas": len(ideas), "cost": cost, "counts": counts}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", default="uranai,emma", help="アカウントのID（カンマ区切り）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-if-done", action="store_true", help="今日の案がもうあるアカウントは飛ばす（毎朝の自動実行用）")
    args = ap.parse_args()
    db = hd.firestore_client()
    if db is None:
        print("[企画] FIREBASE_SERVICE_ACCOUNT_JSON がありません")
        return 1
    ok = True
    for account in [a.strip() for a in args.account.split(",") if a.strip()]:
        ok = run_account(db, account, dry_run=args.dry_run, skip_if_done=args.skip_if_done)["ok"] and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
