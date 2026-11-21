"""ライターと審査（企画会議の次の段階）: 選ばれた案を、そのキャラの完成した1投稿にし、語の規則で審査する。

設計: kcs-hq/departments/clients/planning_meeting_design.md（作る順番の4）。
- AI は文章を書くだけ。審査は AI の自己採点でなく、コードの規則で見る（費用0円・毎回同じ結果）。
- 事実は、話題の題にある日付・時刻だけ。それ以外の日付・時刻が本文に出たら差し戻す（情報接地）。
- 投稿はしない。審査を通ったものを、承認待ち（approvals）に出すのは hq_write.py。
"""
import datetime as dt
import re

from . import ng_patterns, planner
from . import topic_scout as ts

WEEKDAYS = "月火水木金土日"
X_MAX_WEIGHT = 280  # X の上限（全角は2として数える。日本語なら140字）
PR_WORDS = ("PR", "ＰＲ", "広告")
# 紹介（アフィリエイト）でリンクを置くアカウント。リンクを置く投稿は【PR】が要る
PR_BUSINESSES = {"adult", "sunakun"}
# 断定（占い・もる）の語
ASSERT_WORDS = ("必ず", "絶対", "100%", "１００％", "確実に")
# 簡体字にしかない、よく使う字（HAL の中国語の併記を確かめる。日本語と同じ形の字は入れない）
SIMPLIFIED_HINT = "们这个么说对时还过吗呢吧哦"

SYSTEM = (
    "あなたはKCS合同会社のSNSライターです。企画会議で選ばれた案を、そのアカウントの人物として、Xにそのまま投稿できる完成した文章にします。\n"
    "- 1つの案につき、完成した投稿を1つだけ書く。言い換え案・案1/案2・解説・見出し・「ポイント」は書かない。\n"
    "- そのキャラの口調（一人称・語尾・口癖・絵文字の数・長さ）で書く。説明書きのような文や、AIっぽい定型文（いかがでしたか・まとめると・以下の通り）は書かない。\n"
    "- 事実として使ってよいのは、案ごとの【話題】と【使ってよい事実】の題にある日付・時刻・行事だけ。ほかの日付・時刻・数字・人名・商品名・作品名を作らない。\n"
    "- 「今日」「今夜」「明日」は、案ごとの【投稿予定日】を基準に書く。\n"
    "- 本文に URL を書かない。リンクを置く案は、本文で「リプ欄に置いておくね」のように誘導し、reply にリプ欄の一言を書く（URLは書かない。あとで人が入れる）。リンクを置かない案は reply を空にする。\n"
    "- 商品やサービスの紹介でリンクを置く案は、本文に【PR】を入れる。\n"
    "- 長さは、日本語で140字以内（Xの上限）。キャラの「長さ」の目安を優先する。\n"
    "- アカウントの決まり（rules）を必ず守る。\n"
    '出力はJSONだけ（前後に文章を付けない）: {"posts": [{"index": 0, "text": "投稿の本文", "reply": "リプ欄の一言（無ければ空）"}]}'
)


def x_weight(text: str) -> int:
    """X の数え方（だいたい）: 半角の英数字・記号・ラテン文字は1、日本語・絵文字などは2。"""
    n = 0
    for c in text or "":
        o = ord(c)
        n += 1 if (o <= 0x10FF or 0x2000 <= o <= 0x200D or 0x2010 <= o <= 0x201F or 0x2032 <= o <= 0x2037) else 2
    return n


def topic_date(title: str, today: dt.date) -> dt.date | None:
    """話題の題の「11月24日」を日付にする（年は、今日に近いほう）。無ければ None。"""
    m = re.search(r"(\d{1,2})月(\d{1,2})日", title or "")
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    best = None
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            d = dt.date(year, month, day)
        except ValueError:
            continue
        if best is None or abs((d - today).days) < abs((best - today).days):
            best = d
    return best


def planned_date(topic_title: str, today: dt.date) -> dt.date:
    """投稿予定日: 話題の日が今日から7日以内ならその日。過ぎていたり、話題に日付が無ければ今日。"""
    d = topic_date(topic_title, today)
    return d if d is not None and 0 <= (d - today).days <= 7 else today


def date_label(d: dt.date) -> str:
    return f"{d.month}月{d.day}日（{WEEKDAYS[d.weekday()]}）"


