"""見張り（hq_watch.py）の回帰テスト。GitHub・Discord・Firestore には触らない（偽物で確かめる）。

    python scripts/qa_check_watch.py
"""
import contextlib
import datetime as dt
import io
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scripts import hq_watch as hw  # noqa: E402

NOW = dt.datetime(2026, 11, 21, 9, 45, tzinfo=dt.timezone.utc)


def check(cond, msg):
    if not cond:
        print(f"NG: {msg}")
        sys.exit(1)
    print(f"OK: {msg}")


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


class Col:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def document(self, i):
        return Doc(self.db, self.name, i)


class FakeDb:
    def __init__(self, data):
        self.data = data

    def collection(self, name):
        return Col(self, name)


def run_(i, name, conclusion="failure", minutes_ago=10):
    t = (NOW - dt.timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": i, "name": name, "conclusion": conclusion, "created_at": t, "html_url": f"https://github.com/x/actions/runs/{i}"}


def fake_fetch(runs, workflows=()):
    calls = []

    def fetch(path, params=None):
        calls.append((path, dict(params or {})))
        if path == "/actions/runs":
            return {"workflow_runs": [r for r in runs if r["conclusion"] == params["status"]]}
        return {"workflows": list(workflows)}

    fetch.calls = calls
    return fetch


def fake_send(ok=True):
    sent = []

    def send(channel, message):
        sent.append((channel, message))
        return ok

    send.sent = sent
    return send


def quiet(fn):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn()
    return r, buf.getvalue()


DISCORD = {"knowledge": {"discord": {"channels": {"general": "111111111111111111", "mgr_system": "222222222222222222"}}}}
RUNS = [
    run_(1, "WF-18 注文", minutes_ago=5), run_(2, "WF-18 注文", minutes_ago=20), run_(3, "WF-20 企画会議", "timed_out", 30),
    run_(4, "WF-19 スカウト", "startup_failure", 50),
]

# 1) 失敗を、ワークフローごとにまとめて、システム部のチャンネルへ1通で送る
db = FakeDb(dict(DISCORD))
fetch, send = fake_fetch(RUNS), fake_send()
res, log = quiet(lambda: hw.run(db, fetch=fetch, send=send, now=NOW))
check(res["failed"] == 4 and res["workflows"] == 3 and len(send.sent) == 1, "失敗4件を、3本のワークフローにまとめて1通で送る")
ch, msg = send.sent[0]
check(ch == "222222222222222222", "送り先はシステム部（mgr_system）のチャンネル")
check("WF-18 注文: 失敗・11/21 18:40（ほか 1回）" in msg and "時間切れ" in msg and "起動できず" in msg, "日本時間・回数・失敗の種類を書く")
check("https://github.com/x/actions/runs/1" in msg, "最新の失敗の画面へのリンクを付ける")
check({p["status"] for _, p in fetch.calls if _ == "/actions/runs"} == set(hw.FAIL) and all(p["created"].startswith(">=2026-11-20T09:45") for _, p in fetch.calls if _ == "/actions/runs"), "失敗の3種類を、直近24時間で聞く")
check("WF-18" not in log and "https://" not in log, "ログには件数だけ")
st = db.data["system_watch"]["actions"]
check(set(st["notifiedIds"]) == {1, 2, 3, 4} and set(st["lastByWorkflow"]) == {"WF-18 注文", "WF-20 企画会議", "WF-19 スカウト"}, "知らせた実行とワークフローを記録する")

# 2) 2回目: 同じ失敗は送らない。同じワークフローの新しい失敗も、6時間内は控える。別のワークフローは送る
fetch2, send2 = fake_fetch(RUNS + [run_(5, "WF-18 注文", minutes_ago=1), run_(6, "WF-16 通知", minutes_ago=2)]), fake_send()
res2, _ = quiet(lambda: hw.run(db, fetch=fetch2, send=send2, now=NOW + dt.timedelta(minutes=5)))
check(res2["failed"] == 2 and res2["quiet"] == 1 and res2["workflows"] == 1, "同じ失敗は送らず、6時間内の同じワークフローは控える")
check(len(send2.sent) == 1 and "WF-16 通知" in send2.sent[0][1] and "WF-18" not in send2.sent[0][1], "別のワークフローの失敗だけ送る")

# 3) 6時間たてば、同じワークフローでも、また知らせる
later = NOW + dt.timedelta(hours=7)
fetch3, send3 = fake_fetch([{**run_(7, "WF-18 注文"), "created_at": (later - dt.timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")}]), fake_send()
res3, _ = quiet(lambda: hw.run(db, fetch=fetch3, send=send3, now=later))
check(res3["workflows"] == 1 and len(send3.sent) == 1, "6時間たてば、同じワークフローの失敗をまた知らせる")

# 4) 失敗が無ければ送らない（記録は更新する）
fetch4, send4 = fake_fetch([]), fake_send()
res4, _ = quiet(lambda: hw.run(db, fetch=fetch4, send=send4, now=later + dt.timedelta(hours=1)))
check(res4["failed"] == 0 and not send4.sent and db.data["system_watch"]["actions"]["checkedAt"].startswith("2026-11-21T17:45"), "失敗が無ければ送らない。見た時刻は記録する")

# 5) GitHub に止められた定時実行を知らせる（1回だけ）。手で止めたものは知らせない
wfs = [{"name": "WF-09 ニュース", "state": "disabled_inactivity"}, {"name": "WF-01 古い", "state": "disabled_manually"}, {"name": "WF-19", "state": "active"}]
db5 = FakeDb(dict(DISCORD))
fetch5, send5 = fake_fetch([], wfs), fake_send()
res5, _ = quiet(lambda: hw.run(db5, fetch=fetch5, send=send5, now=NOW))
check(res5["disabled"] == 1 and "WF-09 ニュース" in send5.sent[0][1] and "Enable workflow" in send5.sent[0][1] and "WF-01" not in send5.sent[0][1], "60日で止められた定時実行を知らせる（手で止めたものは除く）")
send5b = fake_send()
res5b, _ = quiet(lambda: hw.run(db5, fetch=fake_fetch([], wfs), send=send5b, now=NOW + dt.timedelta(hours=1)))
check(res5b["disabled"] == 0 and not send5b.sent, "止められた定時実行は、1回だけ知らせる")

# 6) 送れなかったら記録しない（次の回にもう一度送る）。dry-run は送らず記録もしない
db6 = FakeDb(dict(DISCORD))
quiet(lambda: hw.run(db6, fetch=fake_fetch(RUNS), send=fake_send(ok=False), now=NOW))
check("system_watch" not in db6.data, "Discord に送れなかったら、記録しない（次の回に、もう一度送る）")
send6 = fake_send()
res6, log6 = quiet(lambda: hw.run(db6, dry_run=True, fetch=fake_fetch(RUNS), send=send6, now=NOW))
check(res6["sent"] and not send6.sent and "system_watch" not in db6.data and "dry-run" in log6, "dry-run は送らず、記録もしない")

# 7) システム部のチャンネルが無ければ総合へ。どちらも無ければ止まる
db7 = FakeDb({"knowledge": {"discord": {"channels": {"general": "111111111111111111"}}}})
send7 = fake_send()
quiet(lambda: hw.run(db7, fetch=fake_fetch(RUNS), send=send7, now=NOW))
check(send7.sent[0][0] == "111111111111111111", "システム部のチャンネルが無ければ、総合へ送る")
res8, _ = quiet(lambda: hw.run(FakeDb({}), fetch=fake_fetch(RUNS), send=fake_send(), now=NOW))
check(res8 == {"ok": False, "reason": "no_channel", "failed": 4}, "送り先が無ければ、送らずに止まる（失敗として残す）")

# 8) コマンドとして起動できる
out = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "hq_watch.py"), "--help"], capture_output=True, text=True, encoding="utf-8")
check(out.returncode == 0, "scripts/hq_watch.py は、コマンドとして起動できる")
print("ALL OK")
