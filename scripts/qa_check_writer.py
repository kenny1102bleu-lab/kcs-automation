"""ライター（hq_write.py・common/post_writer.py）の回帰テスト。AI・Firestore には触らない（偽物で確かめる）。

    python scripts/qa_check_writer.py
"""
import contextlib
import datetime as dt
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scripts import hq_write as hw  # noqa: E402
from scripts.common import post_writer as pw  # noqa: E402

NOW = dt.datetime(2026, 11, 21, 9, 0, tzinfo=dt.timezone.utc)  # 日本時間 18時
TODAY = dt.date(2026, 11, 21)


def check(cond, msg):
    if not cond:
        print(f"NG: {msg}")
        sys.exit(1)
    print(f"OK: {msg}")


URANAI = {
    "name": "占い", "goal": "公式LINEへ", "funnel": "X→LINE", "persona": "p", "rules": ["断定しない", "URLを本文に貼らない"],
    "entityId": "corp", "businessId": "uranai", "voice": {"first": "わたし", "endings": ["〜傾向があります"], "catchphrases": ["c"], "emoji": "1つ", "length": "100字"},
    "topicPolicy": {"requireNoPerson": False}, "avoidWords": ["呪い"],
}
EMMA = {
    "name": "エマ", "goal": "g", "funnel": "f", "persona": "p", "rules": ["PR表示", "リンクは返信欄"],
    "entityId": "personal", "businessId": "adult", "topicPolicy": {"requireNoPerson": True}, "avoidWords": [],
}
HAL = {"name": "HAL", "businessId": "hal", "entityId": "personal", "language": "ja+zh-Hans", "rules": ["r"]}
MORU = {"name": "もる", "businessId": "moru", "entityId": "personal", "voice": {"first": "ぼく", "endings": ["〜だワン", "〜なんだワン"]}, "rules": ["r"]}
FACTS = "11月24日 満月（23時53分ごろ）\n11月22日 小雪（二十四節気）"

# 1) 投稿予定日・Xの文字数
check(pw.planned_date("11月24日 満月（23時53分ごろ）", TODAY) == dt.date(2026, 11, 24), "話題の日が7日以内なら、その日に投稿する")
check(pw.planned_date("11月20日 何か", TODAY) == TODAY and pw.planned_date("", TODAY) == TODAY, "過ぎた話題・日付の無い話題は、今日")
check(pw.planned_date("1月3日 正月", dt.date(2026, 12, 30)) == dt.date(2027, 1, 3), "年をまたぐ日付は、来年として読む")
check(pw.date_label(dt.date(2026, 11, 24)) == "11月24日（火）", "投稿予定日は曜日つきで渡す")
check(pw.x_weight("abc") == 3 and pw.x_weight("あいう") == 6 and pw.x_weight("🌙") == 2, "Xの数え方: 半角1・全角2・絵文字2")

# 2) プロンプト: キャラクターシート・事実・投稿予定日を渡す。URLは渡さない
idea = {"title": "満月の手放し", "format": "予想・占い", "angle": "a", "hook": "h", "goal": "g", "topic": "11月24日 満月（23時53分ごろ）"}
system, user = pw.build_prompt(URANAI, [{"index": 0, "idea": idea, "plannedDate": dt.date(2026, 11, 24)}], ["11月24日 満月（23時53分ごろ）", "11月22日 小雪（二十四節気）"])
check("投稿予定日: 11月24日（火）" in user and "11月22日 小雪" in user and "一人称: わたし" in user, "投稿予定日・使ってよい事実・口調を渡す")
check("完成した投稿を1つだけ" in system and "URL を書かない" in system and "【PR】" in system, "1案だけ・URLなし・PR表示を求める")

# 3) 返事の読み取り（前後の文章・途中で切れた返事）
raw = json.dumps({"posts": [{"index": 0, "text": "本文A", "reply": ""}, {"index": 1, "text": "本文B", "reply": "リプ"}]}, ensure_ascii=False)
check(pw.parse_posts("どうぞ\n" + raw) == {0: {"text": "本文A", "reply": ""}, 1: {"text": "本文B", "reply": "リプ"}}, "JSONの前後に文章があっても読む")
check(list(pw.parse_posts(raw[: raw.index('"index": 1') - 2])) == [0], "途中で切れた返事は、閉じた下書きだけ拾う")
check(pw.parse_posts("ごめんなさい") == {}, "JSONでなければ空")

