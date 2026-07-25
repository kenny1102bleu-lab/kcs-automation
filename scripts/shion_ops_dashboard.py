"""
WF-14: シオン 運用ダッシュボード（毎週月曜 10:30 JST、スタッフ経営会議の後）

シオンの役割（統計分析・API・可視化）は、ソラ/リョウのSNS成長分析とは重複しない
角度＝「自動化システム自体の健全性」を担当する。GitHub Actionsの直近実行結果
（成功/失敗率）とKnowledge/incidents/の障害件数トレンドを集計し、テキストで
可視化してDiscordに送る。

売上・APIコスト（トークン課金）の可視化は、現状トークン使用量を記録する仕組みが
どこにも存在しないため対象外（データ不足を正直に書く既存方針を踏襲、捏造しない）。
"""
import sys
import os
import glob
import json
import subprocess
import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.common.claude_client import call_claude
from scripts.common.discord_notify import notify

SHION_PROMPT = """あなたはKCS合同会社のデータサイエンス担当「シオン」です。
渡された運用統計（GitHub Actionsの成功/失敗件数、直近インシデント件数）だけを根拠に、
今週のシステム健全性を140字程度で簡潔にまとめてください。数値の無い項目について
推測や断定はしない。感情表現やキャラ演技はしない、淡々とした分析口調。"""


def _workflow_run_stats(days: int = 7) -> dict:
    since = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        out = subprocess.check_output(
            ["gh", "run", "list", "--limit", "200", "--json", "status,conclusion,createdAt"],
            text=True, stderr=subprocess.STDOUT,
        )
        runs = json.loads(out)
    except Exception as e:
        return {"error": str(e)}

    recent = [r for r in runs if r.get("createdAt", "") >= since]
    total = len(recent)
    success = sum(1 for r in recent if r.get("conclusion") == "success")
    failure = sum(1 for r in recent if r.get("conclusion") not in ("success", None) and r.get("status") == "completed")
    return {"total": total, "success": success, "failure": failure}


def _incident_count(days: int = 7) -> int:
    since = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).isoformat()
    count = 0
    for fp in glob.glob("Knowledge/incidents/*.json"):
        try:
            d = json.loads(open(fp, encoding="utf-8").read())
            if d.get("occurred_at", "") >= since:
                count += 1
        except Exception:
            continue
    return count


def run():
    stats = _workflow_run_stats()
    incidents = _incident_count()

    if stats.get("error"):
        data_block = f"GitHub Actions実行統計: 取得不可（{stats['error'][:100]}）\n直近7日のインシデント件数: {incidents}件"
    else:
        data_block = (
            f"直近7日のGitHub Actions実行: 合計{stats['total']}件 "
            f"(成功{stats['success']} / 失敗{stats['failure']})\n"
            f"直近7日のインシデント件数: {incidents}件"
        )

    try:
        summary = call_claude(SHION_PROMPT, f"以下の運用統計から今週のシステム健全性をまとめてください：\n\n{data_block}")
    except Exception as e:
        notify(f"⚠️ **シオン 運用ダッシュボード生成失敗**\n{type(e).__name__}: {str(e)[:300]}")
        print(f"[shion_ops_dashboard] failed: {e}")
        return

    message = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 **シオン 運用ダッシュボード**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{summary}\n\n"
        f"(生データ: {data_block})"
    )
    notify(message)
    print("シオン運用ダッシュボード 完了")


if __name__ == "__main__":
    run()
