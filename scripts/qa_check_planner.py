"""企画会議（hq_plan.py・common/planner.py）の回帰テスト。AI・Firestore には触らない（偽物で確かめる）。

    python scripts/qa_check_planner.py
"""
import contextlib
import datetime as dt
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scripts import hq_plan as hp  # noqa: E402
from scripts.common import planner as pl  # noqa: E402

NOW = dt.datetime(2026, 11, 20, 9, 0, tzinfo=dt.timezone.utc)
NOW_ISO = NOW.isoformat()


def check(cond, msg):
    if not cond:
        print(f"NG: {msg}")
        sys.exit(1)
    print(f"OK: {msg}")


PLAN_A = {  # 占い風: 人物の話題は外すが、それ以外は乗る
    "name": "A", "goal": "g", "funnel": "f", "persona": "p", "rules": ["r"], "entityId": "corp", "businessId": "uranai",
    "formats": ["豆知識", "予想・占い", "ミニドラマ", "あるある", "質問・投票", "クイズ"],
    "topicPolicy": {"allowCategories": None, "requireNoPerson": False, "blockPersonLikely": True},
}
PLAN_B = {  # 人物が出ない話題だけ
    "name": "B", "goal": "g", "funnel": "f", "persona": "p", "rules": ["r"], "entityId": "personal", "businessId": "adult",
    "formats": ["気分で選ぶ", "ひとりごと", "質問・投票"],
    "topicPolicy": {"allowCategories": ["季節", "行事", "天体"], "requireNoPerson": True, "blockPersonLikely": True},
}


def topic(id_, title, **over):
    return {"id": id_, "title": title, "category": "急上昇", "heat": 10, "safe": True, "person": "unknown", "expiresAt": "2026-11-21T00:00:00+00:00", **over}


TOPICS = [
    topic("t_storm", "冬の嵐で3連休が荒れ模様"),
    topic("t_pol", "首相が会談", safe=False, reasons=["政治・戦争"], person="likely"),
    topic("t_who", "田中投手が更改", person="likely"),
    topic("t_old", "古い話題", expiresAt="2026-11-20T08:00:00+00:00"),
    topic("t_moon", "11月25日 満月（目安）", category="天体", person="none", heat=5),
    topic("t_season", "11月22日 小雪（二十四節気）", category="季節", person="none", heat=5),
]

# 1) 乗ってよい話題
a = pl.eligible_topics(TOPICS, PLAN_A["topicPolicy"], NOW_ISO)
check([t["id"] for t in a] == ["t_storm", "t_moon", "t_season"] or [t["id"] for t in a] == ["t_storm", "t_season", "t_moon"], "乗らない印・期限切れ・人物の印のある話題を外す")
b = pl.eligible_topics(TOPICS, PLAN_B["topicPolicy"], NOW_ISO)
check(sorted(t["id"] for t in b) == ["t_moon", "t_season"], "人物が出ないと分かった・許したカテゴリの話題だけ（人物が分からない急上昇は外す）")

# 2) プロンプト: 話題の題・理由は渡すが、URL は渡さない
system, user = pl.build_prompt(PLAN_A, [{**TOPICS[0], "url": "https://secret.example/x", "context": ["理由の見出し"]}], [{"title": "前の案", "format": "豆知識"}])
check("理由の見出し" in user and "前の案" in user and "t_storm" in user, "話題のID・理由・直近の案を渡す")
check("secret.example" not in user and "ネタ" in system and "仮説" in system, "URL は渡さない。掛け合わせと仮説を求める")

# 3) 返事の読み取り
def idea(title, fmt, novelty=3, fit=4, funnel=4, safety=5, ease=4, tid="", **kw):
    return {"title": title, "format": fmt, "topicId": tid, "angle": "a", "hook": "h", "goal": "g", "hypothesis": "y",
            "scores": {"novelty": novelty, "fit": fit, "funnel": funnel, "safety": safety, "ease": ease}, **kw}


raw = json.dumps({"ideas": [idea("案1", "豆知識", tid="t_storm"), idea("形式外", "謎の形式"), idea("", "豆知識"), {"title": "点なし", "format": "豆知識"}, idea("案ID違い", "クイズ", tid="t_nothing")]}, ensure_ascii=False)
ideas = pl.parse_ideas("```json\n" + raw + "\n```", PLAN_A, {"t_storm"})
check([i["title"] for i in ideas] == ["案1", "案ID違い"], "形式が決まりの外・題が空・点が無い案を捨てる")
check(ideas[0]["topicId"] == "t_storm" and ideas[1]["topicId"] == "", "知らない話題IDは空にする")
check(pl.parse_ideas("ただの文章", PLAN_A, set()) == [], "JSONでなければ、空（捨てずに失敗として扱う）")

