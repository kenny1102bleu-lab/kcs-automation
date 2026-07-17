"""
data/growth_report_latest.json からアカウント別のソラ改善指示を取得する。

ソラ（グロースアナリスト、WF-08）が daily で生成する growth_report_latest.json の
report_text には「【HALへの指示】」「【すなくんへの指示】」セクションが含まれる。
このモジュールはその指示を抽出し、各投稿スクリプトのプロンプトに注入するために使う。

ファイル不在・パースエラー・セクション未検出時は None を返す（呼び出し側で安全にスキップ）。
"""
import json
import os
import re


def get_sora_instructions(account: str) -> str | None:
    """
    growth_report_latest.json の report_text から当該アカウントへの指示を抽出して返す。

    account: "HAL" or "SUNAKUN"
    戻り値: 指示文字列（見つからない場合は None）
    """
    try:
        data_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data"
        )
        path = os.path.join(data_dir, "growth_report_latest.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        report_text = data.get("report_text", "")
        if not report_text:
            return None
        label_map = {"HAL": "HAL", "SUNAKUN": "すなくん"}
        label = label_map.get(account.upper(), account)
        # 「【Xへの指示】...次の【Yへの指示】か文末まで」を抽出
        m = re.search(
            rf"【{re.escape(label)}への指示】(.*?)(?=【[^】]+への指示】|$)",
            report_text,
            re.DOTALL,
        )
        if not m:
            return None
        instructions = m.group(1).strip()
        return instructions if instructions else None
    except Exception:
        return None
