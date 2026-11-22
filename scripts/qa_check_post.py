"""X への投稿（hq_post.py）の回帰テスト。X・Firestore・Discord には触らない（偽物で確かめる）。

    python scripts/qa_check_post.py
"""
import contextlib
import datetime as dt
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scripts import hq_post as hp  # noqa: E402

NOW = dt.datetime(2026, 11, 22, 3, 0, tzinfo=dt.timezone.utc)  # 日本時間 12時
TODAY = "2026-11-22"


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


URANAI = {"name": "占い", "businessId": "uranai", "entityId": "corp", "rules": ["r"], "avoidWords": []}
EMMA = {"name": "エマ", "businessId": "adult", "entityId": "personal", "rules": ["r"], "topicPolicy": {"requireNoPerson": True}, "avoidWords": []}
GOOD = "満月はやさしい傾向があります🌙 続きは公式LINEで"


def approval(account, title, planned=TODAY, status="承認", **kw):
    return {"title": title, "text": GOOD, "reply": "", "kind": "x_post", "accountId": account, "ideaId": f"idea_{title}",
            "plannedDate": planned, "status": status, "businessId": URANAI["businessId"] if account == "uranai" else "adult",
            "createdAt": f"2026-11-21T00:00:0{len(title) % 10}+00:00", "expiresAt": f"{planned}T23:59:00+09:00", **kw}


def new_db(approvals, x_posts=None):
    return FakeDb({
        "knowledge": {"plan_uranai": URANAI, "plan_emma": EMMA, "discord": {"channels": {"general": "G", "mgr_uranai": "U"}}},
        "businesses": {"uranai": {"managerId": "mgr_uranai"}, "adult": {}},
        "approvals": json.loads(json.dumps(approvals, ensure_ascii=False)),
        "x_posts": dict(x_posts or {}),
        "ideas": {},
    })


def fake_post(fail=0):
    calls = []

    def post(prefix, text, reply_to=""):
        calls.append({"prefix": prefix, "text": text, "reply_to": reply_to})
        if len(calls) <= fail:
            raise RuntimeError("HTTP 503")
        return f"T{len(calls)}"

    post.calls = calls
    return post


def fake_send():
    sent = []

    def send(channel, message):
        sent.append((channel, message))
        return True

    send.sent = sent
    return send


def go(db, now=NOW, accounts=("uranai", "emma"), keys=lambda p: True, post=None, send=None, dry_run=False):
    post, send = post or fake_post(), send or fake_send()
    res, log = quiet(lambda: hp.run(db, dry_run=dry_run, post=post, send=send, now=now, accounts=accounts, keys=keys))
    return res, log, post, send


# 1) スイッチ・鍵・時間
check(hp.enabled_accounts() == set() or "KCS_X_POST_ACCOUNTS" in os.environ, "スイッチの既定は空（どこにも投稿しない）")
os.environ["KCS_X_POST_ACCOUNTS"] = " uranai, ,emma "
check(hp.enabled_accounts() == {"uranai", "emma"}, "スイッチはカンマ区切りで読む（空白・空は捨てる）")
del os.environ["KCS_X_POST_ACCOUNTS"]
check(hp.key_prefix("uranai", {}) == "URANAI" and hp.key_prefix("emma", {"xKeyPrefix": "emma_x"}) == "EMMA_X", "鍵の接頭辞は、アカウントIDの大文字（元データで変えられる）")
check(hp.quiet_hour(dt.datetime(2026, 11, 22, 23, 0)) and hp.quiet_hour(dt.datetime(2026, 11, 22, 6, 59)) and not hp.quiet_hour(dt.datetime(2026, 11, 22, 7, 0)), "夜（23時〜7時）は出さない")

res, log, post, _ = go(new_db({}))
check(res["posted"] == 0 and "出すものはありません" in log and not post.calls, "承認済みが無ければ、何もしない")

