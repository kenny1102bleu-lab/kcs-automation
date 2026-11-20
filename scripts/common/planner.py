"""企画会議のプランナー（純粋関数だけ）: 「ネタ×世界観×形式×目的」の掛け合わせで案を出させ、点数と規則で選ぶ。

設計: kcs-hq/departments/clients/planning_meeting_design.md（2026-11-20）。
- アカウントの世界観・決まりは、Firestore の knowledge/plan_<id>（非公開）から渡される。**このファイルに、個別のアカウントの中身は書かない**（公開リポジトリ）。
- AIは「案」（企画の骨）を出す。投稿の完成した文章は、あとの段階（ライター）で作る。
- 選ぶのはコード（点数・同じ形式を続けない・実験枠）。AIの自己採点は、そのまま信じず、規則で外す・減点する。
"""
import json
import re

from scripts.common import topic_scout as ts

N_IDEAS = 20
N_PICK = 3
RECENT_DAYS = 14
WEIGHTS = {"novelty": 0.25, "fit": 0.25, "funnel": 0.25, "safety": 0.15, "ease": 0.10}
REPEAT_PENALTY = 0.3  # 直近の同じ形式、1回につき
MIN_SAFETY = 3
TOPIC_LIMIT = 12


def eligible_topics(topics: list[dict], policy: dict, now_iso: str) -> list[dict]:
    """そのアカウントが乗ってよい話題。期限切れ・乗らない印・（人物の印）・カテゴリの制限を外し、熱量の高い順。"""
    out = []
    for t in topics:
        if t.get("expiresAt") and t["expiresAt"] <= now_iso:
            continue
        if t.get("safe") is False:
            continue
        person = t.get("person", "unknown")
        if policy.get("blockPersonLikely") and person == "likely":
            continue
        if policy.get("requireNoPerson") and person != "none":
            continue
        allow = policy.get("allowCategories")
        if allow and t.get("category") not in allow:
            continue
        out.append(t)
    return sorted(out, key=lambda t: (-int(t.get("heat") or 0), str(t.get("title"))))[:TOPIC_LIMIT]


SYSTEM = (
    "あなたはKCS合同会社の企画会議のプランナーです。SNS（X）のアカウントごとに、毎日、新しい投稿の企画を考えます。\n"
    "考え方: **ネタ（ニュース・季節・流行）× 世界観（そのアカウントらしさ）× 形式 × 目的** を掛け合わせて、"
    "「そのアカウントにしか出せない見方」の企画を作る。ニュースに乗るだけ・誰でも書ける投稿・毎日同じ型は不可。\n"
    "- 案は {n} 個。形式を6種類以上に散らし、意外な組み合わせを必ず入れる。\n"
    "- 各案に「なぜ伸びると思うか（仮説）」を1行つける。\n"
    "- 1つのネタから、Xの投稿・ショート動画の台本・素材（在庫）に展開できる案を歓迎する。\n"
    "- 与えられた話題と、アカウントの決まり（rules）を必ず守る。決まりに触れる案は出さない。話題に無い事実・数字・人名を作らない。\n"
    "- 直近に出した案（recent）と似た案は出さない。\n"
    '出力はJSONだけ（前後に文章を付けない）: {{"ideas": [{{"title": "案の題（20字以内）", "topicId": "話題のID（使わなければ空）", '
    '"format": "形式（formatsの中から1つ）", "angle": "掛け合わせと切り口（60字以内）", "hook": "最初の一文のねらい（40字以内）", '
    '"goal": "ゴールにどうつなぐか（40字以内）", "hypothesis": "なぜ伸びると思うか（60字以内）", '
    '"scores": {{"novelty": 1-5, "fit": 1-5, "funnel": 1-5, "safety": 1-5, "ease": 1-5}}}}]}}。'
    "scores は、新しさ・世界観との合い・ゴールへのつながり・安全（決まりに触れないか）・作りやすさ。厳しめにつける。"
)


def build_prompt(plan: dict, topics: list[dict], recent: list[dict], n: int = N_IDEAS) -> tuple[str, str]:
    """(system, user)。話題は、題・カテゴリ・理由（関連ニュースの見出し）だけを渡す（URL・本文は渡さない）。"""
    system = SYSTEM.format(n=n)
    lines = []
    for t in topics:
        ctx = " / ".join(t.get("context") or [])
        lines.append(f"- [{t['id']}] {t.get('title', '')}（{t.get('category', '')}）" + (f" 理由: {ctx}" if ctx else ""))
    recent_lines = [f"- {r.get('title', '')}（{r.get('format', '')}）" for r in recent[:30]]
    user = "\n".join([
        f"【アカウント】{plan.get('name', '')}",
        f"【ゴール】{plan.get('goal', '')}",
        f"【集客の流れ】{plan.get('funnel', '')}",
        f"【世界観と口調】\n{plan.get('persona', '')}",
        "【決まり（rules）】\n" + "\n".join(f"- {r}" for r in plan.get("rules", [])),
        "【形式（formats）】" + "、".join(plan.get("formats", [])),
        "【使える話題】\n" + ("\n".join(lines) if lines else "（いま使える話題はありません。季節・暮らし・気分の自由な企画にしてください）"),
        "【直近に出した案（recent）】\n" + ("\n".join(recent_lines) if recent_lines else "（なし）"),
    ])
    return system, user


