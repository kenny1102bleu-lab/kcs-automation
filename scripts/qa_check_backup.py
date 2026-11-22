"""バックアップ（hq_backup.py）と戻す仕組み（hq_restore.py）の確認。本物の Firestore は使わない。

    python scripts/qa_check_backup.py
"""
import contextlib
import datetime
import io
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scripts import hq_backup as hb  # noqa: E402
from scripts import hq_restore as hr  # noqa: E402

SECRET = "桜亭 110,000円"  # ログに出てはいけない中身の見本


def check(cond, msg):
    if not cond:
        print(f"NG: {msg}")
        sys.exit(1)
    print(f"OK: {msg}")


# ---- 偽の Firestore ----

class FakeDB:
    def __init__(self):
        self.store = {}  # パス -> data（ドキュメントのパスは "col/id" や "col/id/sub/id2"）
        self.writes = 0

    def collections(self):
        names = sorted({p.split("/")[0] for p in self.store})
        return [FakeCol(self, n) for n in names]

    def collection(self, name):
        return FakeCol(self, name)

    def document(self, path):
        col, _, doc_id = path.rpartition("/")
        return FakeRef(self, col, doc_id)

    def batch(self):
        return FakeBatch(self)


class FakeCol:
    def __init__(self, db, path):
        self.db, self.path = db, path
        self.id = path.split("/")[-1]

    def stream(self):
        depth = self.path.count("/") + 2
        for p in sorted(self.db.store):
            if p.startswith(self.path + "/") and p.count("/") + 1 == depth:
                yield FakeSnap(self.db, p)

    def document(self, doc_id):
        return FakeRef(self.db, self.path, doc_id)


class DocumentReference:  # 型の名前で参照と見分けるので、本物と同じ名前にする
    pass


class FakeRef(DocumentReference):
    def __init__(self, db, col_path, doc_id):
        self.db, self.id = db, doc_id
        self.path = f"{col_path}/{doc_id}"

    def collection(self, name):
        return FakeCol(self.db, f"{self.path}/{name}")

    def collections(self):
        depth = self.path.count("/") + 3
        names = sorted({p.split("/")[depth - 2] for p in self.db.store
                        if p.startswith(self.path + "/") and p.count("/") + 1 == depth})
        return [FakeCol(self.db, f"{self.path}/{n}") for n in names]

    def __eq__(self, other):
        return isinstance(other, FakeRef) and other.path == self.path


class FakeSnap:
    def __init__(self, db, path):
        self.db = db
        col, _, self.id = path.rpartition("/")
        self.reference = FakeRef(db, col, self.id)
        self._data = db.store[path]

    def to_dict(self):
        return dict(self._data)


class FakeBatch:
    def __init__(self, db):
        self.db, self.ops = db, []

    def set(self, ref, data):
        self.ops.append((ref.path, data))

    def commit(self):
        for path, data in self.ops:
            self.db.store[path] = data
            self.db.writes += 1


class GeoLike:
    latitude, longitude = 36.38, 140.47


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = fn(*a, **k)
    return res, buf.getvalue()


TS = datetime.datetime(2026, 11, 22, 3, 40, 1, 123456, tzinfo=datetime.timezone.utc)


def sample_db():
    db = FakeDB()
    db.store = {
        "approvals/a1": {"title": SECRET, "amount": 110000, "at": TS, "ok": True, "none": None},
        "approvals/a2": {"title": "二件目", "tags": ["x", "y"], "nested": {"__type": "まぎらわしい", "n": 1.5}},
        "ideas/i1": {"text": "案", "ref": FakeRef(db, "approvals", "a1"), "blob": b"\x00\x01kcs"},
        "ideas/i1/notes/n1": {"memo": "サブコレクションの中"},
        "x_posts/p1": {"tweetId": "123", "postedAt": TS},
    }
    return db


