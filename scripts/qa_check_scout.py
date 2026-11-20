"""スカウト（トピックデスクの収集）の回帰テスト。通信・Firestore には触らない（偽物で確かめる）。

    python scripts/qa_check_scout.py
"""
import contextlib
import datetime as dt
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scripts import hq_scout as hs  # noqa: E402
from scripts.common import topic_scout as ts  # noqa: E402

NOW = dt.datetime(2026, 11, 20, 9, 0, tzinfo=dt.timezone.utc)


def check(cond, msg):
    if not cond:
        print(f"NG: {msg}")
        sys.exit(1)
    print(f"OK: {msg}")


YAHOO = """<rss version="2.0"><channel><title>Yahoo</title>
<item><title>冬の嵐で3連休が荒れ模様</title><link>https://example.test/a?source=rss</link><pubDate>Fri, 20 Nov 2026 08:07:19 GMT</pubDate></item>
<item><title>首相が会談 重要鉱物で一致</title><link>https://example.test/b</link><pubDate>Fri, 20 Nov 2026 07:25:56 GMT</pubDate></item>
<item><title>

</title></item></channel></rss>"""
GTRENDS = """<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>冬の嵐で3連休が荒れ模様</title><ht:approx_traffic>2,000+</ht:approx_traffic><pubDate>Fri, 20 Nov 2026 00:50:00 -0800</pubDate></item>
<item><title>スーパームーン</title><ht:approx_traffic>500+</ht:approx_traffic><pubDate>Fri, 20 Nov 2026 00:10:00 -0800</pubDate></item>
<item><title>ある名前</title><ht:approx_traffic>200+</ht:approx_traffic><pubDate>Fri, 20 Nov 2026 00:20:00 -0800</pubDate><ht:news_item><ht:news_item_title>首相、ある名前氏と会談（時事）</ht:news_item_title></ht:news_item><ht:news_item><ht:news_item_title>二つ目の見出し</ht:news_item_title></ht:news_item><ht:news_item><ht:news_item_title>三つ目は捨てる</ht:news_item_title></ht:news_item></item>
</channel></rss>"""

# 1) RSS の読み取り
y = ts.parse_rss(YAHOO, "yahoo_top", "主要")
check(len(y) == 2 and y[0]["publishedAt"].startswith("2026-11-20T08:07:19"), "Yahoo の RSS から、見出し・時刻を読む。空の見出しは捨てる")
g = ts.parse_rss(GTRENDS, "gtrends", "急上昇")
check(g[2]["context"] == ["首相、ある名前氏と会談（時事）", "二つ目の見出し"], "急上昇の関連ニュースの見出しを、理由として最大2つ読む")
check(g[0]["traffic"] == 2000 and g[0]["publishedAt"].startswith("2026-11-20T08:50:00"), "Google トレンドの検索数（2,000+）と時刻（UTCに直す）を読む")
check(ts.parse_rss("<rss><壊れ", "x", "x") == [], "壊れた XML は、空で返す（止まらない）")

# 2) 安全の印・人物の印
check(ts.safety_of("首相が会談 重要鉱物で一致") == {"safe": False, "reasons": ["政治・戦争"], "person": "likely"}, "政治の話題は「乗らない」＋人物の印")
check(ts.safety_of("高校生が逮捕 事件の容疑")["reasons"] == ["事故・事件", "未成年"], "事件・未成年は、複数の理由で外す")
check(ts.safety_of("冬の嵐で3連休が荒れ模様")["safe"] is True, "天気の話題は、乗ってよい")
check(ts.safety_of("田中投手が更改")["person"] == "likely", "「投手」など、人物の語がある話題には、人物の印")

ctx_topic = next(t for t in ts.build_topics(g, NOW) if t["title"] == "ある名前")
check(ctx_topic["safe"] is False and ctx_topic["person"] == "likely", "関連ニュースの見出しが政治・人物なら、短い語の話題にも印をつける")

