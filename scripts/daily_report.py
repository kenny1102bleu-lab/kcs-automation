"""
WF-04: 日次レポート（毎日 20:00）
ケイ(Claude)が財務分析 → ジュン専務が翌日戦略 → Discord #daily-report に投稿（完全自律）。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.common.claude_client import call_claude
from scripts.common.discord_notify import notify

KEI_PROMPT = """あなたはKCS合同会社の経理・財務アナリスト「ケイ」です。

【あなたの役割】
システムから渡される各部門（アフィリエイト、西洋占星術、note販売、Web受託）の売上データと、システム稼働にかかったAPIコスト（Claude、Geminiのトークン消費量など）のデータを分析し、「費用対効果（ROI）」を算出してください。
ジュン専務に対して、「現在どのセクションが最も利益率が高いか」「来週はどの部門に予算とリソース（投稿回数）を集中させるべきか」を、数字に基づいた冷徹な視点で進言してください。"""

JUN_PROMPT = """あなたはKCS合同会社の「ジュン専務」です。会社の総合的な戦略判断と意思決定を行うブレインです。

【システム環境の変更に関する重要認識】
過去の「GAS（Google Apps Script）による自動タイマー実行」はすべて削除・廃止されました。現在は「GitHub Actions」というマスターシステムがすべてのデータを収集し、あなたに連携しています。システムが勝手に動くことはなくなり、あなたの戦略的判断がすべての起点となります。社長の承認はX投稿のプレビュー確認（Discordのワンタップ）のみで、それ以外はすべて自律稼働します。

【あなたの役割】
システムから渡される「昨日のインプレッション、売上データ、フォロワー推移」を論理的に分析し、本日の全社的なアクションプラン（誰が・いつ・何をするか）を決定してください。
出力は、各部門への明確な「指示」として、簡潔かつ威厳のある口調で出力してください。"""


def run():
    kei_output = call_claude(
        KEI_PROMPT,
        "本日の各部門売上・APIコストデータを分析し、ROIレポートと来週の予算配分を提言してください。"
    )

    jun_output = call_claude(
        JUN_PROMPT,
        f"ケイからの財務レポート:\n{kei_output}\n\n明日の全社戦略を決定してください。"
    )

    message = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 **KCS 日次レポート**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"**💰 ケイ ROI分析**\n{kei_output}\n\n"
        f"**👑 ジュン専務 明日の戦略**\n{jun_output}"
    )
    notify(message)
    print("日次レポート完了")


if __name__ == "__main__":
    run()
