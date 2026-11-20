"""中枢アプリの Discord 連携（マネージャーごとのチャンネル・承認待ちの振り分け）の回帰テスト。
実際の Firestore・Discord には触らない（偽物で、送る内容と記録だけを確かめる）。

    python scripts/qa_check_hq_discord.py
"""
import contextlib
import io
import json
import os
import sys
import threading
import urllib.request
from http.server import HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bot import webhook_server as ws  # noqa: E402
from scripts import hq_discord as hd  # noqa: E402
from scripts import hq_discord_channels as hc  # noqa: E402
from scripts import hq_notify_approvals as hn  # noqa: E402


def check(cond, msg):
    if not cond:
        print(f"NG: {msg}")
        sys.exit(1)
    print(f"OK: {msg}")


# ── 偽の Firestore ──
class Doc:
    def __init__(self, store, path, data):
        self.store, self.path, self._data = store, path, data
        self.id = path.split("/")[-1]
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None

    def get(self):
        return Doc(self.store, self.path, self.store.get(self.path))

    def set(self, data, merge=False):
        self.store[self.path] = {**(self.store.get(self.path) or {}), **data} if merge else dict(data)

    def update(self, data):
        self.store[self.path] = {**self.store[self.path], **data}


class Query:
    def __init__(self, store, name, cond=None):
        self.store, self.name, self.cond = store, name, cond

    def where(self, field, op, value):
        return Query(self.store, self.name, (field, value))

    def stream(self):
        for path, data in list(self.store.items()):
            if not path.startswith(self.name + "/"):
                continue
            if self.cond and data.get(self.cond[0]) != self.cond[1]:
                continue
            yield Doc(self.store, path, data)

    def document(self, doc_id):
        return Doc(self.store, f"{self.name}/{doc_id}", self.store.get(f"{self.name}/{doc_id}"))


class FakeDb:
    def __init__(self, store):
        self.store = store

    def collection(self, name):
        return Query(self.store, name)


def capture(fn):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn()
    return result, buf.getvalue()


STAFF = {
    "ai_staff/mgr_sales": {"kind": "manager", "name": "営業マネージャー"},
    "ai_staff/mgr_hal": {"kind": "manager", "name": "HAL担当"},
    "ai_staff/jun": {"kind": "staff", "name": "ジュン専務"},
}
BIZ = {
    "businesses/homepage": {"managerId": "mgr_sales"},
    "businesses/hal": {"managerId": "mgr_hal"},
    "businesses/company_ops": {"managerId": ""},
}
SECRET_TITLE = "KCS-2026-0001 桜亭 入金が14日遅れています 110,000円"

# 1) チャンネル名: マネージャーだけ。スタッフ個人は入らない。総合が付く
names = hd.manager_channel_names([{"id": k.split("/")[1], **v} for k, v in STAFF.items()])
check(set(names) == {"general", "mgr_sales", "mgr_hal"}, "チャンネルはマネージャーごと＋総合（スタッフ個人は入らない）")

# 2) 振り分け: 事業の担当マネージャー → 無ければ総合
channels = {"general": "G", "mgr_sales": "S", "mgr_hal": "H"}
biz = {k.split("/")[1]: v for k, v in BIZ.items()}
check(hd.channel_for({"businessId": "homepage"}, biz, channels) == "S", "事業の担当マネージャーのチャンネルへ")
check(hd.channel_for({"businessId": "hal"}, biz, channels) == "H", "タレントの事業は専属マネージャーへ")
check(hd.channel_for({"businessId": "company_ops"}, biz, channels) == "G", "担当が無い事業は総合へ")
check(hd.channel_for({"businessId": "unknown"}, biz, channels) == "G", "不明な事業も総合へ")

