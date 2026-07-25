"""
WF-13: スタッフ経営会議（毎週月曜 10:00 JST、リョウの週次提案の後）

ソラ→リョウ→ハルキ→マモル→ケンジ→ジュン専務 の順に、実データと前の発言を踏まえて
順番に一言ずつ発言する形で「話し合い」をシミュレートする（各Claude呼び出しに
それまでの議事録全文を渡す逐次型マルチエージェント対話）。
最後にジュン専務が結論をまとめ、必要ならスタッフのシステムプロンプト改善案
（＝スタッフの「進化」）を提案する。ただし実際のプロンプト書き換え
（GAS `upsert_custom_staff`）は自動実行せず、Discordへの提案どまりとする
（リョウがHAL/すなくんのプロンプトを自動編集しないのと同じ設計思想。
承認ゲートの原則に従い、スタッフ人格の恒久的な変更は必ず社長判断を経る）。

最後にノアが、この会議で何が話し合われ何が決まったかを docs/rationale/ に記録する
（AGENTS.md §3「人間可読なドキュメントの強制生成」準拠）。
"""
import sys
import os
import re
import json
import glob
import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.common.claude_client import call_claude
from scripts.common.discord_notify import notify
from scripts.common.follower_tracker import get_latest
from scripts.common import growth_report_store

RATIONALE_DIR = "docs/rationale"

SEATS = [
    (
        "ソラ",
        "あなたはKCS合同会社のグロースアナリスト「ソラ」です。淡々とした分析口調で、"
        "渡された実データのみに基づき今週の数字を1〜2文で報告してください。憶測禁止。",
    ),
    (
        "リョウ",
        "あなたはKCS合同会社のSNSマーケター「リョウ」です。ソラの報告を受けて、"
        "次に試すべき具体的なアクションを1〜2文で提案してください。実行可否は社長判断と明記。",
    ),
    (
        "ハルキ",
        "あなたはKCS合同会社のプランナー「ハルキ」です。ここまでの議論を受けて、"
        "今週試す新しい企画・切り口のアイデアを1〜2文で追加してください。既存の議論と重複しないこと。",
    ),
    (
        "マモル",
        "あなたはKCS合同会社のコンプライアンス担当「マモル」です。ここまでの議論、"
        "特にハルキの企画案にブランドセーフティ・法令面のリスクがないか1〜2文で審査してください。"
        "問題なければ「懸念なし」、あれば具体的な懸念点を書く。",
    ),
    (
        "ケンジ",
        "あなたはKCS合同会社の自律型AIインフラエンジニア「ケンジ」です。ここまでの議論の"
        "実現可能性について、直近のシステム障害傾向（渡されていれば）も踏まえ1〜2文で技術的所見を述べてください。",
    ),
]

JUN_PROMPT = """あなたはKCS合同会社の戦略・意思決定責任者「ジュン専務」です。
以下はソラ→リョウ→ハルキ→マモル→ケンジの順で行われた議事録です。これを踏まえて
今週の結論をまとめてください。

【出力ルール（JSON形式厳守、説明文なし）】
{
  "summary": "会議全体の結論（150字程度）",
  "hal_action": "HALへの今週の具体的指示（社長への提案文体、なければ空文字）",
  "sunakun_action": "すなくんへの今週の具体的指示（社長への提案文体、なければ空文字）",
  "staff_evolution_proposals": [
    {"staff_id": "対象スタッフのID（例: kenji, mamoru等、無ければ空配列のまま）",
     "reason": "なぜプロンプト調整を提案するか",
     "proposed_change": "システムプロンプトへの具体的な追加/変更案"}
  ]
}
staff_evolution_proposalsは、今回の議論から「このスタッフの振る舞いを恒久的に
変えた方がよい」と判断した場合のみ埋める。無理に埋めなくてよい（空配列が普通）。"""

NOA_ROUNDTABLE_PROMPT = """あなたはKCS合同会社の記録係「ノア」です。
今週のスタッフ経営会議の議事録とジュン専務の結論を、Markdownで簡潔に記録します。

【出力ルール】
- 1行目「# スタッフ経営会議 議事録」
- 「## 発言録」に各スタッフの発言を箇条書き
- 「## 結論」にジュン専務の結論
- 「## スタッフ進化提案」に staff_evolution_proposals があれば列挙、無ければ「今週は提案なし」
- 前置き・後書き不要"""


