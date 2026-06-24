"""
WF-06: システム監視フロー（ケンジ自己修復PR方式）

エラー情報を受け取り → ケンジが解析 → patch_code があれば自動でブランチ+PR作成 →
Discordに『!approve_patch <PR番号>』で承認可能と通知。
社長承認(merge)後はRender自動デプロイで反映。完全に可逆。
"""
import sys
import os
import json
import subprocess
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.common.claude_client import call_claude
from scripts.common.discord_notify import notify

KENJI_PROMPT = """あなたはKCS合同会社の自律型AIインフラエンジニア「ケンジ」です。
GitHub Actions、Discord Bot、GAS、Firebase等のエラー通知を解析し、自己修復を提案します。

【エラー解析ルール】
1. 緊急度判定: 「高（停止）」「中（一部障害）」「低（警告のみ）」
2. 原因特定: 社長が理解できる日本語で
3. 自己修復パッチ: コード/YAMLの具体的差分を提案。直接書換ではなく、対象ファイルパスと差分形式で

【出力ルール（JSON厳守、説明文なし）】
{
  "system": "GitHub Actions / Discord Bot / GAS / Render 等",
  "severity": "高 / 中 / 低",
  "reason": "原因の分かりやすい説明",
  "solution": "対策の概要",
  "patches": [
    {"file": "対象ファイルパス（相対）", "old": "修正前コード（unique）", "new": "修正後コード"}
  ]
}
patches が空配列なら「コード修正不要」を意味する。"""


def _sh(cmd: list[str], cwd: str | None = None) -> str:
    return subprocess.check_output(cmd, cwd=cwd, text=True, stderr=subprocess.STDOUT).strip()


def _apply_patches(patches: list[dict]) -> list[str]:
    """patches[].file の old を new に置換。マッチしないものはスキップしreasonに残す。"""
    applied = []
    for p in patches:
        fp = p.get("file", "").strip().lstrip("./")
        old = p.get("old", "")
        new = p.get("new", "")
        if not fp or not old:
            continue
        if not os.path.exists(fp):
            continue
        content = open(fp, encoding="utf-8").read()
        if old not in content:
            continue
        open(fp, "w", encoding="utf-8").write(content.replace(old, new, 1))
        applied.append(fp)
    return applied


def _create_pr(branch: str, title: str, body: str) -> str | None:
    """変更されたファイルを branch にcommit & push & PR作成。PR URLを返す。"""
    try:
        # gitの設定
        _sh(["git", "config", "user.email", "kenji-bot@kcs.local"])
        _sh(["git", "config", "user.name", "Kenji Bot"])
        _sh(["git", "checkout", "-b", branch])
        _sh(["git", "add", "-A"])
        _sh(["git", "commit", "-m", title])
        _sh(["git", "push", "-u", "origin", branch])
        url = _sh(["gh", "pr", "create", "--title", title, "--body", body, "--base", "master"])
        return url
    except subprocess.CalledProcessError as e:
        print(f"PR作成失敗: {e.output}")
        return None


def run():
    error_info = os.environ.get("ERROR_INFO", "エラー情報なし")

    result_text = call_claude(KENJI_PROMPT, f"以下のエラーを解析:\n{error_info}")

    try:
        text = result_text.strip()
        if "```" in text:
            text = text.split("```")[1].strip()
            if text.startswith("json"):
                text = text[4:].strip()
        result = json.loads(text)
    except Exception:
        notify(f"🔴 **ケンジ解析失敗**\n```\n{result_text[:1500]}\n```", channel="error-log")
        return

    severity_emoji = {"高": "🔴", "中": "🟡", "低": "🟢"}.get(result.get("severity", ""), "⚪")
    message = (
        f"{severity_emoji} **システム障害 [{result.get('severity','?')}]**\n"
        f"**対象:** {result.get('system','?')}\n"
        f"**原因:** {result.get('reason','?')}\n"
        f"**対策:** {result.get('solution','?')}\n"
    )

    patches = result.get("patches") or []
    if not patches:
        notify(message + "（コード修正不要）", channel="error-log")
        return

    # パッチ適用 → PR作成
    applied = _apply_patches(patches)
    if not applied:
        message += "⚠️ パッチのoldがソースとマッチせず適用できませんでした。手動確認をお願いします。\n"
        for p in patches:
            message += f"提案: `{p.get('file','?')}`\n```\n- {p.get('old','')[:200]}\n+ {p.get('new','')[:200]}\n```\n"
        notify(message, channel="error-log")
        return

    branch = f"kenji-patch/{int(time.time())}"
    pr_url = _create_pr(
        branch,
        f"fix(kenji): {result.get('system','system')} - {result.get('reason','?')[:60]}",
        f"## 自動修復パッチ\n\n**原因:** {result.get('reason','')}\n**対策:** {result.get('solution','')}\n\n変更ファイル: {', '.join(applied)}\n\n⚠️ ケンジによる自動生成。社長レビュー後にmergeしてください。"
    )

    if pr_url:
        pr_num = pr_url.rstrip("/").split("/")[-1]
        message += (
            f"\n🤖 **自己修復PR作成しました**\n{pr_url}\n"
            f"承認: `!approve_patch {pr_num}` でマージ＆デプロイ\n"
            f"却下: PRを手動でclose"
        )
    else:
        message += "\n⚠️ PR作成失敗（パッチは適用済、手動push必要）"

    notify(message, channel="error-log")
    print(f"システム監視レポート送信完了 (patches={len(applied)})")


if __name__ == "__main__":
    run()