# 4) 審査（語の規則）
ok = {"text": "11月24日は満月。手放すと軽くなる傾向があります🌙 星座別の続きは公式LINEで", "reply": ""}
check(pw.review(ok, URANAI, FACTS) == [], "決まりを守った下書きは通す")
check(any("NG" in p for p in pw.review({"text": "詳しくは https://example.com で"}, URANAI, FACTS)), "本文のURLは差し戻す")
check(any("NG" in p for p in pw.review({"text": "いかがでしたか？満月の話でした"}, URANAI, FACTS)), "AIっぽい定型文は差し戻す")
check(any("話題に無い日付" in p for p in pw.review({"text": "11月25日の満月に"}, URANAI, FACTS)), "話題に無い日付は差し戻す（情報接地）")
check(any("話題に無い日付" in p for p in pw.review({"text": "満月は22時10分ごろ"}, URANAI, FACTS)), "話題に無い時刻は差し戻す")
check(pw.review({"text": "満月は23時53分ごろ。小雪は11月22日"}, URANAI, FACTS) == [], "話題にある日付・時刻は通す（複数の行事をまとめてもよい）")
check(any("断定" in p for p in pw.review({"text": "満月の夜は必ず叶います"}, URANAI, FACTS)), "占いの断定の語は差し戻す")
check(any("乗らない話題" in p for p in pw.review({"text": "入院中の人へ"}, URANAI, FACTS)), "乗らない話題の語は差し戻す")
check(pw.review({"text": "今日もがんばった人へ。満月はやさしい傾向があります"}, URANAI, FACTS) == [], "「がんばった」は通す")
check(any("避ける語" in p for p in pw.review({"text": "満月の呪い"}, URANAI, FACTS)), "キャラが避ける語は差し戻す")
check(any("長すぎる" in p for p in pw.review({"text": "あ" * 141}, URANAI, FACTS)), "140字を超える本文は差し戻す")
check(pw.review({"text": "あ" * 140}, URANAI, FACTS) == [], "140字ちょうどは通す")
check(any("PR" in p for p in pw.review({"text": "今夜の1本。リプ欄に置いておくね", "reply": "これ"}, EMMA, FACTS)), "エマ: リンクを置く紹介に【PR】が無ければ差し戻す")
check(pw.review({"text": "【PR】今夜の1本。リプ欄に置いておくね", "reply": "これ"}, EMMA, FACTS) == [], "エマ: 【PR】つきなら通す")
check(any("決まりに触れる語" in p for p in pw.review({"text": "【PR】会える夜"}, EMMA, FACTS)), "エマ: 会える誘導の語は差し戻す")
check(any("人物" in p for p in pw.review({"text": "女優の新作"}, EMMA, FACTS)), "エマ: 人物の語は差し戻す（語の一覧はスカウトと同じ）")
check(pw.review({"text": "みなさん、今夜はゆっくりしてね"}, EMMA, FACTS) == [], "エマ: 「みなさん」は人物の語にしない")
check(any("簡体字" in p for p in pw.review({"text": "カムバ待ってむり"}, HAL, FACTS)), "HAL: 中国語（簡体字）の併記が無ければ差し戻す")
check(pw.review({"text": "カムバ待ってむり 等不及了，这次也太好了吧"}, HAL, FACTS) == [], "HAL: 簡体字の併記があれば通す")
check(any("繁体字" in p or "traditional" in p for p in pw.review({"text": "待ってむり 這次也太好了吧"}, HAL, FACTS)), "HAL: 繁体字は差し戻す")
check(any("AI" in p or "ai_disclosure" in p for p in pw.review({"text": "私はAIだけど 这个很好"}, HAL, FACTS)), "HAL: AI自認は差し戻す")
check(any("だワン" in p for p in pw.review({"text": "雪がつめたいよ"}, MORU, FACTS)), "もる: 語尾の「だワン」が無ければ差し戻す")
check(pw.review({"text": "雪ってつめたいんだワン🐾"}, MORU, FACTS) == [], "もる: 「だワン」があれば通す")


# 5) 実行: Firestore（偽）と AI（偽）
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


def idea_row(title, status, rank, date="20261121", topic="11月24日 満月（23時53分ごろ）", **kw):
    return {"accountId": "uranai", "date": date, "status": status, "rank": rank, "title": title, "format": "予想・占い",
            "angle": "a", "hook": "h", "goal": "g", "topic": topic, **kw}


IDEAS = {
    "i_old": idea_row("前の日の案", "選ばれた", 0, date="20261120"),
    "i_pick0": idea_row("選ばれた0", "選ばれた", 0),
    "i_pick1": idea_row("選ばれた1", "選ばれた", 1),
    "i_pick2": idea_row("選ばれた2", "選ばれた", 2),
    "i_exp": idea_row("実験", "実験枠", 3),
    "i_cand": idea_row("候補", "候補", 4),
    "i_ng": idea_row("却下", "却下", 5),
    "i_done": idea_row("書いた案", "選ばれた", 6, draft={"text": "x"}),
}
TOPICS = {"t1": {"title": "11月24日 満月（23時53分ごろ）", "category": "天体", "heat": 5, "safe": True, "person": "none", "expiresAt": "2026-11-24T14:59:00+00:00"}}


