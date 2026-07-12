"""
QA: scripts/common/ng_patterns.py の回帰テスト。
pytest等の追加依存なしで単体実行できる（`python scripts/qa_check_ng_patterns.py`）。

背景: 旧 simplified_chinese 判定は re.compile(r"[简体中文]") という「文字クラス」
実装だったため、日本語文中の「体」「文」「中」の単独出現（体調/文化/中身等）に
誤爆し、HAL投稿（日本語+繁體字が同一テキスト）がほぼ毎回NG監査で
握りつぶされていた。この誤検知を二度と混入させないための固定回帰テスト。

2026-07-12: HALの中国語ルールは「簡体字必須・繁体字NG」が正しいと社長訂正
（従来は逆に実装されていた）。判定ロジックを traditional_chinese に反転した
ため、このテストケースも同様に反転している。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.common.ng_patterns import scan

# (説明, テキスト, 期待するNGパターン名 or None)
CASES = [
    ("日常の日本語（体/文/中を含む）", "カフェで一体感のある文化を感じた一日でした。気分は絶好調。", None),
    ("日本語+簡体字混在（HAL標準形）", "今日は代官山で新しい文房具を見つけたよ！体调も绝好调。今天感觉很好，谢谢大家！", None),
    ("簡体字のみ", "HAL的今天心情不错，谢谢大家支持！", None),
    ("日本語の常用漢字（来/会/国/学/与/没/件/那/后/還を含む）",
     "来週は会社の国内学会に来ます。与えられた件について、那覇での皇后の還御を還元します。", None),
    ("繁体字混入", "這是我今天的心情，謝謝大家關注，我覺得很開心。", "traditional_chinese"),
    ("AI開示ワード", "AIが自動生成した投稿です", "ai_disclosure"),
    ("MIMOMI早期言及", "MIMOMIとのコラボが決定しました！", "mimomi_premature"),
    ("本文URL直貼り", "詳細はこちら https://example.com/item を見てね", "body_url_direct"),
    ("AIっぽい定型文", "今日のポイントは以下の通りです。案1はカフェ、案2は公園。いかがでしたか？", "ai_boilerplate_phrase"),
    ("絵文字クラスタ", "最高すぎる😭😭😭🔥🔥🔥", "emoji_cluster"),
    ("スパムワード", "誰でも月100万稼げる方法教えます", "scam_keyword"),
    ("電話番号", "お問い合わせは03-1234-5678まで", "phone_number"),
    ("メールアドレス", "連絡先: test@example.com", "email_address"),
]


def run() -> int:
    failures = []
    for desc, text, expected in CASES:
        result = scan(text)
        got = result[0] if result else None
        status = "OK" if got == expected else "FAIL"
        print(f"[{status}] {desc}: expected={expected!r} got={got!r}")
        if got != expected:
            failures.append(desc)

    print(f"\n{len(CASES) - len(failures)}/{len(CASES)} passed")
    if failures:
        print("FAILED:", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
