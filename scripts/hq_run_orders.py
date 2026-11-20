"""社長の指示（Firestore の tasks・kind:'order'・run:'queued'）を、担当のAIスタッフに実行させる（WF-18）。

設計: kcs-hq/departments/system/ai_execution_design.md（2026-11-20 謙一 OK）。
- 実行役は Claude / Gemini（GitHub Actions で動く）。Antigravity は謙一のPCのワーカーが取るので、ここでは触らない。
- AIは文章（成果物と提案）を返すだけ。提案は承認（approvals）に積む。承認は謙一だけ。
- 月の枠（既定5ドル）を超えない。80%で警告（1回）、100%で止める。使った額は usage/YYYY-MM。
- 1回の実行で最大3件。同じ指示を二重に実行しない（トランザクションで取り合う・concurrency で1本だけ動かす）。
- このリポジトリは公開。ログに出すのは件数と合計額だけ。指示の題・成果物・名前は出さない。

    python scripts/hq_run_orders.py [--dry-run] [--max 3]
"""
import argparse
import datetime as dt
import os
import sys

import requests

# `python scripts/xxx.py` で起動すると、リポジトリの直下が import の探す場所に入らない。先に足す（hq_notify_approvals.py と同じ）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import hq_discord as hd  # noqa: E402
from scripts.common import ai_budget, order_exec  # noqa: E402
from scripts.common.env_clean import clean_env, redact_key  # noqa: E402

MAX_OUTPUT_TOKENS = 6000  # 考える分も含む。1件の費用の天井
CLAUDE_DEFAULT_MODEL = "claude-sonnet-5-5"
GEMINI_MODEL = "gemini-2.5-flash"


def pick_model(executor: str, order: dict) -> str:
    if executor == "gemini":
        return GEMINI_MODEL
    if order.get("assigneeId") == "jun":
        from scripts.common.staff_models import model_for  # ジュン専務の判断役モデルは、ここが唯一の窓口

        return model_for("ジュン")
    return CLAUDE_DEFAULT_MODEL


