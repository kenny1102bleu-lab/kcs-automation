"""トピックデスクの「スカウト」: ニュース・トレンド・季節の話題を集めて、安全の印をつける（予算0円・AIを使わない）。

設計: kcs-hq/departments/clients/planning_meeting_design.md（2026-11-20）。
- 取り方: Yahoo!ニュース トピックスの RSS／Google トレンドの急上昇ワード RSS／季節・行事・天体の暦（コードで持つ）。
- ここは機械の規則だけ。**見出しの事実だけ**を扱い、記事の本文は取らない・写さない。
- 乗ってはいけない話題（災害・事件・訃報・病気・政治・裁判・未成年）に印をつける。人物が出る話題にも印をつける
  （アカウントによっては、人物の話題に乗らない。例: 成人向けのアカウント）。印は、あとで AI が確かめる前の一次の規則。
- 純粋関数だけ（通信・Firestore は呼び出し側）。公開リポジトリなので、ログに見出しは出さない（件数だけ）。
"""
import datetime as dt
import math
import hashlib
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

JST = dt.timezone(dt.timedelta(hours=9))
UTC = dt.timezone.utc

# 取り先。source は topics の出どころの名前（ログ・画面に出す）
# **Yahoo!ニュースの RSS は、「個人利用にのみ」と書かれている**（https://news.yahoo.co.jp/rss の注意事項・2026-11-20 確認）。
# 収益を目的にした運用（法人・アフィリエイト・案件）に、機械で取り込むのは、この条件に合わない恐れがある。
# そのため、**既定では使わない**。Yahoo に確認して許可が取れたとき、または個人の閲覧だけに使うときに、環境変数
# KCS_SCOUT_YAHOO=1 で有効にする。
YAHOO = "https://news.yahoo.co.jp/rss/topics/"
SOURCES = [  # (出どころ, URL, カテゴリ, 既定で使うか)
    ("yahoo_top", YAHOO + "top-picks.xml", "主要", False),
    ("yahoo_ent", YAHOO + "entertainment.xml", "エンタメ", False),
    ("yahoo_it", YAHOO + "it.xml", "IT", False),
    ("yahoo_domestic", YAHOO + "domestic.xml", "国内", False),
    ("yahoo_business", YAHOO + "business.xml", "経済", False),
    ("yahoo_sports", YAHOO + "sports.xml", "スポーツ", False),
    ("gtrends", "https://trends.google.com/trending/rss?geo=JP", "急上昇", True),
]


def active_sources(env_flag: str = "") -> list[tuple[str, str, str]]:
    """使う取り先。Yahoo は、許可が取れたときだけ（env_flag が "1"）。"""
    return [(s, u, c) for (s, u, c, default) in SOURCES if default or (env_flag == "1" and s.startswith("yahoo_"))]

NEWS_TTL_HOURS = 12
SAFETY_BLOCK = {
    "災害": ["地震", "津波", "噴火", "豪雨", "洪水", "土砂", "被災", "避難", "犠牲", "震度", "冠水", "停電"],
    "事故・事件": ["事故", "死亡", "殺害", "殺人", "逮捕", "容疑", "事件", "火災", "爆発", "刺され", "刺した", "遺体", "行方不明", "衝突", "転落", "墜落", "強盗", "詐欺", "窃盗", "暴行", "傷害"],
    "訃報": ["死去", "訃報", "逝去", "追悼", "お別れ"],
    # 「がん」だけだと「がんばった」に当たる（2026-11-21 にエマの案を誤って外した）。病名の形で持つ
    "病気": ["感染", "癌", "がん患者", "がん治療", "がん検診", "末期がん", "乳がん", "肺がん", "胃がん", "大腸がん", "小児がん", "病死", "入院", "手術", "容体", "闘病", "症状"],
    "政治・戦争": ["政治", "首相", "大統領", "選挙", "内閣", "与党", "野党", "国会", "政府", "議員", "知事", "市長", "総理", "外交", "会談", "ミサイル", "戦争", "侵攻", "軍", "自民", "立憲", "維新", "公明", "党首", "政権"],
    "裁判": ["裁判", "判決", "起訴", "公判", "被告", "控訴", "有罪", "無罪"],
    "未成年": ["中学生", "高校生", "児童", "少年", "少女", "未成年", "小学生", "園児", "児童生徒", "生徒"],
    "差別・宗教": ["差別", "ヘイト", "宗教", "教団"],
}
PERSON_WORDS = ["選手", "監督", "俳優", "女優", "歌手", "氏", "さん", "容疑者", "首相", "大統領", "社長", "市長", "知事", "議員", "アナ", "芸人", "タレント", "投手", "騎手", "力士", "棋士", "声優", "アイドル", "キャスター", "元", "夫妻", "妻", "夫"]