def build_prompt(plan: dict, items: list[dict], facts: list[str] = ()) -> tuple[str, str]:
    """(system, user)。items は {index, idea, plannedDate(date)}。facts はいま有効な話題の題（複数の行事をまとめる案のため）。
    キャラクターシートは企画会議と同じものを渡す。"""
    blocks = []
    for it in items:
        idea = it["idea"]
        blocks.append("\n".join([
            f"[{it['index']}] 題: {idea.get('title', '')}",
            f"形式: {idea.get('format', '')}",
            f"掛け合わせと切り口: {idea.get('angle', '')}",
            f"最初の一文のねらい: {idea.get('hook', '')}",
            f"ゴールへのつなぎ: {idea.get('goal', '')}",
            f"話題（事実はこの題の範囲だけ）: {idea.get('topic') or '（なし。季節や気分の話として書く）'}",
            f"投稿予定日: {date_label(it['plannedDate'])}",
        ]))
    user = "\n".join([
        f"【アカウント】{plan.get('name', '')}",
        f"【ゴール】{plan.get('goal', '')}",
        f"【集客の流れ】{plan.get('funnel', '')}",
        f"【世界観と口調】\n{plan.get('persona', '')}",
        *planner._sheet(plan),
        "【決まり（rules）】\n" + "\n".join(f"- {r}" for r in plan.get("rules", [])),
        "【使ってよい事実（話題の題）】\n" + ("\n".join(f"- {f}" for f in facts) if facts else "（なし）"),
        "【書く案】\n" + "\n\n".join(blocks),
    ])
    return SYSTEM, user


def parse_posts(text: str) -> dict[int, dict]:
    """AIの返事から、index → {text, reply}。途中で切れていたら、閉じているものだけ。"""
    out = {}
    for it in planner._raw_ideas(text, key="posts"):
        if not isinstance(it, dict):
            continue
        try:
            i = int(it.get("index"))
        except (TypeError, ValueError):
            continue
        body = str(it.get("text") or "").strip()
        if body:
            out[i] = {"text": body, "reply": str(it.get("reply") or "").strip()}
    return out


def review(post: dict, plan: dict, facts_text: str = "") -> list[str]:
    """決まりに触れるところを、理由の一覧で返す（空なら通過）。facts_text は、使ってよい事実（話題の題）をつないだもの。"""
    text, reply = post.get("text", ""), post.get("reply", "")
    full = f"{text}\n{reply}"
    problems = []
    if not text:
        return ["本文が空"]
    hit = ng_patterns.scan(full)
    if hit:
        problems.append(f"NGの表現（{hit[0]}: {hit[1]}）")
    for reason, words in ts.SAFETY_BLOCK.items():
        w = next((w for w in words if w in full), "")
        if w:
            problems.append(f"乗らない話題（{reason}: {w}）")
    if (plan.get("topicPolicy") or {}).get("requireNoPerson"):
        # 「さん」「氏」は、自分の文章では「みなさん」「氏名」にも当たるので、下書きの審査では使わない
        w = next((w for w in ts.PERSON_WORDS if w not in ("さん", "氏") and w in full), "")
        if w:
            problems.append(f"人物の語（{w}）")
    if plan.get("businessId") == "adult":
        w = next((w for w in planner.ALWAYS_BLOCK_WORDS if w in full), "")
        if w:
            problems.append(f"決まりに触れる語（{w}）")
    for w in plan.get("avoidWords", []):
        if w and w in full:
            problems.append(f"キャラが避ける語（{w}）")
    if x_weight(text) > X_MAX_WEIGHT:
        problems.append(f"長すぎる（Xの数え方で {x_weight(text)} / {X_MAX_WEIGHT}）")
    if reply and x_weight(reply) > X_MAX_WEIGHT:
        problems.append("リプ欄の一言が長すぎる")
    # 情報接地: 本文の日付・時刻は、使ってよい事実（話題の題）にあるものだけ
    for m in re.findall(r"\d{1,2}月\d{1,2}日|\d{1,2}時\d{1,2}分", full):
        if m not in (facts_text or ""):
            problems.append(f"話題に無い日付・時刻（{m}）")
    if reply and plan.get("businessId") in PR_BUSINESSES and not any(w in text for w in PR_WORDS):
        problems.append("リンクを置く紹介なのに【PR】が無い")
    if plan.get("businessId") in ("uranai", "moru"):
        w = next((w for w in ASSERT_WORDS if w in full), "")
        if w:
            problems.append(f"断定の語（{w}）")
    if plan.get("language") == "ja+zh-Hans" and not any(c in text for c in SIMPLIFIED_HINT):
        problems.append("中国語（簡体字）の併記が無い")
    endings = [e.strip("〜~ ") for e in (plan.get("voice") or {}).get("endings", []) if e.strip("〜~ ")]
    if plan.get("businessId") == "moru" and endings and not any(e in text for e in endings):
        problems.append("もるの語尾（だワン）が無い")
    return problems