# 4) 規則で外す（AIの自己採点を信じない）
check(pl.guard(idea("首相の会談を占う", "予想・占い"), PLAN_A) != "", "政治の語を含む案は外す")
check(pl.guard(idea("田中投手と運勢", "豆知識"), PLAN_B) == "人物の話題（このアカウントは乗らない）", "人物の語を含む案は、人物に乗らないアカウントでは外す")
check(pl.guard(idea("夜に会えるかも", "ひとりごと"), PLAN_B) == "決まりに触れる語", "成人向けアカウントでは、会える誘導の語を外す")
check(pl.guard(idea("案", "豆知識", safety=2), PLAN_A) == "安全の点が低い", "安全の自己採点が低い案は外す")
check(pl.guard(idea("冬の嵐と星座", "豆知識"), PLAN_A) == "", "問題のない案は通す")

# 5) 選ぶ: 形式を重ねない・同じ形式の続けすぎは減点・実験枠
cands = [
    idea("高得点の豆知識", "豆知識", novelty=3, fit=5, funnel=5),
    idea("2番目の豆知識", "豆知識", novelty=3, fit=5, funnel=4),
    idea("占い", "予想・占い", novelty=4, fit=4, funnel=4),
    idea("ドラマ", "ミニドラマ", novelty=3, fit=4, funnel=4),
    idea("大胆なクイズ", "クイズ", novelty=5, fit=3, funnel=3),
    idea("政治ネタを占う", "あるある"),
]
chosen = pl.select(cands, PLAN_A, recent_formats=[])
by = {c["title"]: c for c in chosen}
check(by["高得点の豆知識"]["status"] == "選ばれた" and by["2番目の豆知識"]["status"] != "選ばれた", "同じ形式は、1つしか選ばない（3案の形式が重ならない）")
check(sum(1 for c in chosen if c["status"] == "選ばれた") == 3, "上位3案を選ぶ")
check(by["大胆なクイズ"]["status"] == "実験枠", "新しさが高い案を、実験枠にする")
check(by["政治ネタを占う"]["status"] == "却下" and by["政治ネタを占う"]["rejectedFor"], "規則で外した案は「却下」で理由つきに残す")
check(chosen[0]["status"] == "選ばれた" and chosen[-1]["status"] == "却下", "選ばれた順→実験枠→候補→却下の並び")
repeated = pl.select(cands, PLAN_A, recent_formats=["豆知識", "豆知識", "豆知識"])
by2 = {c["title"]: c for c in repeated}
check(by2["高得点の豆知識"]["total"] < by["高得点の豆知識"]["total"], "直近に同じ形式が続いていたら、減点する")


# 6) 実行: Firestore（偽）と AI（偽）
class Snap:
    def __init__(self, d):
        self._d = d
        self.exists = d is not None

    def to_dict(self):
        return dict(self._d) if self._d is not None else None


class Doc:
    def __init__(self, db, col, id_):
        self.db, self.col, self.id = db, col, id_

    def get(self):
        return Snap(self.db.data.get(self.col, {}).get(self.id))

    def set(self, data, merge=False):
        c = self.db.data.setdefault(self.col, {})
        c[self.id] = {**c.get(self.id, {}), **data} if merge else dict(data)


class Query:
    def __init__(self, db, col, flt=None):
        self.db, self.col, self.flt = db, col, flt

    def where(self, f, op, v):
        return Query(self.db, self.col, (f, v))

    def stream(self):
        class D:
            def __init__(self, i, d):
                self.id, self._d = i, d

            def to_dict(self):
                return dict(self._d)

        return [D(i, d) for i, d in self.db.data.get(self.col, {}).items() if not self.flt or d.get(self.flt[0]) == self.flt[1]]

    def document(self, i):
        return Doc(self.db, self.col, i)


class FakeDb:
    def __init__(self, data):
        self.data = data

    def collection(self, name):
        return Query(self, name)


def quiet(fn):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn()
    return r, buf.getvalue()


def fake_call(text=None, tin=4000, tout=6000):
    calls = []

    def call(model, system, user):
        calls.append({"model": model, "system": system, "user": user})
        return {"text": text if text is not None else raw_ok, "input_tokens": tin, "output_tokens": tout}

    call.calls = calls
    return call