# ---- 1) 型の印 ----
v = {"at": TS, "blob": b"\x00\xff", "m": {"__type": "x", "k": [1, 2.5, None]}, "nan": float("nan")}
enc = hb.encode(v)
check(json.loads(json.dumps(enc)) == enc, "印を付けた形は JSON にできる")
dec = hb.decode(json.loads(json.dumps(enc)))
check(dec["at"] == TS and dec["blob"] == b"\x00\xff", "日時とバイト列が元に戻る")
check(dec["m"] == {"__type": "x", "k": [1, 2.5, None]}, "中身に __type という欄があっても取り違えない")
check(dec["nan"] != dec["nan"], "NaN も残せる")
naive = hb.encode(datetime.datetime(2026, 1, 1, 0, 0))
check(naive["value"].endswith("+00:00"), "時差の無い日時は UTC として残す")
db0 = FakeDB()
r = hb.encode(FakeRef(db0, "approvals", "a1"))
check(r == {"__type": "ref", "value": "approvals/a1"}, "参照はパスで残す")
check(hb.decode(r, db0).path == "approvals/a1", "参照は db から元に戻る")
check(hb.encode(GeoLike()) == {"__type": "geo", "lat": 36.38, "lng": 140.47}, "位置は緯度・経度で残す")
try:
    hb.encode(object())
    check(False, "知らない型は止める")
except TypeError:
    check(True, "知らない型は黙って捨てずに止める")

# ---- 2) 読み出し ----
db = sample_db()
snap = hb.take_snapshot(db, now=TS)
cols = snap["collections"]
check(sorted(cols) == ["approvals", "ideas", "x_posts"], "全部のコレクションを読む（一覧を持たない）")
check(cols["ideas"]["i1"]["sub"]["notes"]["n1"]["data"]["memo"] == "サブコレクションの中", "サブコレクションもたどる")
man = hb.manifest_of(snap)
check(man["documents"] == 5 and man["collections"] == 3, "数はサブコレクションも含めて数える")
check(man["counts"] == {"approvals": 2, "ideas": 2, "x_posts": 1}, "コレクションごとの件数")

# ---- 3) 書き出しとログ ----
with tempfile.TemporaryDirectory() as out:
    outfile = os.path.join(out, "gh_output")
    os.environ["GITHUB_OUTPUT"] = outfile
    res, log = quiet(hb.run, db, out, now=TS)
    check(SECRET not in log and "桜亭" not in log and "110000" not in log, "ログに中身が出ない")
    check("a1" not in log and "approvals" not in log, "ログにドキュメントの ID もコレクション名も出ない")
    check("ドキュメント 5件" in log, "ログには件数だけ出る")
    files = sorted(os.listdir(os.path.join(out, "snapshot")))
    check(files == ["approvals.json", "ideas.json", "x_posts.json"], "コレクションごとに1ファイル")
    body = json.load(open(os.path.join(out, "snapshot", "approvals.json"), encoding="utf-8"))
    check(body["collection"] == "approvals" and body["docs"]["a1"]["data"]["title"] == SECRET, "ファイルに中身が入る（非公開側）")
    raw = open(os.path.join(out, "snapshot", "approvals.json"), encoding="utf-8").read()
    check("\r\n" not in raw, "改行は LF（毎日の差分が小さい）")
    check(json.load(open(os.path.join(out, "manifest.json")))["documents"] == 5, "manifest.json に件数")
    check("dropped=false" in open(outfile).read(), "減っていないときは dropped=false")

    # 同じ中身なら、ファイルも同じ（コミットが空になる）
    before = {f: open(os.path.join(out, "snapshot", f), encoding="utf-8").read() for f in files}
    quiet(hb.run, db, out, now=TS)
    after = {f: open(os.path.join(out, "snapshot", f), encoding="utf-8").read() for f in files}
    check(before == after, "中身が同じならファイルも同じ（並び順が毎回そろう）")

    # 消えたコレクションのファイルは残さない
    del db.store["x_posts/p1"]
    quiet(hb.run, db, out, now=TS)
    check("x_posts.json" not in os.listdir(os.path.join(out, "snapshot")), "無くなったコレクションのファイルは消す（前の日の分は履歴にある）")

    # 数えるだけは書かない
    db.store["x_posts/p9"] = {"a": 1}
    quiet(hb.run, db, out, dry_run=True, now=TS)
    check("x_posts.json" not in os.listdir(os.path.join(out, "snapshot")), "数えるだけのときは書かない")

    # 大きく減ったら知らせる
    big = FakeDB()
    big.store = {f"ideas/i{k}": {"n": k} for k in range(40)}
    quiet(hb.run, big, out, now=TS)
    for k in range(30):
        del big.store[f"ideas/i{k}"]
    open(outfile, "w").close()
    res, log = quiet(hb.run, big, out, now=TS)
    check(res["dropped"] and "dropped=true" in open(outfile).read(), "前回より半分以上減ったら dropped=true")
    check("半分以上減って" in log, "減ったことをログで知らせる")
    del os.environ["GITHUB_OUTPUT"]

