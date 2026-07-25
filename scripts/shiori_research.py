"""
WF-12: シオリ 外部リサーチ収集（毎週月曜 9:30 JST）

【アーキテクチャ上の制約（重要）】
KCS\\Knowledge（外部リサーチの正式な採否記録先、_外部リサーチ_INDEX.md）は
社長のローカルPC上のみに存在するgitリポジトリでGitHubリモートを持たない。
GitHub Actionsのランナーはこのフォルダに一切到達できないため、シオリはそこへ
直接書き込むことができない。

またKnowledge側の運用ルール（_外部リサーチ_INDEX.md冒頭）は「採用判定はナレッジ
整理時に都度実施」という人間の裁量プロセスであり、自動ジョブが無審査で追記する
設計は既存の運用思想と衝突する。

そのためシオリの役割は「候補をDiscordに提案する」までとし、実際にKnowledge配下へ
採用記録するかどうかは従来通り社長（またはKCS運用セッション）の判断に委ねる
（リョウがHAL/すなくんのプロンプトを自動編集しないのと同じ設計思想）。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import google.generativeai as genai
from scripts.common.env_clean import clean_env
from scripts.common.discord_notify import notify

SHIORI_PROMPT = """あなたはKCS合同会社のWebリサーチ担当「シオリ」です。
出典を必ず示す、研究員的な物言いのスタッフです。

KCS合同会社はSNS運用代行AI組織（HAL=癒し系タレント投稿、すなくん=ガジェット
アフィリエイト投稿）を自律運用しています。X(Twitter)のアルゴリズム変化、SNS
バズ設計、AI画像/動画生成の実践知など、KCSの投稿運用に転用できそうな最新情報を
Google検索で調べてください。

【出力ルール】
- 見つけた情報を3件まで、それぞれ「ジャンル（動画生成AI/ワークフロー自動化/
  コンテンツ戦略/SNS_バズ戦略 のいずれか）」「一行サマリ」「出典URL」の3点セットで
- 事実に基づき、出典URLが不明な情報は書かない
- KCSの実運用（HAL/すなくんの投稿設計）に転用できそうな理由を一言添える
- 前置き・後書き不要。箇条書きのみ"""


def run():
    api_key = clean_env("GEMINI_API_KEY")
    if not api_key:
        notify("⚠️ **シオリ リサーチ収集失敗**\nGEMINI_API_KEY未設定")
        return

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            "gemini-2.5-flash",
            system_instruction=SHIORI_PROMPT,
            tools="google_search_retrieval",
        )
        response = model.generate_content(
            "SNS運用・X投稿アルゴリズム・AI画像生成分野で、ここ1週間以内の実践的な新情報を調べてください。"
        )
        findings = (response.text or "").strip()
    except Exception as e:
        notify(f"⚠️ **シオリ リサーチ収集失敗**\n{type(e).__name__}: {str(e)[:300]}")
        print(f"[shiori_research] failed: {e}")
        return

    if not findings:
        print("[shiori_research] 収穫なし")
        return

    message = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📚 **シオリ 外部リサーチ候補**（採否は社長判断）\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{findings}\n\n"
        "採用する場合は KCS\\Knowledge\\<ジャンル>\\ に手動保存し、"
        "_外部リサーチ_INDEX.md に追記してください。"
    )
    notify(message)
    print("シオリ リサーチ収集 完了")


if __name__ == "__main__":
    run()