db = new_db({"a1": approval("uranai", "題A")})
res, log, post, _ = go(db, accounts=())
check(res["off"] == 1 and not post.calls and "postStatus" not in db.data["approvals"]["a1"], "スイッチが切れていれば出さない（印も付けない）")
res, log, post, _ = go(db, keys=lambda p: False)
check(res["nokey"] == 1 and not post.calls, "鍵が無いアカウントは、スイッチが入っていても出さない")
res, log, post, _ = go(db, now=dt.datetime(2026, 11, 22, 15, 0, tzinfo=dt.timezone.utc))
check(res["waiting"] == 1 and not post.calls and "夜なので" in log, "夜（日本時間 0時）は出さない")
db = new_db({"a1": approval("uranai", "題A", planned="2026-11-24")})
res, log, post, _ = go(db)
check(res["waiting"] == 1 and not post.calls, "投稿予定日より前は出さない")
db = new_db({"a1": approval("uranai", "題A", planned="2026-11-21"), "a2": approval("uranai", "却下", status="却下"), "a3": approval("uranai", "待ち", status="待ち")})
res, log, post, _ = go(db)
check(res["expired"] == 1 and db.data["approvals"]["a1"]["postStatus"] == "期限切れ" and not post.calls, "期限を過ぎたものは出さずに「期限切れ」（却下・待ちは見ない）")
res, log, post, _ = go(db)
check(res["expired"] == 0 and "出すものはありません" in log, "期限切れは、次から数えない")

# 2) 出す: 1回の実行で1アカウント1件・記録・通知
db = new_db({"a1": approval("uranai", "題A"), "a2": approval("uranai", "題BB"), "e1": approval("emma", "題E", text="【PR】今夜の1本")})
res, log, post, send = go(db)
ap = db.data["approvals"]
check(res["posted"] == 2 and len(post.calls) == 2, "1回の実行で、1アカウント1件ずつ出す")
check(ap["a1"]["postStatus"] == "投稿済み" and ap["a1"]["tweetId"] == "T1" and "postStatus" not in ap["a2"], "古い順に出し、2件目は次の実行まで待つ")
check(post.calls[0]["prefix"] == "URANAI" and post.calls[0]["text"] == GOOD and post.calls[0]["reply_to"] == "", "本文をそのまま、そのアカウントの鍵で出す")
check(db.data["x_posts"]["a1"]["date"] == TODAY and db.data["x_posts"]["a1"]["tweetId"] == "T1", "投稿の記録を x_posts に残す")
check(db.data["ideas"]["idea_題A"]["tweetId"] == "T1", "案にも投稿のIDを書く")
check(send.sent[0][0] == "U" and "x.com/i/web/status/T1" in send.sent[0][1] and "題A" in send.sent[0][1], "担当のチャンネルへ、投稿のリンクつきで知らせる")
check(send.sent[1][0] == "G", "担当の無い事業は、総合へ知らせる")
check("題A" not in log and GOOD not in log, "ログに題・本文を出さない（公開リポジトリ）")

res, log, post, _ = go(db, now=NOW + dt.timedelta(hours=1))
check(res["posted"] == 0 and res["waiting"] == 1 and not post.calls, "前の投稿から2時間たつまで、次を出さない")
res, log, post, _ = go(db, now=NOW + dt.timedelta(hours=2, minutes=1))
check(res["posted"] == 1 and db.data["approvals"]["a2"]["postStatus"] == "投稿済み", "2時間たったら、次を出す")

logs = {f"p{i}": {"accountId": "uranai", "date": TODAY, "postedAt": "2026-11-21T20:00:00+00:00"} for i in range(4)}
db = new_db({"a1": approval("uranai", "題A")}, x_posts=logs)
res, log, post, _ = go(db)
check(res["limit"] == 1 and not post.calls, "1アカウント1日4件まで")

# 3) 出す直前の審査・リンク
db = new_db({"a1": approval("uranai", "題A", text="詳しくは https://example.com で")})
res, log, post, send = go(db)
check(res["blocked"] == 1 and not post.calls and db.data["approvals"]["a1"]["postStatus"] == "差し止め", "本文にURLがあれば差し止め")
check("差し止め" in send.sent[0][1] and "理由" in send.sent[0][1], "差し止めは、理由つきで知らせる")

