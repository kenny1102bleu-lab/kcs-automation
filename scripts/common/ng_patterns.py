"""
KCS_NG_CONTENT_PATTERNS - 投稿テキスト・コードパッチ両方で違反検知に使う正規表現リスト。

scan(text) → 違反があれば最初の (pattern_name, matched_text) を返す。なければ None。
"""
import re


KCS_NG_CONTENT_PATTERNS = [
    # 存在否定（HAL/すなくんの「中の人」露呈）
    ("ai_disclosure", re.compile(r"(AI|人工知能|ボット|bot|中の人|自動投稿|自動生成)", re.I)),
    # MIMOMI（タイアップ正式決定前）
    ("mimomi_premature", re.compile(r"MIMOMI", re.I)),
    # X本文への外部リンク直貼り（リプ誘導が必須）
    ("body_url_direct", re.compile(r"https?://(?!t\.co)[^\s]+")),
    # 簡体字混入（HALは繁體字必須）
    ("simplified_chinese", re.compile(r"[简体中文]")),
    # 過剰煽り・スパム
    ("scam_keyword", re.compile(r"(必ず儲かる|絶対稼げる|誰でも月.+万|今すぐクリック)")),
    # 個人情報
    ("phone_number", re.compile(r"0\d{2,4}-\d{2,4}-\d{4}")),
    ("email_address", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
]


def scan(text: str, exclude: list[str] | None = None) -> tuple[str, str] | None:
    """text内の最初のNGパターンを返す。exclude にパターン名を入れるとスキップ。"""
    if not text:
        return None
    exclude = exclude or []
    for name, pat in KCS_NG_CONTENT_PATTERNS:
        if name in exclude:
            continue
        m = pat.search(text)
        if m:
            return (name, m.group(0))
    return None


def is_safe(text: str, exclude: list[str] | None = None) -> bool:
    return scan(text, exclude) is None