check(hb.dropped({"documents": 10}, {"documents": 0}) is False, "始めたばかり（20件未満）は見張らない")
check(hb.safe_name("../x") == ".._x" and hb.safe_name("..") == "_..", "コレクション名でフォルダの外へ出られない")

with tempfile.TemporaryDirectory() as out:
    hb.write_snapshot(out, {"takenAt": "t", "collections": {"Ideas": {}, "ideas": {}}})
    check(len(os.listdir(os.path.join(out, "snapshot"))) == 2, "大文字小文字だけ違う名前でも上書きしない")
    loaded = hr.load_snapshot(os.path.join(out, "snapshot"))
    check(sorted(loaded) == ["Ideas", "ideas"], "戻すときはファイルの中の名前を使う")

# ---- 4) 戻す ----
db = sample_db()
with tempfile.TemporaryDirectory() as out:
    quiet(hb.run, db, out, now=TS)
    snapshot = hr.load_snapshot(os.path.join(out, "snapshot"))
    original = dict(db.store)

    # 事故: 2件消える（参照とバイト列を持つものも）・1件書き換わる・サブコレクションが消える・あとから1件増える
    del db.store["approvals/a1"]
    db.store["approvals/a2"] = {"title": "書き換えられた"}
    del db.store["ideas/i1/notes/n1"]
    del db.store["ideas/i1"]
    db.store["approvals/a3"] = {"title": "あとから増えた"}

    res, log = quiet(hr.run, db, snapshot, ["approvals", "ideas"])
    check(res["missing"] == 3 and res["changed"] == 1 and res["extra"] == 1, "比べると、無くなった・変わった・増えたが数えられる")
    check(db.writes == 0, "比べるだけのときは何も書かない")
    check(SECRET not in log and "a1" not in log, "戻すときのログにも中身と ID が出ない")

    res, log = quiet(hr.run, db, snapshot, ["approvals", "ideas"], apply=True)
    check(res["written"] == 4, "無くなったものと変わったものだけ書く")
    check(db.store["approvals/a1"]["title"] == SECRET and db.store["approvals/a1"]["at"] == TS, "消えたものが日時の型ごと戻る")
    check(db.store["approvals/a2"] == original["approvals/a2"], "書き換わったものが元に戻る（まぎらわしい欄も）")
    check(db.store["ideas/i1/notes/n1"] == original["ideas/i1/notes/n1"], "サブコレクションの中も戻る")
    check(db.store["ideas/i1"]["ref"].path == "approvals/a1" and db.store["ideas/i1"]["blob"] == b"\x00\x01kcs", "参照とバイト列も戻る")
    check("approvals/a3" in db.store, "あとから増えたものは消さない")

    res, _ = quiet(hr.run, db, snapshot, ["approvals", "ideas"])
    check(res["missing"] == 0 and res["changed"] == 0, "戻したあとに比べると差が無い")

    res, _ = quiet(hr.run, db, snapshot, ["nothing_here"])
    check(res.get("error") == "unknown", "バックアップに無いコレクションを指定したら止める")

# ---- 5) 入口から起動できる ----
here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for script in ("scripts/hq_backup.py", "scripts/hq_restore.py"):
    p = subprocess.run([sys.executable, script, "--help"], cwd=here, capture_output=True, text=True)
    check(p.returncode == 0, f"python {script} の形で起動できる")
env = {k: v for k, v in os.environ.items() if k != "FIREBASE_SERVICE_ACCOUNT_JSON"}
with tempfile.TemporaryDirectory() as out:
    p = subprocess.run([sys.executable, "scripts/hq_backup.py", "--out", out], cwd=here, capture_output=True, text=True, env=env)
    check(p.returncode == 1 and "FIREBASE_SERVICE_ACCOUNT_JSON がありません" in p.stdout, "鍵が無ければ止まる（値は表示しない）")

print("すべて通過")