db = new_db({"e1": approval("emma", "題E", text="【PR】今夜の1本。リプ欄に置いておくね", reply="これ")})
res, log, post, send = go(db)
check(res["link"] == 1 and not post.calls and db.data["approvals"]["e1"]["postStatus"] == "リンク待ち", "リプ欄の一言があるのにリンクが無ければ、出さない")
check(len(send.sent) == 1 and "リンク待ち" in send.sent[0][1], "リンク待ちを知らせる")
res, log, post, send = go(db)
check(res["link"] == 1 and not send.sent, "リンク待ちの知らせは1回だけ")
db.data["approvals"]["e1"]["link"] = "http://example.com/x"
res, log, post, _ = go(db)
check(res["blocked"] == 1 and not post.calls, "リンクが https でなければ差し止め")
db.data["approvals"]["e1"].update({"link": "https://example.com/x", "postStatus": "リンク待ち"})
res, log, post, _ = go(db)
check(res["posted"] == 1 and len(post.calls) == 2, "リンクが入ったら、本文とリプ欄を出す")
check(post.calls[1]["reply_to"] == "T1" and post.calls[1]["text"] == "これ\nhttps://example.com/x", "リプ欄は、本文への返信として一言＋リンク")

# 4) 失敗・二度出さない
db = new_db({"a1": approval("uranai", "題A")})
res, log, post, send = go(db, post=fake_post(fail=9))
ap = db.data["approvals"]["a1"]
check(res["failed"] == 1 and ap["postStatus"] == "再試行待ち" and ap["postTries"] == 1 and not send.sent, "失敗は、次の実行でやり直す（まだ知らせない）")
check(ap["postError"] == "HTTP 503", "エラーは HTTP の番号だけ残す")
go(db, post=fake_post(fail=9))
res, log, post, send = go(db, post=fake_post(fail=9))
check(db.data["approvals"]["a1"]["postStatus"] == "失敗" and len(send.sent) == 1 and "投稿の失敗" in send.sent[0][1], "3回失敗したら止めて知らせる")
res, log, post, _ = go(db)
check(not post.calls and "出すものはありません" in log, "失敗で止めたものは、もう出さない")

db = new_db({"a1": approval("uranai", "題A", postStatus="投稿中")})
res, log, post, send = go(db)
check(res["unknown"] == 1 and not post.calls and "確認が必要" in send.sent[0][1], "投稿中のまま残ったものは、出さずに知らせる（二度出さない）")
res, log, post, send = go(db)
check(not post.calls and not send.sent, "確認が必要の知らせは1回だけ")

calls = []


def post_reply_fails(prefix, text, reply_to=""):
    calls.append(reply_to)
    if reply_to:
        raise RuntimeError("HTTP 403")
    return "T9"


db = new_db({"e1": approval("emma", "題E", text="【PR】今夜の1本。リプ欄に置いておくね", reply="これ", link="https://example.com/x")})
res, log, _, send = go(db, post=post_reply_fails)
check(db.data["approvals"]["e1"]["postStatus"] == "投稿済み" and db.data["approvals"]["e1"]["replyError"] == "HTTP 403", "リプ欄だけ失敗しても、本文は投稿済み（エラーを残す）")
check("リプ欄の一言は出せませんでした" in send.sent[0][1], "リプ欄の失敗も知らせる")

# 5) dry-run
db = new_db({"a1": approval("uranai", "題A"), "a2": approval("uranai", "古い", planned="2026-11-20")})
before = json.dumps(db.data, ensure_ascii=False, sort_keys=True)
res, log, post, send = go(db, dry_run=True)
check(res["posted"] == 1 and res["expired"] == 1 and not post.calls and not send.sent, "dry-run は数えるだけで、出さない・送らない")
check(json.dumps(db.data, ensure_ascii=False, sort_keys=True) == before and "dry-run" in log, "dry-run は Firestore に書かない")

print("すべて OK")