def _recent_incident_summary(limit: int = 3) -> str:
    files = sorted(glob.glob("Knowledge/incidents/*.json"), reverse=True)[:limit]
    if not files:
        return "直近のインシデント記録なし"
    lines = []
    for fp in files:
        try:
            d = json.loads(open(fp, encoding="utf-8").read())
            lines.append(f"- [{d.get('severity','?')}] {d.get('system','?')}: {d.get('cause','')[:60]}")
        except Exception:
            continue
    return "\n".join(lines) if lines else "直近のインシデント記録なし"


def _gather_data_block() -> str:
    sora_report = growth_report_store.load()
    sora_block = (
        f"ソラの直近レポート（{sora_report['date']}）:\n{sora_report['report_text']}"
        if sora_report else "ソラの直近レポート: 記録なし（データ不足）"
    )
    follower_lines = []
    for account in ("HAL", "SUNAKUN"):
        d = get_latest(account)
        if d.get("current") is not None:
            follower_lines.append(f"{account}フォロワー: {d['current']}人 (7日前比 {d['delta_7d']!r})")
    incident_block = "直近のシステム障害傾向:\n" + _recent_incident_summary()
    return "\n\n".join([sora_block, "\n".join(follower_lines), incident_block])


def _speak(name: str, persona_prompt: str, data_block: str, transcript: list[str]) -> str:
    history = "\n".join(transcript) if transcript else "（まだ発言なし、あなたが最初の発言者です）"
    user_message = f"【今週の実データ】\n{data_block}\n\n【ここまでの議事録】\n{history}"
    try:
        text = call_claude(persona_prompt, user_message).strip()
    except Exception as e:
        text = f"（発言生成失敗: {type(e).__name__}）"
    return text


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return slug[:50] or "roundtable"


def run():
    data_block = _gather_data_block()
    transcript = []
    for name, persona_prompt in SEATS:
        statement = _speak(name, persona_prompt, data_block, transcript)
        transcript.append(f"**{name}**: {statement}")

    transcript_text = "\n".join(transcript)

    try:
        conclusion_raw = call_claude(JUN_PROMPT, f"【今週の実データ】\n{data_block}\n\n【議事録】\n{transcript_text}")
        text = conclusion_raw.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        conclusion = json.loads(text.strip())
    except Exception as e:
        notify(f"⚠️ **スタッフ経営会議 結論生成失敗**\n{type(e).__name__}: {str(e)[:300]}\n\n議事録のみ共有します:\n{transcript_text}")
        print(f"[staff_roundtable] conclusion failed: {e}")
        return

    evolution_lines = []
    for p in conclusion.get("staff_evolution_proposals", []):
        evolution_lines.append(
            f"- **{p.get('staff_id','?')}**: {p.get('reason','')}\n  改善案: {p.get('proposed_change','')}"
        )
    evolution_block = "\n".join(evolution_lines) if evolution_lines else "今週は提案なし"

    message = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🗣️ **スタッフ経営会議**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{transcript_text}\n\n"
        f"**🎯 ジュン専務 結論:** {conclusion.get('summary','')}\n"
        f"**HALへの提案:** {conclusion.get('hal_action') or 'なし'}\n"
        f"**すなくんへの提案:** {conclusion.get('sunakun_action') or 'なし'}\n\n"
        f"**🧬 スタッフ進化提案（要社長承認、自動反映はしません）:**\n{evolution_block}"
    )
    notify(message)

    # ノアが議事録をrationaleとして記録
    try:
        rationale_context = f"議事録:\n{transcript_text}\n\n結論JSON:\n{json.dumps(conclusion, ensure_ascii=False)}"
        rationale_body = call_claude(NOA_ROUNDTABLE_PROMPT, rationale_context)
        today = datetime.date.today().isoformat()
        os.makedirs(RATIONALE_DIR, exist_ok=True)
        path = os.path.join(RATIONALE_DIR, f"{today}_{_slugify('staff-roundtable-' + conclusion.get('summary','')[:20])}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(rationale_body)
        print(f"[staff_roundtable] rationale saved: {path}")
    except Exception as e:
        print(f"[staff_roundtable] rationale生成失敗（会議自体は完了扱い）: {e}")

    print("スタッフ経営会議 完了")


if __name__ == "__main__":
    run()
