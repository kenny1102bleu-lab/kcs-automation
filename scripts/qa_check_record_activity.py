"""秘書室の朝の報告が残す「社内の動き」（Firestore の activity）の回帰テスト。
実際の Firestore には書かない（偽物のクライアントで、書く内容だけを確かめる）。

    python scripts/qa_check_record_activity.py
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scripts import secretary_morning as sm  # noqa: E402

JST = sm.JST


class FakeDoc:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def set(self, data):
        self.store[self.path] = data


class FakeCollection:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def document(self, doc_id):
        return FakeDoc(self.store, f"{self.name}/{doc_id}")


class FakeDb:
    def __init__(self):
        self.store = {}

    def collection(self, name):
        return FakeCollection(self.store, name)


def check(cond, msg):
    if not cond:
        print(f"NG: {msg}")
        sys.exit(1)
    print(f"OK: {msg}")


now = dt.datetime(2026, 11, 19, 8, 0, tzinfo=JST)

# 1) 書く内容: 同じ日は1本（id が日付）、秘書、名義と事業つき、時刻は UTC
fake = FakeDb()
sm.firestore_client = lambda: fake
sm.record_activity(now, 2)
doc = fake.store.get("activity/act_morning_20261119")
check(doc is not None, "activity/act_morning_20261119 を書いた")
check(doc["staffId"] == "sakura" and doc["kind"] == "done", "秘書（sakura）の done")
check(doc["text"] == "朝の報告を送った（承認待ち 2件）", "本文に承認待ちの件数が入る")
check(doc["entityId"] == "corp" and doc["businessId"] == "company_ops", "名義と事業を持つ（絶対ルール1）")
check(doc["at"] == "2026-11-18T23:00:00+00:00", "時刻は UTC で保存（JST 8:00 = 前日 23:00 UTC）")

# 2) 承認待ちを数えられなかったとき（未接続）は件数を書かない
fake2 = FakeDb()
sm.firestore_client = lambda: fake2
sm.record_activity(now, None)
check(fake2.store["activity/act_morning_20261119"]["text"] == "朝の報告を送った", "件数が不明なら本文に入れない")

# 3) 鍵が無ければ何もしない
sm.firestore_client = lambda: None
sm.record_activity(now, 2)
check(True, "鍵が無ければ何も書かず、止まらない")


# 4) 書けなかったとき: 報告は送り済みなので止めない（例外を外に出さない）
def boom():
    raise RuntimeError("down")


sm.firestore_client = boom
sm.record_activity(now, 2)
check(True, "Firestore が落ちていても止まらない（ログには出る）")

print("すべて通過")