raw_ok = json.dumps({"ideas": [idea(f"案{i}", fmt, novelty=3 + (i % 3), tid="t_storm") for i, fmt in enumerate(
    ["豆知識", "予想・占い", "ミニドラマ", "あるある", "質問・投票", "クイズ"] * 3)]}, ensure_ascii=False)

data = {"knowledge": {"plan_uranai": PLAN_A}, "topics": {t["id"]: {k: v for k, v in t.items() if k != "id"} for t in TOPICS}}
db = FakeDb(data)
call = fake_call()
res, log = quiet(lambda: hp.run_account(db, "uranai", now=NOW, call=call))
check(res["ok"] and res["ideas"] == 18, "案を18件読む")
ideas_saved = db.data["ideas"]
check(len(ideas_saved) == 18 and all(v["accountId"] == "uranai" and v["entityId"] == "corp" and v["businessId"] == "uranai" for v in ideas_saved.values()), "案を ideas に、名義・事業つきで記録する（却下も残す）")
check(sum(1 for v in ideas_saved.values() if v["status"] == "選ばれた") == 3, "選ばれた案は3件")
check(call.calls[0]["model"] == "claude-opus-5-5" and "t_storm" in call.calls[0]["user"] and "t_pol" not in call.calls[0]["user"], "高いモデルを使い、乗らない話題は渡さない")
expected_cost = (4000 * 4 + 6000 * 20) / 1e6
check(abs(db.data["usage"]["plan-2026-11"]["costUsd"] - expected_cost) < 1e-3, "費用を usage/plan-2026-11 に足す")
check("案" not in log.replace("案 ", "").replace("案を", "") or "冬の嵐" not in log, "ログに、案・話題の中身を出さない")
check("冬の嵐" not in log and "世界観" not in log, "ログに、話題・世界観を出さない（件数と費用だけ）")

# 7) 直近の案は、次の実行に渡される。元データが無ければ止まる。枠に達したら止まる。dry-run は呼ばない
call2 = fake_call()
later = NOW + dt.timedelta(days=1)
quiet(lambda: hp.run_account(db, "uranai", now=later, call=call2))
check("案0" in call2.calls[0]["user"], "直近の案を、次の実行に渡す（似た案を避けるため）")
res_none, _ = quiet(lambda: hp.run_account(FakeDb({}), "nobody", now=NOW, call=fake_call()))
check(res_none == {"ok": False, "reason": "no_plan"}, "元データ（plan）が無ければ、AIを呼ばずに止まる")
db_full = FakeDb({**data, "usage": {"plan-2026-11": {"costUsd": 30.0}}})
c3 = fake_call()
res_full, _ = quiet(lambda: hp.run_account(db_full, "uranai", now=NOW, call=c3))
check(res_full == {"ok": False, "reason": "budget"} and not c3.calls, "月の枠（30ドル）に達していれば、AIを呼ばない")
db_dry = FakeDb(data.copy())
c4 = fake_call()
res_dry, _ = quiet(lambda: hp.run_account(db_dry, "emma", dry_run=True, now=NOW, call=c4)) if False else (None, "")
db5 = FakeDb({"knowledge": {"plan_emma": PLAN_B}, "topics": data["topics"]})
res5, log5 = quiet(lambda: hp.run_account(db5, "emma", dry_run=True, now=NOW, call=c4))
check(res5["ok"] and res5["topics"] == 2 and not c4.calls and "ideas" not in db5.data, "dry-run は、AIを呼ばず・書かない（エマは人物が出ない話題の2件）")

# 8) 返事がJSONでなかったときは、案0件でも落ちず、費用は記録する（お金は使ったため）
db6 = FakeDb({"knowledge": {"plan_uranai": PLAN_A}, "topics": data["topics"]})
res6, _ = quiet(lambda: hp.run_account(db6, "uranai", now=NOW, call=fake_call(text="ごめんなさい、できません")))
check(res6["ok"] and res6["ideas"] == 0 and db6.data["usage"]["plan-2026-11"]["costUsd"] > 0, "JSONでない返事は、案0件。費用は記録する")

# コマンドとして起動できる
import subprocess  # noqa: E402

r = subprocess.run([sys.executable, "scripts/hq_plan.py", "--help"], cwd=os.path.join(os.path.dirname(__file__), ".."),
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
check(r.returncode == 0 and "usage" in (r.stdout or "").lower(), "scripts/hq_plan.py は、コマンドとして起動できる")
print("ALL OK")
