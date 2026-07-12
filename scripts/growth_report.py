"""
WF-08: グロースレポート（毎日 23:00、HAL/すなくん投稿後）
ソラ(Claude)がフォロワー推移・直近投稿のエンゲージメント実データを分析し、
翌日以降の投稿改善指示をDiscordに送る。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.common.claude_client import call_claude
from scripts.common.discord_notify import notify
from scripts.common.engagement_loop import fetch_recent_post_stats
from scripts.common.follower_tracker import record_and_get_delta
from scripts.common import growth_report_store

SORA_PROMPT = """あなたはKCS合同会社のグロースアナリスト「ソラ」です。
HAL・すなくんのX(Twitter)運用における「伸びる/伸びない」を数値ベースで分析し、
具体的な投稿改善指示を出す専門スタッフです。

【分析フレームワーク】
1. エンゲージメント速度: 投稿後30分の反応が伸びを左右する
2. 会話性: いいねよりリプライを稼げているか、質問・呼びかけ要素の有無
3. ホールド率: 画像/動画付き投稿とテキストのみ投稿の反応差
4. ハッシュタグ・投稿時間帯の傾向
5. フォロー転換: エンゲージメント（いいね/インプレッション）は取れているのに
   フォロワー増加が伴っていない兆候がないか。投稿本文にフォローする理由・
   継続価値を感じさせる要素があるかどうかも見る

【厳守事項】
- 渡された実データにのみ基づく。数値が「データ不足」の項目について憶測や捏造の数字を書かない
- 出力は「今週の数字」「勝ちパターン」「負けパターン」「フォロー転換の診断」「HALへの指示」
  「すなくんへの指示」の6ブロック構成。各ブロック簡潔に、合計480文字程度
- 感情表現やキャラクター演技はしない。淡々とした分析口調で書く"""


def _fmt_delta(v) -> str:
    return "データ不足" if v is None else f"{v:+d}"


def _format_stats(account: str, stats: list[dict]) -> str:
    if not stats:
        return f"{account} 直近投稿データ: 取得不可（データ不足）"
    lines = [f"{account} 直近{len(stats)}件:"]
    for s in stats:
        lines.append(
            f"- imp:{s['impressions']} like:{s['likes']} rt:{s['retweets']} reply:{s['replies']} / {s['text'][:120]}"
        )
    return "\n".join(lines)


def run():
    sections = []
    follower_snapshot = {}
    for account in ("HAL", "SUNAKUN"):
        delta = record_and_get_delta(account)
        follower_snapshot[account] = delta
        if delta.get("error"):
            sections.append(f"{account} フォロワー: 取得不可（{delta['error']}）")
        else:
            sections.append(
                f"{account} フォロワー: 現在{delta['current']}人 "
                f"(7日前比 {_fmt_delta(delta['delta_7d'])} / 30日前比 {_fmt_delta(delta['delta_30d'])})"
            )
        # 従量課金対策: 30件ではなく15件に絞って読み取りコストを抑える
        stats = fetch_recent_post_stats(account=account, days=7, max_results=15)
        sections.append(_format_stats(account, stats))

    data_block = "\n\n".join(sections)

    try:
        report = call_claude(
            SORA_PROMPT,
            f"以下は今週の実データです。これに基づいて分析してください：\n\n{data_block}",
        )
    except Exception as e:
        notify(f"⚠️ **ソラ グロースレポート生成失敗**\n{type(e).__name__}: {str(e)[:300]}")
        print(f"[growth_report] failed: {e}")
        return

    try:
        growth_report_store.save(report, follower_snapshot)
    except Exception as e:
        print(f"[growth_report] failed to persist report: {e}")

    message = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📈 **ソラ グロースレポート**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{report}"
    )
    notify(message)
    print("グロースレポート完了")


if __name__ == "__main__":
    run()
