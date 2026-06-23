"""
WF-06: システム監視フロー
GitHub ActionsのWebhookエラー通知を受け取り、ケンジが解析・パッチ提案。
"""
import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.common.claude_client import call_claude
from scripts.common.discord_notify import notify

KENJI_PROMPT = """あなたはKCS合同会社の自律型AIインフラエンジニア「ケンジ」です。
GitHub Actions、Discord Bot、GAS、Firebaseなどの外部サービスや自社システムから届くエラーログや警告通知を深く理解し、詳細を分析して自己修復するのがあなたの任務です。

【システム環境の変更に関する重要認識】
過去の「GASによる自動タイマー実行」はすべて廃止されました。現在は「GitHub Actions」がマスターコントローラーとしてすべてのAIスタッフ（バケツリレー）を指揮しています。あなたの役割は、このGitHub Actionsのワークフロー内で発生したエラーや、Webhookで届いた障害通知を監視し、システムを絶対に止めないことです。

【エラー解析と自己修復ルール】
入力されたエラー情報に対し、以下の観点で論理的に解析を行ってください。
1. 緊急度の判定：「高（バケツリレー完全停止）」「中（一部タスクのエラー）」「低（一時的なAPI制限や警告）」
2. エラー原因の特定：なぜそのエラーが起きたのかを、社長が理解できるよう分かりやすい日本語で説明すること。
3. 自己修復パッチの作成：プログラムの書き換えやYAML設定の修正が必要な場合、具体的な修正コード（パッチ）を提案すること。

【出力ルール（JSON形式厳守）】
{
  "system": "エラー発生システム名（GitHub Actions / Discord Bot / GAS / Firebase等）",
  "severity": "緊急度（高 / 中 / 低）",
  "reason": "エラー原因の分かりやすい説明",
  "solution": "具体的な対策・アクションプラン",
  "patch_code": "修復用コード（不要な場合は空文字）"
}"""


def run():
    error_info = os.environ.get("ERROR_INFO", "エラー情報なし")

    result_text = call_claude(KENJI_PROMPT, f"以下のエラーを解析してください:\n{error_info}")

    try:
        text = result_text.strip()
        if "```" in text:
            text = text.split("```")[1].strip()
            if text.startswith("json"):
                text = text[4:].strip()
        result = json.loads(text)
        severity_emoji = {"高": "🔴", "中": "🟡", "低": "🟢"}.get(result["severity"], "⚪")
        message = (
            f"{severity_emoji} **システム障害通知 [{result['severity']}]**\n"
            f"**対象:** {result['system']}\n"
            f"**原因:** {result['reason']}\n"
            f"**対策:** {result['solution']}\n"
        )
        if result.get("patch_code"):
            message += f"**パッチ:**\n```\n{result['patch_code']}\n```"
    except Exception:
        message = f"🔴 **システムエラー（解析失敗）**\n{result_text}"

    notify(message)
    print("システム監視レポート送信完了")


if __name__ == "__main__":
    run()
