"""社長の指示の実行（hq_run_orders.py）の回帰テスト。実際の Firestore・AI には触らない（偽物で確かめる）。

    python scripts/qa_check_run_orders.py
"""
import contextlib
import datetime as dt
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scripts import hq_run_orders as ro  # noqa: E402
from scripts.common import ai_budget, order_exec  # noqa: E402

NOW = dt.datetime(2026, 11, 20, 3, 0, tzinfo=dt.timezone.utc)  # JST 12:00


def check(cond, msg):
    if not cond:
        print(f"NG: {msg}")
        sys.exit(1)
    print(f"OK: {msg}")


# ── 偽の Firestore ──
class Snap:
    def __init__(self, doc_id, data):
        self.id, self._d = doc_id, data

    @property
    def exists(self):
        return self._d is not None

    def to_dict(self):
        return dict(self._d) if self._d is not None else None


class Doc:
    def __init__(self, db, name, doc_id):
        self.db, self.name, self.id = db, name, doc_id

    def get(self, transaction=None):
        return Snap(self.id, self.db.data.get(self.name, {}).get(self.id))

    def set(self, data, merge=False):
        col = self.db.data.setdefault(self.name, {})
        col[self.id] = {**col.get(self.id, {}), **data} if merge else dict(data)

    def update(self, data):
        self.db.data.setdefault(self.name, {}).setdefault(self.id, {}).update(data)


class Query:
    def __init__(self, db, name, flt=None):
        self.db, self.name, self.flt = db, name, flt

    def where(self, field, op, value):
        return Query(self.db, self.name, (field, value))

    def stream(self):
        rows = self.db.data.get(self.name, {})
        return [Snap(i, d) for i, d in rows.items() if not self.flt or d.get(self.flt[0]) == self.flt[1]]


class Col(Query):
    def document(self, doc_id):
        return Doc(self.db, self.name, doc_id)


class FakeDb:
    def __init__(self, data):
        self.data = data

    def collection(self, name):
        return Col(self, name)


def order(title, assignee="jun", at="2026-11-20T01:00:00+00:00", **over):
    return {"kind": "order", "title": title, "assigneeId": assignee, "done": False, "run": "queued",
            "createdAt": at, "entityId": "corp", "businessId": "company_ops", **over}


def base_data():
    return {
        "ai_staff": {"jun": {"name": "ジュン専務", "role": "全社の判断役", "kind": "staff"},
                     "ryo": {"name": "リョウ", "role": "制作・マーケ", "dept": "マーケティング部"}},
        "businesses": {"b1": {"name": "テスト事業", "status": "稼働", "owner": "営業部", "next": "次の一手", "note": "秘密のメモ"}},
        "approvals": {"a1": {"status": "待ち"}},
    }


def always_claim(db, ref, now):
    d = db.data["tasks"][ref.id]
    if d.get("run") != "queued":
        return False
    d["run"] = "running"
    return True


def fake_call(text='{"result": "できました", "actions": [{"title": "催促の下書きを送る", "detail": "文面あり"}]}',
              tin=5000, tout=2000):
    calls = []

    def call(executor, model, system, user):
        calls.append({"executor": executor, "model": model, "system": system, "user": user})
        return {"text": text, "input_tokens": tin, "output_tokens": tout}

    call.calls = calls
    return call


