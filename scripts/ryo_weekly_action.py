"""
WF-11: リョウ 週次改善提案（毎週月曜 9:00 JST）
ソラ(growth_report.py)が観測した実データを受けて、次回投稿で試すべき具体的な
「型」を提案する。HAL/すなくんのプロンプトを自動編集することはしない
（Discordへの提案どまり、反映は社長判断）。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.common.claude_client import call_claude
from scripts.common.discord_notify import notify
from scripts.common.follower_tracker import get_latest
from scripts.common.engagement_loop import get_win_patterns
from scripts.common import growth_report_store

RYO_PROMPT = """あなたはKCS合同会社のSNSマーケター「リョウ」です。
グロースアナリスト「ソラ」が出した分析データと、直近の勝ち/負けパターンを受け取り、
「次回の投稿で具体的に何を試すべきか」を実行可能なアクションに落とし込む専門スタッフです。

【役割の境界（重要）】
- ソラ＝データ観測・報告。リョウ＝そのデータを元にした改善アクション提案。
- あなたはHAL/すなくんのシステムプロンプトを直接書き換える権限を持たない。
  「次回試すこと」を社長への提案として明記するに留める。
- 実行可否・頻度の最終判断は社長が行う。「〜してください」ではなく
  「〜を試すことを提案します」の文体で書く。

【出力ルール】
- 「今週のソラのデータ要点（1行）」「HALへの提案」「すなくんへの提案」の3ブロック構成
- 各ブロック簡潔に、合計350文字程度
- データが無い/薄い場合は「データ不足のため今週は提案なし」と正直に書く（憶測禁止）
- 感情表現やキャラクター演技はしない。淡々とした実務口調"""


def run():
    sora_report = growth_report_store.load()
    sora_block = (
        f"【ソラの直近レポート（{sora_report['date']}）】\n{sora_report['report_text']}"
        if sora_report else "【ソラの直近レポート】記録なし（データ不足）"
    )

    follower_lines = []
    for account in ("HAL", "SUNAKUN"):
        d = get_latest(account)
        if d.get("current") is None:
            follower_lines.append(f"{account} フォロワー: 記録なし")
        else:
            follower_lines.append(
                f"{account} フォロワー: 現在{d['current']}人 "
                f"(7日前比 {d['delta_7d']!r} / 30日前比 {d['delta_30d']!r})"
            )

    win_pattern_lines = []
    for account in ("HAL", "SUNAKUN"):
        wp = get_win_patterns(account=account, days=7)
        win_pattern_lines.append(f"{account} 勝ち/負けパターン: {wp}" if wp else f"{account} 勝ち/負けパターン: データ不足")

    data_block = "\n\n".join([sora_block, "\n".join(follower_lines), "\n".join(win_pattern_lines)])

    try:
        proposal = call_claude(RYO_PROMPT, f"以下のデータを元に、次回投稿の改善提案をまとめてください：\n\n{data_block}")
    except Exception as e:
        notify(f"⚠️ **リョウ 週次改善提案 生成失敗**\n{type(e).__name__}: {str(e)[:300]}")
        print(f"[ryo_weekly_action] failed: {e}")
        return

    message = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 **リョウ 週次改善提案**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{proposal}"
    )
    notify(message)
    print("リョウ週次改善提案 完了")


if __name__ == "__main__":
    run()
