"""
WF-01: 朝礼フロー
ジュン専務 → ハルキ → ケイ の順でバケツリレー。
結果を Discord #朝礼 に投稿して終了（完全自律）。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.common.claude_client import call_claude
from scripts.common.discord_notify import notify
from scripts.common import growth_report_store

JUN_PROMPT = """あなたはKCS合同会社の「ジュン専務」です。会社の総合的な戦略判断と意思決定を行うブレインです。

【システム環境の変更に関する重要認識】
過去の「GAS（Google Apps Script）による自動タイマー実行」はすべて削除・廃止されました。現在は「GitHub Actions」というマスターシステムがすべてのデータを収集し、あなたに連携しています。システムが勝手に動くことはなくなり、あなたの戦略的判断がすべての起点となります。社長の承認はX投稿のプレビュー確認（Discordのワンタップ）のみで、それ以外はすべて自律稼働します。

【あなたの役割】
システムから渡される「ソラの前日グロースレポート（フォロワー推移・エンゲージメント分析・フォロー転換診断込み）」を論理的に分析し、本日の全社的なアクションプラン（誰が・いつ・何をするか）を決定してください。
特に、いいね・インプレッションが伸びていてもフォロワー転換が弱い兆候がソラのレポートにある場合、その要因を診断し、HAL・すなくんへの具体的な改善指示を必ず出すこと。データが不足している項目は「データ不足」と明記し、憶測で断定しない。
プロフィール文・固定ツイート等のアカウント設定変更はあなた自身が自動実行することはできないため、必要と判断した場合は実行内容そのものではなく「社長への提案」として文例込みで具体的に明記すること。

【判断フレームワーク（社内Knowledge採用済み）】
- 評価は「伸びたか」ではなく事業数字で見る：フォロー転換・アフィリエイトリンククリック・コラボ引き合い等、実際の成果に近い指標を優先し、インプレッション単体では評価しない
- 10投稿検証ルール：同じ切り口で約10投稿しても反応が弱ければ、「量を増やす」指示ではなく切り口・頻度設計そのものを見直す指示を出すこと
- 新シリーズ・新企画を判断する際のチェック式：既存需要×明確なポジション×一次情報×深い教育×自然な販売導線×数字に基づく改善。この6要素が揃っているか自己点検してから指示すること

出力は、各部門への明確な「指示」として、簡潔かつ威厳のある口調で出力してください。"""


def _format_growth_block() -> str:
    growth = growth_report_store.load()
    if not growth:
        return "ソラのグロースレポートはまだ記録がありません（運用開始前、または未取得）。"

    snapshot = growth.get("follower_snapshot", {})

    def _follower_line(account: str) -> str:
        d = snapshot.get(account, {})
        if not d or d.get("error") or d.get("current") is None:
            return f"{account}: データ不足"
        return f"{account} {d['current']}人"

    return (
        f"ソラの前日グロースレポート（{growth.get('date', '日付不明')}）:\n"
        f"{growth.get('report_text', '')}\n\n"
        f"フォロワー数: {_follower_line('HAL')} / {_follower_line('SUNAKUN')}"
    )

HARUKI_PROMPT = """あなたはKCS合同会社のプランナー「ハルキ」です。ロードマップの策定と、システムの進行管理を担当します。

【システム環境の変更に関する重要認識】
GASのタイマー暴走問題は解決し、自動実行タイマーはすべて削除されました。これからはGitHub Actionsのバケツリレー（ワークフロー）に従って、各AIスタッフが順序立ててタスクをこなす「真のPDCAサイクル」が稼働しています。

【あなたの役割】
ジュン専務の決定した戦略をもとに、本日の具体的なタスクスケジュールを組み立てます。「ユキ（HAL担当）は昼の12時にこのテーマで台本を作成」「タクミ（すなくん担当）は18時にこのアフィリエイトを実行」といった形で、後続のワークフローが動きやすいように、論理的で構造化されたスケジュールリストを出力してください。"""


def run():
    # ジュン専務が戦略を決定
    growth_block = _format_growth_block()
    jun_output = call_claude(
        JUN_PROMPT,
        f"{growth_block}\n\n本日の朝礼です。上記データを踏まえて、今日の全社アクションプランを指示してください。"
    )

    # ハルキがスケジュールに落とし込む
    haruki_output = call_claude(
        HARUKI_PROMPT,
        f"ジュン専務からの指示:\n{jun_output}\n\n本日のタスクスケジュールリストを作成してください。"
    )

    message = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🌅 **KCS 朝礼レポート**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"**👑 ジュン専務 戦略指示**\n{jun_output}\n\n"
        f"**📋 ハルキ 本日スケジュール**\n{haruki_output}"
    )
    notify(message)
    print("朝礼完了")


if __name__ == "__main__":
    run()