def parse_ideas(text: str, plan: dict, topic_ids: set[str]) -> list[dict]:
    """AIの返事から案を読む。形式が決まりの外・点が足りない・題が空の案は捨てる。JSONでなければ空。"""
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    formats = set(plan.get("formats", []))
    out = []
    for it in (data.get("ideas") if isinstance(data, dict) else None) or []:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()[:40]
        fmt = str(it.get("format") or "").strip()
        sc = it.get("scores") if isinstance(it.get("scores"), dict) else {}
        try:
            scores = {k: max(1, min(5, int(sc[k]))) for k in WEIGHTS}
        except (KeyError, TypeError, ValueError):
            continue
        if not title or (formats and fmt not in formats):
            continue
        tid = str(it.get("topicId") or "").strip()
        out.append({
            "title": title, "format": fmt, "topicId": tid if tid in topic_ids else "",
            "angle": str(it.get("angle") or "").strip()[:140], "hook": str(it.get("hook") or "").strip()[:100],
            "goal": str(it.get("goal") or "").strip()[:100], "hypothesis": str(it.get("hypothesis") or "").strip()[:140],
            "scores": scores,
        })
    return out[: N_IDEAS + 5]


# 成人向けアカウント用の追加の語（案の文章に入っていたら外す）。アカウントごとの決まりは plan の中が正。ここは、最後の網
ALWAYS_BLOCK_WORDS = ["会える", "出会える", "DMで", "制服", "学生", "少女", "年下", "未成年", "JK", "JC", "ロリ"]


def guard(idea: dict, plan: dict) -> str:
    """決まりに触れる案の理由を返す（問題なければ空）。AIの自己採点でなく、語の規則で見る。"""
    text = " ".join(str(idea.get(k, "")) for k in ("title", "angle", "hook", "goal"))
    for reason, words in ts.SAFETY_BLOCK.items():
        if any(w in text for w in words):
            return f"乗らない話題（{reason}）"
    policy = plan.get("topicPolicy") or {}
    if policy.get("requireNoPerson") and any(w in text for w in ts.PERSON_WORDS):
        return "人物の話題（このアカウントは乗らない）"
    if plan.get("entityId") == "personal" and plan.get("businessId") == "adult" and any(w in text for w in ALWAYS_BLOCK_WORDS):
        return "決まりに触れる語"
    if idea["scores"]["safety"] < MIN_SAFETY:
        return "安全の点が低い"
    return ""


def total_score(idea: dict, recent_formats: list[str]) -> float:
    base = sum(idea["scores"][k] * w for k, w in WEIGHTS.items())
    repeats = sum(1 for f in recent_formats if f == idea["format"])
    return round(base - min(repeats * REPEAT_PENALTY, 1.0), 3)


def select(ideas: list[dict], plan: dict, recent_formats: list[str], k: int = N_PICK) -> list[dict]:
    """規則で外す → 点数（同じ形式の続けすぎは減点）→ 形式が重ならない上位 k 案 ＋ 実験枠（新しさが高い案）。
    すべての案に status（選ばれた／実験枠／候補／却下）と total をつけて返す（捨てずに記録するため）。"""
    out = []
    for idea in ideas:
        why = guard(idea, plan)
        out.append({**idea, "total": total_score(idea, recent_formats), "status": "却下" if why else "候補", "rejectedFor": why})
    pool = sorted((i for i in out if i["status"] == "候補"), key=lambda i: -i["total"])
    used_formats = set()
    picked = 0
    for i in pool:
        if picked >= k:
            break
        if i["format"] in used_formats:
            continue
        i["status"] = "選ばれた"
        used_formats.add(i["format"])
        picked += 1
    # 実験枠: 選ばれなかった中で、新しさが4以上・世界観の合いが3以上の案のうち、新しさが最も高いもの
    bold = [i for i in pool if i["status"] == "候補" and i["scores"]["novelty"] >= 4 and i["scores"]["fit"] >= 3]
    if bold:
        max(bold, key=lambda i: (i["scores"]["novelty"], i["total"]))["status"] = "実験枠"
    return sorted(out, key=lambda i: (("選ばれた", "実験枠", "候補", "却下").index(i["status"]), -i["total"]))
