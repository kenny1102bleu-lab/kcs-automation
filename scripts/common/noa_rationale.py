"""
ノア：自己修復パッチの設計意図（Rationale）を自動記録する。
ケンジ(system_monitor.py)がコード修正をmaster直push/PR作成する際、
そのたびに「なぜ直したか」を docs/rationale/ に人間可読なMarkdownとして残す。
AGENTS.md §3「人間可読なドキュメントの強制生成: 意図なき変更はロールバック対象」に対応。
生成に失敗しても既存の自己修復フロー自体は止めない（既存挙動を壊さない方針を踏襲）。
"""
import re
import datetime
import pathlib

from scripts.common.claude_client import call_claude

RATIONALE_DIR = pathlib.Path("docs/rationale")

NOA_PROMPT = """あなたはKCS合同会社の記録係「ノア」です。
自己修復エンジニア「ケンジ」が行ったコード修正について、その設計意図（Rationale）を
Markdownで簡潔に記録します。

【出力ルール】
- 1行目は「# Rationale: <対象システム> - <一言要約>」の見出し
- 続けて「## 背景」「## 原因」「## 対策」の3セクション
- 各セクション2〜4行、簡潔に。渡された情報のみに基づき、憶測や誇張を書かない
- 前置き・後書き不要。Markdown本文のみ出力すること"""


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return slug[:50] or "auto-fix"


def write_rationale_doc(system: str, severity: str, reason: str, solution: str, applied_files: list[str]) -> str | None:
    """rationale mdを生成しファイルに書き込む。呼び出し側のgit add -Aで同じコミットに含まれる。"""
    try:
        context = (
            f"対象システム: {system}\n重要度: {severity}\n原因: {reason}\n対策: {solution}\n"
            f"変更ファイル: {', '.join(applied_files) or 'なし'}"
        )
        body = call_claude(NOA_PROMPT, context)
    except Exception as e:
        print(f"[ノア] rationale生成失敗（修復フローは継続）: {e}")
        return None

    today = datetime.date.today().isoformat()
    slug = _slugify(f"{system}-{reason}")
    RATIONALE_DIR.mkdir(parents=True, exist_ok=True)
    path = RATIONALE_DIR / f"{today}_{slug}.md"
    try:
        path.write_text(body, encoding="utf-8")
        return str(path)
    except Exception as e:
        print(f"[ノア] rationale書き込み失敗（修復フローは継続）: {e}")
        return None