# 3) 承認待ちの通知: 新しいものだけ・二度送らない・題名はログに出さない
store = {
    **BIZ,
    "knowledge/discord": {"channels": channels},
    "approvals/a1": {"status": "待ち", "businessId": "homepage", "department": "営業部", "title": SECRET_TITLE},
    "approvals/a2": {"status": "待ち", "businessId": "hal", "department": "営業部", "title": "別件", "notifiedAt": "x"},
    "approvals/a3": {"status": "承認", "businessId": "homepage", "department": "経理部", "title": "済"},
}
sent = []
res, log = capture(lambda: hn.run(FakeDb(store), send=lambda ch, msg: sent.append((ch, msg)) or True))
check(res == {"skipped": False, "sent": 1, "failed": 0, "new": 1}, "新しい承認待ち1件だけを送る（通知済み・決めたものは送らない）")
check(sent[0][0] == "S" and SECRET_TITLE in sent[0][1], "営業の承認待ちは営業マネージャーのチャンネルへ。題名は Discord に届く")
check(store["approvals/a1"].get("notifiedAt"), "送ったものには notifiedAt が付く")
check("桜亭" not in log and "110,000" not in log and "KCS-2026" not in log, "ログに題名・取引先名・金額が出ない（公開ログ対策）")
sent.clear()
res2, _ = capture(lambda: hn.run(FakeDb(store), send=lambda ch, msg: sent.append(1) or True))
check(res2["new"] == 0 and not sent, "もう一度実行しても、二度送らない")

# 4) 送れなかったら、通知済みにしない（次回また送る）
store["approvals/a4"] = {"status": "待ち", "businessId": "homepage", "department": "営業部", "title": "t"}
res3, _ = capture(lambda: hn.run(FakeDb(store), send=lambda ch, msg: False))
check(res3["failed"] == 1 and not store["approvals/a4"].get("notifiedAt"), "送れなかったものは通知済みにしない")

# 5) dry-run は送らず・印も付けない。対応表が無ければ何もしない
res4, _ = capture(lambda: hn.run(FakeDb(store), dry_run=True, send=lambda ch, msg: sent.append("x") or True))
check("x" not in sent and not store["approvals/a4"].get("notifiedAt"), "dry-run は送らず、印も付けない")
res5, _ = capture(lambda: hn.run(FakeDb({"approvals/z": {"status": "待ち"}}), send=lambda ch, msg: True))
check(res5["skipped"] is True, "対応表（knowledge/discord）が無ければ何もしない")

# 6) チャンネル作成: dry-run は何も作らない／作ったら対応表を書く／失敗なら書かない
posted = []
db = FakeDb({**STAFF, **BIZ})
rc, log = capture(lambda: hc.run(db, apply=False, post=lambda p: posted.append(p) or {}))
check(rc == 0 and not posted, "dry-run はボットに何も頼まない")
check("営業マネージャー" not in log and "HAL" not in log, "ログにマネージャーの名前が出ない")
reply = {"channels": {"総合": "11", "営業マネージャー": "22", "HAL担当": "33"}}
rc, _ = capture(lambda: hc.run(db, apply=True, post=lambda p: posted.append(p) or reply))
check(rc == 0 and posted[0]["action"] == "create_channels" and posted[0]["category"] == hd.CATEGORY, "作成をボットに頼む（カテゴリつき）")
check(sorted(posted[0]["names"]) == sorted(["総合", "営業マネージャー", "HAL担当"]), "頼む名前は、総合＋マネージャー")
check(db.store["knowledge/discord"]["channels"] == {"general": "11", "mgr_sales": "22", "mgr_hal": "33"}, "対応表（マネージャーの id → チャンネルID）を Firestore に書く")
db2 = FakeDb({**STAFF})
rc, log = capture(lambda: hc.run(db2, apply=True, post=lambda p: {"error": "forbidden"}))
check(rc == 1 and "knowledge/discord" not in db2.store and "権限" in log, "権限が無いときは、対応表を書かず、理由を出す")
rc, _ = capture(lambda: hc.run(FakeDb({**STAFF}), apply=True, post=lambda p: {"channels": {"総合": "1"}}))
check(rc == 1, "ID が一部しか返らなければ、対応表を書かない")
rc, _ = capture(lambda: hc.run(FakeDb({"ai_staff/jun": {"kind": "staff", "name": "x"}}), apply=True, post=lambda p: reply))
check(rc == 1, "名簿にマネージャーがいなければ、作らない")

# 7) ボット側の受け口: 依頼の検査と、結果を返す
check(ws.validate_channel_request({"names": ["a", " b "], "category": "c"}) == (["a", "b"], "c"), "依頼の名前を整える")
for bad in ({}, {"names": [], "category": "c"}, {"names": ["a"], "category": ""}, {"names": ["a"] * 31, "category": "c"}, {"names": ["x" * 81], "category": "c"}, {"names": [""], "category": "c"}):
    try:
        ws.validate_channel_request(bad)
        check(False, f"おかしな依頼を断る: {bad}")
    except ValueError:
        pass