def call_claude(model: str, system: str, user: str) -> dict:
    from scripts.common.claude_client import client  # 鍵が無いと import で落ちるので、使うときだけ読む

    r = client.messages.create(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
        extra_body={"output_config": {"effort": "medium"}},
    )
    text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    return {"text": text, "input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens}


def call_gemini(model: str, system: str, user: str) -> dict:
    # 鍵はURLに付けず、ヘッダーで送る（URLに付けると、エラーの文章から漏れる）
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": clean_env("GEMINI_API_KEY")},
        json={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": MAX_OUTPUT_TOKENS},
        },
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    meta = data.get("usageMetadata", {})
    return {
        "text": "".join(p.get("text", "") for p in parts),
        "input_tokens": meta.get("promptTokenCount", 0),
        "output_tokens": meta.get("candidatesTokenCount", 0) + meta.get("thoughtsTokenCount", 0),
    }


CALLERS = {"claude": call_claude, "gemini": call_gemini}


def real_claim(db, ref, now: dt.datetime) -> bool:
    """queued → running を、トランザクションで取る。取れなければ（もう誰かが取った）False。"""
    from firebase_admin import firestore

    @firestore.transactional
    def txn(tx):
        data = ref.get(transaction=tx).to_dict() or {}
        if data.get("run") != "queued":
            return False
        tx.update(ref, {"run": "running", "runAt": now.isoformat(), "attempts": int(data.get("attempts") or 0) + 1})
        return True

    return txn(db.transaction())


def _notify_budget(db, message: str) -> None:
    """システム担当のチャンネル（無ければ総合）へ。届かなくても止めない。"""
    doc = db.collection(hd.DOC[0]).document(hd.DOC[1]).get()
    channels = (doc.to_dict() or {}).get("channels", {}) if doc.exists else {}
    channel_id = channels.get("mgr_system") or channels.get(hd.GENERAL)
    if channel_id:
        hd.send_to_channel(channel_id, message)


def run(db, dry_run: bool = False, max_orders: int = 3, now=None, call=None, claim=None, notify=None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    claim = claim or real_claim
    notify = notify or (lambda msg: _notify_budget(db, msg))
    key = ai_budget.month_key(now)
    budget = ai_budget.budget_usd()
    usage_ref = db.collection("usage").document(key)
    snap = usage_ref.get()
    usage = (snap.to_dict() or {}) if snap.exists else {}
    spent = float(usage.get("costUsd") or 0)
    runs = int(usage.get("runs") or 0)

    queued = []
    for d in db.collection("tasks").where("run", "==", "queued").stream():
        o = d.to_dict() or {}
        if o.get("kind") == "order" and not o.get("done") and order_exec.choose_executor(o) in order_exec.ACTIONS_EXEC:
            queued.append(d)
    queued.sort(key=lambda d: str((d.to_dict() or {}).get("createdAt") or ""))

    lv = ai_budget.level(spent, budget)
    tail = "（dry-run: 実行しません）" if dry_run else ""
    print(f"[指示] 待ち {len(queued)}件・今月の使用 ${spent:.2f} / 枠 ${budget:.2f}（{lv}）{tail}")
    summary = {"queued": len(queued), "done": 0, "failed": 0, "level": lv, "spent": spent}
    if dry_run or not queued:
        return summary
    if lv == "stop":
        print("[指示] 今月の枠に達しているので、実行しません")
        if not usage.get("warnedStop"):
            notify(f"AI実行の使用額が今月の枠に達しました（${spent:.2f} / ${budget:.2f}）。待ちの指示は止めています。")
            usage_ref.set({"warnedStop": True}, merge=True)
        return summary

    call = call or (lambda ex, model, system, user: CALLERS[ex](model, system, user))
    for d in queued[:max_orders]:
        if ai_budget.level(spent, budget) == "stop":
            break
        ref = db.collection("tasks").document(d.id)
        if not claim(db, ref, now):
            continue
        order = d.to_dict() or {}
        try:
            executor = order_exec.choose_executor(order)
            model = pick_model(executor, order)
            staff_snap = db.collection("ai_staff").document(str(order.get("assigneeId") or "jun")).get()
            staff = (staff_snap.to_dict() or {}) if staff_snap.exists else {}
            businesses = [b.to_dict() or {} for b in db.collection("businesses").stream()]
            waiting = sum(1 for _ in db.collection("approvals").where("status", "==", "待ち").stream())
            system, user = order_exec.build_prompt(order, staff, order_exec.safe_context(businesses, waiting))
            res = call(executor, model, system, user)
            parsed = order_exec.parse_result(res["text"])
            cost = ai_budget.cost_usd(model, res["input_tokens"], res["output_tokens"])
            ref.update({
                "run": "review", "result": parsed["result"], "executor": executor, "model": model,
                "costUsd": round(cost, 4), "proposals": len(parsed["actions"]), "error": "",
            })
            for i, a in enumerate(parsed["actions"]):
                db.collection("approvals").document(f"appr_order_{d.id}_{i}").set({
                    "kind": "order_proposal", "refId": d.id, "title": a["title"],
                    "body": f"{a['detail']}\n\n※AIの提案です。承認すると「この提案を進めてよい」の意味になります（AIは送信・投稿・支払いをしません）。".strip(),
                    "department": staff.get("dept") or "秘書室",
                    "entityId": order.get("entityId") or "corp", "businessId": order.get("businessId") or "company_ops",
                    "status": "待ち", "createdAt": now.isoformat(),
                })
            db.collection("activity").document(f"act_orderrun_{d.id}").set({
                "at": now.isoformat(), "staffId": order.get("assigneeId") or "jun", "kind": "done",
                "text": f"成果物ができた: {str(order.get('title') or '')[:60]}",
                "entityId": order.get("entityId") or "corp", "businessId": order.get("businessId") or "company_ops",
                "refId": d.id,
            })
            spent += cost
            runs += 1
            summary["done"] += 1
            usage_ref.set({"costUsd": round(spent, 4), "runs": runs, "updatedAt": now.isoformat()}, merge=True)
        except Exception as e:  # 種類と短い説明だけ残す。鍵が混ざっても出さない
            ref.update({"run": "failed", "error": f"{type(e).__name__}: {redact_key(str(e))[:200]}"})
            summary["failed"] += 1

    summary["spent"] = spent
    summary["level"] = ai_budget.level(spent, budget)
    if summary["level"] == "warn" and not usage.get("warned80"):
        notify(f"AI実行の使用額が今月の枠の80%に達しました（${spent:.2f} / ${budget:.2f}）。")
        usage_ref.set({"warned80": True}, merge=True)
    elif summary["level"] == "stop" and not usage.get("warnedStop"):
        notify(f"AI実行の使用額が今月の枠に達しました（${spent:.2f} / ${budget:.2f}）。待ちの指示は止めます。")
        usage_ref.set({"warnedStop": True}, merge=True)
    print(f"[指示] 実行 {summary['done']}件・失敗 {summary['failed']}件・今月の使用 ${spent:.2f} / ${budget:.2f}")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max", type=int, default=3)
    args = ap.parse_args()
    db = hd.firestore_client()
    if db is None:
        print("[指示] FIREBASE_SERVICE_ACCOUNT_JSON がありません")
        return 1
    run(db, dry_run=args.dry_run, max_orders=max(1, min(args.max, 5)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