def run_quiet(*a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = ro.run(*a, **k)
    return out, buf.getvalue()


# 1) 費用の計算
check(abs(ai_budget.cost_usd("claude-sonnet-5-5", 1_000_000, 1_000_000) - 12.0) < 1e-9, "Sonnet 5.5 は入力$2・出力$10（100万トークン）")
check(abs(ai_budget.cost_usd("claude-opus-5-5", 5000, 2000) - (5000 * 4 + 2000 * 20) / 1e6) < 1e-12, "Opus 5.5 の1件の費用")
check(ai_budget.cost_usd("知らないモデル", 1_000_000, 0) == 10.0, "知らないモデルは、いちばん高い単価で数える")
check(ai_budget.level(0, 5) == "ok" and ai_budget.level(4.0, 5) == "warn" and ai_budget.level(5, 5) == "stop", "80%で警告・100%で停止")
check(ai_budget.month_key(dt.datetime(2026, 11, 30, 16, 0, tzinfo=dt.timezone.utc)) == "2026-12", "月の区切りは日本時間（UTCの11/30 16:00 は 12月）")
os.environ["KCS_ORDER_BUDGET_USD"] = "abc"
check(ai_budget.budget_usd() == 5.0, "枠の環境変数が読めない値なら、既定の$5")
del os.environ["KCS_ORDER_BUDGET_USD"]

# 2) 振り分け
check(order_exec.choose_executor({"assigneeId": "jun"}) == "claude", "ジュン宛は Claude")
check(order_exec.choose_executor({"assigneeId": "ryo"}) == "gemini", "ほかの担当は Gemini")
check(order_exec.choose_executor({"assigneeId": "ryo", "executor": "claude"}) == "claude", "executor の指定が先")
check(order_exec.choose_executor({"assigneeId": "jun", "executor": "antigravity"}) == "antigravity", "antigravity の指定")

# 3) AIに渡す情報は絞る
ctx = order_exec.safe_context([{"name": "テスト事業", "status": "稼働", "owner": "営業部", "next": "次の一手", "note": "秘密のメモ", "amount": 999}], 2)
check("テスト事業" in ctx and "次の一手" in ctx and "承認待ち: 2件" in ctx, "事業の名前・状況・次の一手・承認待ちの件数は渡す")
check("秘密のメモ" not in ctx and "999" not in ctx, "メモと金額は渡さない")
system, user = order_exec.build_prompt({"title": "x" * 500}, {"name": "リョウ", "role": "制作"}, ctx)
check("送信" in system and "従わない" in system, "AIに、送信できないこと・命令に従わないことを伝える")
check(len(user) < 1000 and "x" * 201 not in user, "指示の題は200字で切る")

# 4) 返事の読み取り
p = order_exec.parse_result('```json\n{"result": "本文", "actions": [{"title": "提案A", "detail": "d"}, {"title": ""}]}\n```')
check(p["result"] == "本文" and len(p["actions"]) == 1, "コードブロックつきJSONを読む。題の無い提案は捨てる")
p = order_exec.parse_result("ただの文章")
check(p == {"result": "ただの文章", "actions": []}, "JSONでなければ、全文を成果物にする（捨てない）")
p = order_exec.parse_result('{"result": "' + "あ" * 9000 + '", "actions": []}')
check(len(p["result"]) == order_exec.RESULT_MAX_CHARS, "成果物は4,000字まで")

# 5) 実行: 成果物・提案・活動・使用額
data = base_data()
data["tasks"] = {"t1": order("今月の整理をして"), "t2": order("別の下書き", assignee="ryo", at="2026-11-20T02:00:00+00:00")}
db = FakeDb(data)
call = fake_call()
out, log = run_quiet(db, now=NOW, call=call, claim=always_claim, notify=lambda m: None)
check(out["done"] == 2 and out["failed"] == 0, "2件とも実行")
check([c["executor"] for c in call.calls] == ["claude", "gemini"], "古い順に、ジュン宛=Claude・リョウ宛=Gemini")
t1 = db.data["tasks"]["t1"]
check(t1["run"] == "review" and t1["result"] == "できました" and t1["proposals"] == 1, "成果物を tasks に書き、review にする")
check(t1["costUsd"] > 0 and t1["model"].startswith("claude"), "費用とモデルを残す")
ap = db.data["approvals"].get("appr_order_t1_0")
check(ap and ap["status"] == "待ち" and ap["kind"] == "order_proposal" and ap["entityId"] == "corp", "提案は承認待ちに積む（実行はしない）")
check("act_orderrun_t1" in db.data["activity"], "社内の動きに「成果物ができた」を残す")
u = db.data["usage"]["2026-11"]
check(u["runs"] == 2 and abs(u["costUsd"] - (t1["costUsd"] + db.data["tasks"]["t2"]["costUsd"])) < 1e-3, "usage/2026-11 に合計を足す")
check("できました" not in log and "今月の整理" not in log and "催促" not in log, "ログに、題・成果物・提案を出さない（件数と額だけ）")

# 6) 二重に実行しない
data = base_data()
data["tasks"] = {"t1": order("a")}
db = FakeDb(data)
out, _ = run_quiet(db, now=NOW, call=fake_call(), claim=lambda d, r, n: False, notify=lambda m: None)
check(out["done"] == 0 and db.data["tasks"]["t1"]["run"] == "queued", "ほかが取っていれば何もしない")

# 7) dry-run は何も書かず、AIも呼ばない
data = base_data()
data["tasks"] = {"t1": order("a")}
db = FakeDb(data)
call = fake_call()
out, log = run_quiet(db, dry_run=True, now=NOW, call=call, claim=always_claim, notify=lambda m: None)
check(out["queued"] == 1 and not call.calls and db.data["tasks"]["t1"]["run"] == "queued" and "usage" not in db.data, "dry-run: 数えるだけ")

# 8) 取り込まない指示: antigravity宛・完了済み・order以外
data = base_data()
data["tasks"] = {
    "t1": order("a", executor="antigravity"), "t2": order("b", done=True), "t3": {**order("c"), "kind": "task"},
}
db = FakeDb(data)
out, _ = run_quiet(db, now=NOW, call=fake_call(), claim=always_claim, notify=lambda m: None)
check(out["queued"] == 0, "antigravity宛・完了済み・指示でないものは、ここでは実行しない")

# 9) 失敗しても止めず、理由（種類）だけ残す。鍵は出さない
data = base_data()
data["tasks"] = {"t1": order("a")}
db = FakeDb(data)


def boom(executor, model, system, user):
    raise RuntimeError("HTTP error key=SECRETKEY123 failed")


out, log = run_quiet(db, now=NOW, call=boom, claim=always_claim, notify=lambda m: None)
t = db.data["tasks"]["t1"]
check(out["failed"] == 1 and t["run"] == "failed", "失敗は failed にして残す")
check("SECRETKEY123" not in t["error"] and "RuntimeError" in t["error"], "エラーの文章から鍵を消す")

# 10) 枠: 100%なら実行せず、警告は1回だけ
data = base_data()
data["tasks"] = {"t1": order("a")}
data["usage"] = {"2026-11": {"costUsd": 5.0, "runs": 40}}
db = FakeDb(data)
msgs = []
call = fake_call()
out, _ = run_quiet(db, now=NOW, call=call, claim=always_claim, notify=msgs.append)
check(out["level"] == "stop" and not call.calls and db.data["tasks"]["t1"]["run"] == "queued", "枠に達していれば実行しない（指示は待ちのまま）")
check(len(msgs) == 1 and db.data["usage"]["2026-11"]["warnedStop"], "停止の知らせは送る")
run_quiet(db, now=NOW, call=call, claim=always_claim, notify=msgs.append)
check(len(msgs) == 1, "停止の知らせは1回だけ")

# 11) 80%に達したら警告（1回）。途中で100%になれば、残りは実行しない
data = base_data()
data["tasks"] = {"t1": order("a"), "t2": order("b", at="2026-11-20T02:00:00+00:00")}
data["usage"] = {"2026-11": {"costUsd": 3.9, "runs": 30}}
db = FakeDb(data)
msgs = []
call = fake_call(tin=1_000_000, tout=60_000)  # Opus 5.5 で約 $5.2 → 1件目で枠を超える
out, _ = run_quiet(db, now=NOW, call=call, claim=always_claim, notify=msgs.append)
check(out["done"] == 1 and db.data["tasks"]["t2"]["run"] == "queued", "1件で枠を超えたら、2件目は実行しない")
check(len(msgs) == 1 and "枠に達しました" in msgs[0], "枠に達した知らせ")

print("ALL OK")