check(True, "空・多すぎ・長すぎ・カテゴリなしの依頼は断る")

ws.set_channels_callback(lambda names, category: {"channels": {n: "9" for n in names}, "cat": category})
server = HTTPServer(("127.0.0.1", 0), ws.Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
port = server.server_address[1]


def post(body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None


status, body = post({"action": "create_channels", "category": "KCS 事業", "names": ["総合", "経理"]})
check(status == 200 and body["channels"] == {"総合": "9", "経理": "9"} and body["cat"] == "KCS 事業", "create_channels は、結果（チャンネルID）を返す")
status, _ = post({"action": "create_channels", "category": "", "names": ["x"]})
check(status == 400, "おかしな依頼は 400 を返す")
server.shutdown()

# 8) コマンドとして起動できる（入口。GitHub Actions と同じ python scripts/xxx.py の形。import の探す場所の不具合を防ぐ）
import subprocess  # noqa: E402

root = os.path.join(os.path.dirname(__file__), "..")
for script in ("scripts/hq_notify_approvals.py", "scripts/hq_discord_channels.py"):
    r = subprocess.run([sys.executable, script, "--help"], cwd=root, capture_output=True, text=True, encoding="utf-8")
    check(r.returncode == 0 and "usage" in r.stdout.lower(), f"{script} は、コマンドとして起動できる")

# 9) すでにあるチャンネルを使う（existing が channels より優先）。法人／個人の印
eff = hd.effective_channels({"channels": {"general": "G", "mgr_hal": "H"}, "existing": {"mgr_hal": "EXIST", "general": ""}})
check(eff == {"general": "G", "mgr_hal": "EXIST"}, "すでにあるチャンネルの指定が優先。空の指定は無視")
store = {
    **BIZ,
    "knowledge/discord": {"channels": channels, "existing": {"mgr_hal": "777"}},
    "approvals/p1": {"status": "待ち", "businessId": "hal", "department": "クライアント運用部", "title": "t1", "entityId": "personal"},
    "approvals/p2": {"status": "待ち", "businessId": "homepage", "department": "営業部", "title": "t2", "entityId": "corp"},
    "approvals/p3": {"status": "待ち", "businessId": "homepage", "department": "営業部", "title": "t3"},
}
sent = []
capture(lambda: hn.run(FakeDb(store), send=lambda ch, msg: sent.append((ch, msg)) or True))
by = {m.split(chr(10))[1]: (ch, m) for ch, m in sent}
check(by["t1"][0] == "777", "existing に指定したチャンネル（すでにあるもの）へ送る")
check("【個人】" in by["t1"][1] and "【法人】" in by["t2"][1], "通知に【法人】【個人】の印が付く")
check("【" not in by["t3"][1], "名義が無い承認待ちには、印を付けない")

for ok in ({"mgr_hal": "1494812858830032988"}, {"general": "1494714501801447586"}):
    check(hd.valid_link(ok) == ok, f"指定を受け付ける: {list(ok)[0]}")
for bad in ({}, {"hal": "1494812858830032988"}, {"mgr_hal": "abc"}, {"mgr_hal": "123"}, {"mgr_": "1494812858830032988"}, []):
    try:
        hd.valid_link(bad)
        check(False, f"おかしな指定を断る: {bad}")
    except ValueError:
        pass
check(True, "キーが違う・IDが数字でない・短い・空の指定は断る")

store = {"knowledge/discord": {"channels": {"general": "G"}, "category": "KCS 事業"}}
db9 = FakeDb(store)
rc, log = capture(lambda: hc.link(db9, '{"mgr_hal": "1494812858830032988"}'))
check(rc == 0 and store["knowledge/discord"]["existing"] == {"mgr_hal": "1494812858830032988"}, "指定を existing に書く")
check(store["knowledge/discord"]["channels"] == {"general": "G"} and "1494812858830032988" not in log, "作ったチャンネルの対応は消さない。ログにIDは出ない")
rc, _ = capture(lambda: hc.link(db9, "ではない"))
check(rc == 1, "JSONでない指定は、何もしない")
db9.store["knowledge/discord"]["existing"] = {"mgr_hal": "1494812858830032988"}
capture(lambda: hc.run(db9, apply=True, post=lambda p: {"channels": {"総合": "11"}}) if False else None)

print("すべて通過")