def _norm(title: str) -> str:
    t = re.sub(r"[\s　]+", "", str(title or ""))
    return re.sub(r"[「」『』（）()\[\]【】!！?？、。,.:：・\"'“”]", "", t)


def topic_id(title: str) -> str:
    """同じ話題は、出どころが違っても同じID（見出しをそろえて、ハッシュにする）。"""
    return "tp_" + hashlib.sha1(_norm(title).encode("utf-8")).hexdigest()[:16]


def _to_utc(s: str):
    try:
        d = parsedate_to_datetime(s)
        return (d if d.tzinfo else d.replace(tzinfo=UTC)).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def parse_rss(xml_text: str, source: str, category: str) -> list[dict]:
    """RSS から、見出し・リンク・時刻・（急上昇なら）おおよその検索数を取る。壊れた XML は、空で返す。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        if not title:
            continue
        pub = _to_utc(it.findtext("pubDate") or "")
        traffic = 0
        context = []  # 急上昇の理由になっている関連ニュースの見出し（企画の材料。最大2つ）
        for child in it:
            if child.tag.endswith("approx_traffic"):
                m = re.match(r"(\d[\d,]*)", (child.text or "").replace(" ", ""))
                traffic = int(m.group(1).replace(",", "")) if m else 0
            elif child.tag.endswith("news_item"):
                for sub in child:
                    if sub.tag.endswith("news_item_title") and (sub.text or "").strip() and len(context) < 2:
                        context.append(sub.text.strip())
        out.append({
            "title": title, "url": (it.findtext("link") or "").strip(), "publishedAt": pub.isoformat() if pub else "",
            "source": source, "category": category, "traffic": traffic, "context": context,
        })
    return out


def safety_of(title: str) -> dict:
    """一次の規則で、乗ってはいけない話題と、人物が出る話題に印をつける。
    safe=False: 乗らない（全アカウント共通）。person: 'likely'（人物が出そう）| 'unknown'（規則では分からない）| 'none'（暦など、人物が出ない）"""
    reasons = [k for k, words in SAFETY_BLOCK.items() if any(w in title for w in words)]
    person = "likely" if any(w in title for w in PERSON_WORDS) else "unknown"
    return {"safe": not reasons, "reasons": reasons, "person": person}


def build_topics(items: list[dict], now: dt.datetime) -> list[dict]:
    """同じ話題をまとめて、熱量（出どころの数＋検索数）・安全の印・有効期限をつける。熱量の高い順。"""
    groups: dict[str, dict] = {}
    for it in items:
        tid = topic_id(it["title"])
        g = groups.get(tid)
        if g is None:
            groups[tid] = g = {
                "id": tid, "title": it["title"], "url": it.get("url", ""), "category": it.get("category", ""),
                "sources": [], "traffic": 0, "publishedAt": it.get("publishedAt", ""), "context": [], **safety_of(it["title"]),
            }
        if it["source"] not in g["sources"]:
            g["sources"].append(it["source"])
        g["traffic"] = max(g["traffic"], it.get("traffic", 0))
        for line in it.get("context", []):
            if line not in g["context"] and len(g["context"]) < 2:
                g["context"].append(line)
                # 関連ニュースの見出しにある危ない語も、印に入れる（急上昇の語そのものは短く、理由が見出しにある）
                extra = safety_of(line)
                g["reasons"] = sorted(set(g["reasons"]) | set(extra["reasons"]))
                g["safe"] = not g["reasons"]
                if extra["person"] == "likely":
                    g["person"] = "likely"
        if it.get("publishedAt") and it["publishedAt"] > g["publishedAt"]:
            g["publishedAt"] = it["publishedAt"]
    out = []
    for g in groups.values():
        g["heat"] = len(g["sources"]) * 10 + min(g["traffic"], 5000) // 100
        g["collectedAt"] = now.isoformat()
        g["expiresAt"] = (now + dt.timedelta(hours=NEWS_TTL_HOURS)).isoformat()
        out.append(g)
    return sorted(out, key=lambda g: (-g["heat"], g["title"]))


# ── 季節・行事・天体（暦） ──
EVENTS_FIXED = [  # (月, 日, 名前, 種類) 年ごとに同じ。天体・二十四節気は「ごろ」（年で1日ずれることがある）
    (11, 15, "七五三", "行事"), (11, 22, "小雪（二十四節気）", "季節"), (11, 23, "勤労感謝の日", "行事"),
    (12, 7, "大雪（二十四節気）", "季節"), (12, 14, "ふたご座流星群（極大ごろ）", "天体"), (12, 22, "冬至（ゆず湯・かぼちゃ）", "季節"),
    (12, 24, "クリスマスイブ", "行事"), (12, 25, "クリスマス", "行事"), (12, 31, "大晦日", "行事"),
    (1, 1, "元日", "行事"), (1, 4, "しぶんぎ座流星群（極大ごろ）", "天体"), (1, 5, "小寒（二十四節気）", "季節"), (1, 20, "大寒（二十四節気）", "季節"),
    (2, 3, "節分", "行事"), (2, 4, "立春（二十四節気）", "季節"), (2, 14, "バレンタインデー", "行事"), (2, 19, "雨水（二十四節気）", "季節"),
    (3, 3, "ひな祭り", "行事"), (3, 6, "啓蟄（二十四節気）", "季節"), (3, 14, "ホワイトデー", "行事"), (3, 21, "春分（二十四節気）", "季節"),
    (4, 5, "清明（二十四節気）", "季節"), (4, 20, "穀雨（二十四節気）", "季節"), (5, 5, "こどもの日・立夏", "行事"), (5, 21, "小満（二十四節気）", "季節"),
    (6, 6, "芒種（二十四節気）", "季節"), (6, 21, "夏至（二十四節気）", "季節"), (7, 7, "七夕・小暑", "行事"), (7, 23, "大暑（二十四節気）", "季節"),
    (8, 7, "立秋（二十四節気）", "季節"), (8, 13, "ペルセウス座流星群（極大ごろ）", "天体"), (8, 23, "処暑（二十四節気）", "季節"),
    (9, 8, "白露（二十四節気）", "季節"), (9, 23, "秋分（二十四節気）", "季節"), (10, 8, "寒露（二十四節気）", "季節"), (10, 21, "オリオン座流星群（極大ごろ）", "天体"),
    (10, 23, "霜降（二十四節気）", "季節"), (10, 31, "ハロウィン", "行事"), (11, 7, "立冬（二十四節気）", "季節"), (11, 18, "しし座流星群（極大ごろ）", "天体"),
]

SYNODIC = 29.530588853  # 朔望月（日）
NEW_MOON_EPOCH = dt.datetime(2000, 1, 6, 18, 14, tzinfo=UTC)  # 基準の新月


def moon_age(t: dt.datetime) -> float:
    return ((t - NEW_MOON_EPOCH).total_seconds() / 86400) % SYNODIC


# 新月・満月の瞬間（Meeus『Astronomical Algorithms』49章の主な項。誤差は数分）。
# 平均の周期だけだと半日ほどずれて、日付が1日違うことがある（2026-11 の満月を 25日と出していた。正しくは 24日 23:53）。
_NEW_TERMS = [(-0.40720, 0, 0, 1, 0), (0.17241, 1, 1, 0, 0), (0.01608, 0, 0, 2, 0), (0.01039, 0, 0, 0, 2), (0.00739, 1, -1, 1, 0),
              (-0.00514, 1, 1, 1, 0), (0.00208, 2, 2, 0, 0), (-0.00111, 0, 0, 1, -2), (-0.00057, 0, 0, 1, 2), (0.00056, 1, 1, 2, 0),
              (-0.00042, 0, 0, 3, 0), (0.00042, 1, 1, 0, 2), (0.00038, 1, 1, 0, -2), (-0.00024, 1, -1, 2, 0)]
_FULL_TERMS = [(-0.40614, 0, 0, 1, 0), (0.17302, 1, 1, 0, 0), (0.01614, 0, 0, 2, 0), (0.01043, 0, 0, 0, 2), (0.00734, 1, -1, 1, 0),
               (-0.00515, 1, 1, 1, 0), (0.00209, 2, 2, 0, 0), (-0.00111, 0, 0, 1, -2), (-0.00057, 0, 0, 1, 2), (0.00056, 1, 1, 2, 0),
               (-0.00042, 0, 0, 3, 0), (0.00042, 1, 1, 0, 2), (0.00038, 1, 1, 0, -2), (-0.00024, 1, -1, 2, 0)]
_JD_UNIX = 2440587.5  # 1970-01-01 0時（UTC）のユリウス日


def moon_phase_time(k: float) -> dt.datetime:
    """k が整数なら新月、.5 なら満月の瞬間（UTC）。k=0 は 2000-01-06 の新月。"""
    T = k / 1236.85
    jde = 2451550.09766 + 29.530588861 * k + 0.00015437 * T ** 2 - 0.000000150 * T ** 3 + 0.00000000073 * T ** 4
    E = 1 - 0.002516 * T - 0.0000074 * T ** 2
    M = math.radians(2.5534 + 29.10535670 * k - 0.0000014 * T ** 2)
    Mp = math.radians(201.5643 + 385.81693528 * k + 0.0107582 * T ** 2 + 0.00001238 * T ** 3)
    F = math.radians(160.7108 + 390.67050284 * k - 0.0016118 * T ** 2 - 0.00000227 * T ** 3)
    Om = math.radians(124.7746 - 1.56375588 * k + 0.0020672 * T ** 2)
    terms = _FULL_TERMS if abs(k % 1 - 0.5) < 1e-9 else _NEW_TERMS
    # (係数, Eの乗数, M, M', F)。E の乗数は M の係数の絶対値と同じ
    corr = sum(c * E ** e * math.sin(m * M + mp * Mp + f * F) for c, e, m, mp, f in terms) - 0.00017 * math.sin(Om)
    jd_ut = jde + corr - 69 / 86400  # 力学時 → 世界時（ΔT 約69秒）
    return dt.datetime.fromtimestamp((jd_ut - _JD_UNIX) * 86400, tz=UTC)


def moon_events(start: dt.date, days: int) -> list[tuple[dt.date, str]]:
    """start から days 日のうちの、新月・満月（日本時間の日付と、だいたいの時刻）。"""
    end = start + dt.timedelta(days=days)
    k0 = math.floor((start.year + (start.timetuple().tm_yday - 1) / 365.25 - 2000) * 12.3685) - 2
    out = []
    for i in range(0, 2 * (days // 29 + 4)):
        k = k0 + i / 2
        t = moon_phase_time(k).astimezone(JST)
        if start <= t.date() < end:
            name = "満月" if i % 2 else "新月"
            out.append((t.date(), f"{name}（{t.hour}時{t.minute:02d}分ごろ）"))
    return sorted(out)


def calendar_topics(now: dt.datetime, days: int = 14) -> list[dict]:
    """これから days 日以内の、季節・行事・天体・月の満ち欠けを、話題として返す。人物が出ない（person: none）。"""
    today = now.astimezone(JST).date()
    out = []
    for d in range(days + 1):
        day = today + dt.timedelta(days=d)
        names = [(n, k) for (m, dd, n, k) in EVENTS_FIXED if m == day.month and dd == day.day]
        # 第4金曜日のブラックフライデー・成人の日（1月の第2月曜）は、日付が年で変わる
        if day.month == 11 and day.weekday() == 4 and 22 <= day.day <= 28:
            names.append(("ブラックフライデー", "行事"))
        if day.month == 1 and day.weekday() == 0 and 8 <= day.day <= 14:
            names.append(("成人の日", "行事"))
        names += [(n, "天体") for (md, n) in moon_events(day, 1)]
        for name, kind in names:
            label = f"{day.month}月{day.day}日 {name}"
            out.append({
                "id": topic_id(f"{day.isoformat()}{name}"), "title": label, "url": "", "category": kind,
                "sources": ["calendar"], "traffic": 0, "publishedAt": "", "safe": True, "reasons": [], "person": "none",
                "heat": 5, "collectedAt": now.isoformat(),
                "expiresAt": dt.datetime(day.year, day.month, day.day, 23, 59, tzinfo=JST).astimezone(UTC).isoformat(),
            })
    return out
