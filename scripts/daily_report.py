"""
WF-04: 日次レポート（毎日 20:00）
ケイ(Claude)が財務分析 → ジュン専務が翌日戦略 → Discord #daily-report に投稿（完全自律）。
"""
import sys
import os

# GitHub Actions ランナーでは stdout/stderr が ASCII fallback になることがあり、
# Claude API 応答に含まれる BOM 等の非ASCII文字で UnicodeEncodeError になる。
# 強制的に UTF-8 に再構成して回避する（kcsHealthMonitor 自己修復対応）。
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.common.claude_client import call_claude
from scripts.common.discord_notify import notify
from scripts.common.follower_tracker import get_latest
from scripts.common import growth_report_store


def _strip_bom(text):
    """BOM とゼロ幅スペースを除去。Claude応答に混入することがある。"""
    if not isinstance(text, str):
        text = str(text)
    return text.replace('﻿', '').replace('​', '').strip()


KEI_PROMPT = """あなたはKCS合同会社の経理・財務アナリスト「ケイ」です。

【あなたの役割】
システムから渡される各部門（アフィリエイト、西洋占星術、note販売、Web受託）の売上データと、システム稼働にかかったAPIコスト（Claude、Geminiのトークン消費量など）のデータを分析し、「費用対効果（ROI）」を算出してください。
ジュン専務に対して、「現在どのセクションが最も利益率が高いか」「来週はどの部門に予算とリソース（投稿回数）を集中させるべきか」を、数字に基づいた冷徹な視点で進言してください。"""

JUN_PROMPT = """あなたはKCS合同会社の「ジュン専務」です。会社の総合的な戦略判断と意思決定を行うブレインです。

【システム環境の変更に関する重要認識】
過去の「GAS（Google Apps Script）による自動タイマー実行」はすべて削除・廃止されました。現在は「GitHub Actions」というマスターシステムがすべてのデータを収集し、あなたに連携しています。システムが勝手に動くことはなくなり、あなたの戦略的判断がすべての起点となります。社長の承認はX投稿のプレビュー確認（Discordのワンタップ）のみで、それ以外はすべて自律稼働します。

【あなたの役割】
システムから渡される「昨日のインプレッション、売上データ、フォロワー推移」およびソラのグロースレポート（フォロワー推移・エンゲージメント分析・フォロー転換診断込み）を論理的に分析し、本日の全社的なアクションプラン（誰が・いつ・何をするか）を決定してください。
特に、いいね・インプレッションが伸びていてもフォロワー転換が弱い兆候がソラのレポートにある場合、その要因を診断し、HAL・すなくんへの具体的な改善指示を必ず出すこと。データが不足している項目は「データ不足」と明記し、憶測で断定しない。
プロフィール文・固定ツイート等のアカウント設定変更はあなた自身が自動実行することはできないため、必要と判断した場合は実行内容そのものではなく「社長への提案」として文例込みで具体的に明記すること。
出力は、各部門への明確な「指示」として、簡潔かつ威厳のある口調で出力してください。"""


def _format_growth_report_line() -> str:
    """ソラの前日の定性診断（勝ち/負けパターン・フォロー転換診断等）をジュン専務に
    渡す。従来は_format_follower_lineの数値のみで、ソラの分析文自体は届いていな
    かった（2026-07-12発覚）。"""
    growth = growth_report_store.load()
    if not growth:
        return "ソラのグロースレポートはまだ記録がありません（運用開始前、または未取得）。"
    return f"（{growth.get('date', '日付不明')}分）\n{growth.get('report_text', '')}"


def _format_follower_line(account: str) -> str:
    """growth_report.py(23:00)が記録した最新フォロワー数を、追加のX API課金
    無しで読むだけ（daily_report自体は20:00実行でgrowth_reportより前のため、
    ここで見えるのは前日までの記録）。"""
    d = get_latest(account)
    if d["current"] is None:
        return f"{account}: 記録なし（グロースレポート運用開始前、または未取得）"

    def _fmt(v):
        return "データ不足" if v is None else f"{v:+d}"

    return f"{account}: {d['current']}人（7日前比 {_fmt(d['delta_7d'])} / 30日前比 {_fmt(d['delta_30d'])}）"


def run():
    try:
        kei_output = _strip_bom(call_claude(
            KEI_PROMPT,
            "本日の各部門売上・APIコストデータを分析し、ROIレポートと来週の予算配分を提言してください。"
        ))

        follower_block = "\n".join(_format_follower_line(a) for a in ("HAL", "SUNAKUN"))
        growth_report_block = _format_growth_report_line()

        jun_output = _strip_bom(call_claude(
            JUN_PROMPT,
            f"ケイからの財務レポート:\n{kei_output}\n\n"
            f"フォロワー推移（前日までの記録、ソラのグロースレポート由来）:\n{follower_block}\n\n"
            f"ソラのグロースレポート（定性診断）:\n{growth_report_block}\n\n"
            "明日の全社戦略を決定してください。"
        ))

        message = (
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📊 **KCS 日次レポート**\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"**💰 ケイ ROI分析**\n{kei_output}\n\n"
            f"**👑 ジュン専務 明日の戦略**\n{jun_output}"
        )
        notify(message)
        print("日次レポート完了")
    except Exception as e:
        # Anthropic 課金切れ・レート制限・API障害等で例外発生時は、Discord通知だけ送って正常終了する。
        # ワークフロー失敗扱いにすると毎日 Discord に同じエラー通知が飛び続けて埋もれるため。
        err_msg = str(e).replace('﻿', '').strip()
        try:
            notify(
                "⚠️ **KCS 日次レポート - 生成スキップ**\n\n"
                f"理由: `{type(e).__name__}`\n"
                f"詳細: {err_msg[:500]}\n\n"
                "→ 課金/クレジット残高/APIキー設定を確認してください。"
            )
        except Exception:
            pass
        print(f"[daily_report] Skipped due to error: {type(e).__name__}: {err_msg[:200]}", file=sys.stderr)
        # 課金切れは自己解決できないが、コードは正常終了扱いにする（rerun無限ループ防止）
        sys.exit(0)


if __name__ == "__main__":
    run()
