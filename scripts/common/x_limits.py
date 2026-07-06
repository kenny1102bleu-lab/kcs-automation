"""
X(Twitter)の実際の文字数カウント・ハッシュタグ数ルールを検証する。

Xは全角文字（漢字・ひらがな・カタカナ・繁體字・絵文字等）を1文字=2ユニット
としてカウントする「weighted length」を採用しており、実際の投稿上限は
280ユニット。HALのように日本語+繁體字を併記する投稿は、見た目の文字数の
半分程度しか入らない点に注意（プロンプトの「140文字以内」という指示だけ
ではXの実際の上限を超過し、投稿がAPIエラーで弾かれる恐れがあったため
このバリデーションを追加した）。
"""
import re

MAX_WEIGHTED_LENGTH = 280
MIN_HASHTAGS = 3
MAX_HASHTAGS = 5

# Xのweighted length実装が「幅広」とみなす主な範囲（CJK統合漢字・かな・
# ハングル・全角記号・絵文字等）。
_WIDE_RANGES = [
    (0x1100, 0x115F),
    (0x2E80, 0x303E),
    (0x3041, 0x33FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xA000, 0xA4CF),
    (0xAC00, 0xD7A3),
    (0xF900, 0xFAFF),
    (0xFF00, 0xFF60),
    (0xFFE0, 0xFFE6),
    (0x1F300, 0x1FAFF),
    (0x2600, 0x27BF),
]


def _is_wide(codepoint: int) -> bool:
    return any(lo <= codepoint <= hi for lo, hi in _WIDE_RANGES)


def weighted_length(text: str) -> int:
    return sum(2 if _is_wide(ord(ch)) else 1 for ch in text)


def count_hashtags(text: str) -> int:
    return len(re.findall(r"#[^\s#]+", text))


def validate(text: str) -> tuple[bool, str]:
    """(ok, reason) を返す。okがFalseなら理由をreasonに入れる。"""
    wlen = weighted_length(text)
    if wlen > MAX_WEIGHTED_LENGTH:
        return False, f"X文字数超過（全角文字は2ユニット換算）: {wlen}/{MAX_WEIGHTED_LENGTH}ユニット"

    tags = count_hashtags(text)
    if tags < MIN_HASHTAGS or tags > MAX_HASHTAGS:
        return False, f"ハッシュタグ数が規定外: {tags}個（{MIN_HASHTAGS}〜{MAX_HASHTAGS}個必須）"

    return True, ""