# 3) まとめ: 同じ話題は、出どころが違っても1件。熱量は、出どころの数＋検索数
topics = ts.build_topics(y + g, NOW)
storm = next(t for t in topics if t["title"].startswith("冬の嵐"))
check(sorted(storm["sources"]) == ["gtrends", "yahoo_top"], "同じ話題を、Yahoo と Google トレンドで1件にまとめる")
check(storm["heat"] == 2 * 10 + 2000 // 100 and topics[0]["id"] == storm["id"], "熱量が高い順（出どころ2つ＋検索数）")
check(ts.topic_id("冬の嵐で 3連休が荒れ模様！") == ts.topic_id("冬の嵐で3連休が荒れ模様"), "スペースや記号の違いは、同じ話題とみなす")
check(storm["expiresAt"] == (NOW + dt.timedelta(hours=ts.NEWS_TTL_HOURS)).isoformat(), "ニュースの有効期限は12時間")

# 4) 暦: 季節・行事・天体。人物が出ない（person: none）
cal = ts.calendar_topics(NOW, days=14)
titles = [c["title"] for c in cal]
check("11月22日 小雪（二十四節気）" in titles and "11月23日 勤労感謝の日" in titles, "二十四節気と行事が、14日先まで出る")
check("11月27日 ブラックフライデー" in titles, "第4金曜日のブラックフライデー（年で日付が変わる）を出す")
check(any("満月" in t for t in titles), "満月・新月（目安）が出る")
check(all(c["person"] == "none" and c["safe"] and c["sources"] == ["calendar"] for c in cal), "暦の話題は、人物が出ない・安全")
check(ts.calendar_topics(dt.datetime(2027, 1, 7, 0, 0, tzinfo=dt.timezone.utc), 7)[-1]["title"].endswith("成人の日") or any("成人の日" in c["title"] for c in ts.calendar_topics(dt.datetime(2027, 1, 7, 0, 0, tzinfo=dt.timezone.utc), 7)), "成人の日（1月の第2月曜）")
age = ts.moon_age(dt.datetime(2000, 1, 6, 18, 14, tzinfo=dt.timezone.utc))
check(abs(age) < 1e-6 or abs(age - ts.SYNODIC) < 1e-6, "月齢の基準日（2000-01-06 の新月）は月齢0")


# 5) 収集: 1つ取れなくても止めない。全部取れなければ失敗
def fake_get(url, timeout=20):
    if "business" in url:
        raise RuntimeError("通信エラー https://secret.example/?key=SECRET")
    return GTRENDS if "trends.google" in url else YAHOO


check([s for s, _, _ in ts.active_sources("")] == ["gtrends"], "既定では、Yahoo の RSS を使わない（「個人利用にのみ」の条件があるため）")
check(len(ts.active_sources("1")) == 7 and ts.active_sources("0") == ts.active_sources(""), "KCS_SCOUT_YAHOO=1 のときだけ Yahoo を使う")
topics, result = hs.collect(NOW, get=fake_get)
check(list(result) == ["gtrends"] and result["gtrends"] == 3, "既定では、Google トレンドだけを取る")
os.environ["KCS_SCOUT_YAHOO"] = "1"
topics, result = hs.collect(NOW, get=fake_get)
check(result["yahoo_business"] == -1 and result["yahoo_top"] == 2 and result["gtrends"] == 3, "（Yahoo を有効にしたとき）取り先が1つ失敗しても、ほかを使う（-1で記録）")


class Snap:
    def __init__(self, d):
        self._d = d
        self.exists = d is not None

    def to_dict(self):
        return dict(self._d) if self._d is not None else None


class Ref:
    def __init__(self, db, id_):
        self.db, self.id = db, id_

    def get(self):
        return Snap(self.db.data.get(self.id))

    def set(self, data, merge=False):
        self.db.data[self.id] = {**(self.db.data.get(self.id) or {}), **data} if merge else dict(data)

    def delete(self):
        self.db.data.pop(self.id, None)


class Query:
    def __init__(self, db, cutoff):
        self.db, self.cutoff = db, cutoff

    def limit(self, n):
        return self

    def stream(self):
        class D:
            def __init__(self, i):
                self.id = i

        return [D(i) for i, d in list(self.db.data.items()) if d.get("expiresAt", "") < self.cutoff]


class Col:
    def __init__(self, db):
        self.db = db

    def document(self, i):
        return Ref(self.db, i)

    def where(self, field, op, value):
        return Query(self.db, value)


class FakeDb:
    def __init__(self, data=None):
        self.data = data or {}

    def collection(self, name):
        assert name == "topics"
        return Col(self)


def quiet(fn):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn()
    return r, buf.getvalue()


db = FakeDb({"tp_old": {"expiresAt": "2026-11-10T00:00:00+00:00"}})
res, log = quiet(lambda: hs.run(db, now=NOW, get=fake_get))
check(res["ok"] and res["new"] > 0 and res["removed"] == 1 and "tp_old" not in db.data, "話題を書き、期限を3日過ぎたものを消す")
check(all(d.get("entityId") == "corp" and d.get("businessId") == "company_ops" for d in db.data.values()), "名義と事業を持つ（絶対ルール1）")
check("冬の嵐" not in log and "首相" not in log and "example.test" not in log and "SECRET" not in log, "ログに、見出し・URL・通信の中身を出さない（件数だけ）")

# 同じ話題を、もう一度集めると、新しい扱いにならず、最初に見た時刻と出どころを残す
first = db.data[storm["id"]]["firstSeenAt"]
later = NOW + dt.timedelta(hours=1)
res2, _ = quiet(lambda: hs.run(db, now=later, get=fake_get))
check(res2["new"] == 0 and db.data[storm["id"]]["firstSeenAt"] == first, "2回目は新しい話題が0件。最初に見た時刻を残す")

# dry-run は書かない。全部取れなければ失敗
db2 = FakeDb()
res3, _ = quiet(lambda: hs.run(db2, dry_run=True, now=NOW, get=fake_get))
check(res3["ok"] and db2.data == {}, "dry-run は、何も書かない")


def all_fail(url, timeout=20):
    raise RuntimeError("x")


res4, log4 = quiet(lambda: hs.run(FakeDb(), now=NOW, get=all_fail))
check(res4["ok"] is False and "どの取り先も取れませんでした" in log4, "どの取り先も取れなければ、失敗にする")
del os.environ["KCS_SCOUT_YAHOO"]

# コマンドとして起動できる
import subprocess  # noqa: E402

r = subprocess.run([sys.executable, "scripts/hq_scout.py", "--help"], cwd=os.path.join(os.path.dirname(__file__), ".."),
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
check(r.returncode == 0 and "usage" in (r.stdout or "").lower(), "scripts/hq_scout.py は、コマンドとして起動できる")
print("ALL OK")
