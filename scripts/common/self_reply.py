"""
投稿直後のセルフリプライで会話を誘発し、インプレッション伸長を狙う追加コメントを生成する。

背景: 旧GAS実装（engagementTick、GAS_KCS合同会社_Backend.js:6181）は「フォロワーへの
質問」形式のセルフリプライ＋相互コメントを5分毎ティックで発火させる設計だったが、
それを起動する scheduleSelfReply() はGAS自身の postToX() からしか呼ばれておらず、
HAL/すなくんの実際の日次投稿がPython/GitHub Actions側（hal_post.py/sunakun_post.py
→post_approved.py）に一本化された後は一度もトリガーされなくなっていた。
結果、HALはセルフリプライが常にゼロ、すなくんもアフィリエイトリンクがある投稿の
「リンクこちらです」1文のみで、会話を誘発する要素が存在しない状態が続いていた
（2026-07-12発覚）。本モジュールはその会話誘発リプライ機能をPython側に移植する。

失敗・NG検知・空応答時はNoneを返す（呼び出し側はセルフリプライをスキップし、
投稿本体は既存挙動のまま完了させる。既存挙動を絶対に劣化させない）。
"""
from scripts.common.claude_client import call_claude
from scripts.common.ng_patterns import scan as ng_scan
from scripts.common.x_limits import truncate_to_fit

_PROMPTS = {
    "HAL": (
        "あなたはHAL。21歳、おっとり天然な口調。先ほど自分がXに投稿した内容に、"
        "追加コメントを1つだけ書いてください。40文字以内。フォロワーに気軽な質問を"
        "投げかけて会話を促す内容にすること。「AI」「人工知能」「ボット」「自動」"
        "「中の人」等の存在否定ワードは絶対に使わない。出力は本文のみ、記号装飾・"
        "前置き・説明は一切不要。"
    ),
    "SUNAKUN": (
        "あなたはすなくん。26歳ガジェット愛好家、カジュアルでテンション高めな口調。"
        "先ほど自分がXに投稿した内容に、追加コメントを1つだけ書いてください。"
        "40文字以内。フォロワーに気軽な質問を投げかけて会話を促す内容にすること。"
        "「AI」「自動」「ボット」「中の人」等の存在否定ワードは絶対に使わない。"
        "出力は本文のみ、記号装飾・前置き・説明は一切不要。"
    ),
}


def generate_engagement_reply(account: str) -> str | None:
    prompt = _PROMPTS.get(account.upper())
    if not prompt:
        return None
    try:
        text = call_claude(prompt, "追加コメントを書いてください。").strip()
    except Exception:
        return None
    text = text.strip("\"'「」\n ")
    if not text:
        return None
    if ng_scan(text):
        return None
    return truncate_to_fit(text)