def fake_call(posts, tin=3000, tout=1500):
    calls = []

    def call(model, system, user):
        calls.append({"model": model, "system": system, "user": user})
        return {"text": json.dumps({"posts": posts}, ensure_ascii=False), "input_tokens": tin, "output_tokens": tout, "stop_reason": "end_turn"}

    call.calls = calls
    return call


def new_db(**extra):
    return FakeDb({"knowledge": {"plan_uranai": URANAI}, "ideas": json.loads(json.dumps(IDEAS, ensure_ascii=False)), "topics": dict(TOPICS), **extra})


db = new_db()
picked = hw.pick_ideas(db, "uranai")
check([i for i, _ in picked] == ["i_pick0", "i_pick1", "i_pick2", "i_exp"], "いちばん新しい日の、選ばれた・実験枠で、下書きの無い案だけ（最大4つ）")

good = "満月は23時53分ごろ。手放すと軽くなる傾向があります🌙 星座別の続きは公式LINEで"
call = fake_call([
    {"index": 0, "text": good, "reply": ""},
    {"index": 1, "text": "11月25日の満月は必ず叶います", "reply": ""},
    {"index": 2, "text": good, "reply": ""},
])
res, log = quiet(lambda: hw.run_account(db, "uranai", now=NOW, call=call))
check(res["ok"] and res["sent"] == 2 and res["held"] == 2, "審査を通った2つを承認待ちに、通らない1つと返事に無い1つを差し戻しに")
ap = db.data["approvals"]
check(set(ap) == {"post_i_pick0", "post_i_pick2"}, "承認待ちのIDは post_<案のID>（二重に作らない）")
a0 = ap["post_i_pick0"]
check(a0["status"] == "待ち" and a0["kind"] == "x_post" and a0["text"] == good and a0["body"].startswith(good), "承認待ちに、本文をそのまま入れる（秘書室で読める）")
check(a0["entityId"] == "corp" and a0["businessId"] == "uranai" and a0["department"] == "企画会議", "名義・事業はアカウントの元データから")
check(a0["plannedDate"] == "2026-11-24" and a0["expiresAt"].startswith("2026-11-24T23:59") and "11/24" in a0["title"], "投稿予定日と有効期限（その日の終わり）をつける")
d1 = db.data["ideas"]["i_pick1"]
check(d1["draftStatus"] == "差し戻し" and any("断定" in p for p in d1["draft"]["problems"]) and any("話題に無い日付" in p for p in d1["draft"]["problems"]), "差し戻しは、理由つきで案に残す")
check(db.data["ideas"]["i_exp"]["draftStatus"] == "差し戻し" and db.data["ideas"]["i_exp"]["draft"]["problems"] == ["AIの返事に、この案の下書きが無い"], "返事に無い案も、差し戻しとして残す")
check(db.data["ideas"]["i_pick0"]["approvalId"] == "post_i_pick0" and db.data["ideas"]["i_pick0"]["draftStatus"] == "承認待ち", "案に、承認待ちのIDを残す")
check("投稿予定日: 11月24日（火）" in call.calls[0]["user"] and "11月24日 満月（23時53分ごろ）" in call.calls[0]["user"], "AIに、投稿予定日と使ってよい事実を渡す")
check(abs(db.data["usage"]["plan-2026-11"]["costUsd"] - (3000 * 4 + 1500 * 20) / 1e6) < 1e-6, "費用を企画会議と同じ枠に足す")
check(good not in log and "満月" not in log, "ログに、下書き・案の中身を出さない（件数と費用だけ）")

# 6) 2回目は、書いた案を書き直さない。dry-run は呼ばない。枠に達したら呼ばない。元データが無ければ止まる
call2 = fake_call([])
res2, _ = quiet(lambda: hw.run_account(db, "uranai", now=NOW, call=call2))
check(res2["picked"] == 0 and not call2.calls, "下書き済みの案は、もう一度書かない（AIも呼ばない）")
c3 = fake_call([])
res3, _ = quiet(lambda: hw.run_account(new_db(), "uranai", dry_run=True, now=NOW, call=c3))
check(res3["picked"] == 4 and not c3.calls, "dry-run は、AIを呼ばず件数だけ")
c4 = fake_call([])
res4, _ = quiet(lambda: hw.run_account(new_db(usage={"plan-2026-11": {"costUsd": 30.0}}), "uranai", now=NOW, call=c4))
check(res4 == {"ok": False, "reason": "budget"} and not c4.calls, "月の枠に達していれば、AIを呼ばない")
res5, _ = quiet(lambda: hw.run_account(FakeDb({}), "nobody", now=NOW, call=fake_call([])))
check(res5 == {"ok": False, "reason": "no_plan"}, "元データが無ければ止まる")

# 7) コマンドとして起動できる
import subprocess  # noqa: E402

out = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "hq_write.py"), "--help"], capture_output=True, text=True, encoding="utf-8")
check(out.returncode == 0, "scripts/hq_write.py は、コマンドとして起動できる")
print("ALL OK")
