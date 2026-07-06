"""
KCS_NG_CONTENT_PATTERNS - 投稿テキスト・コードパッチ両方で違反検知に使う正規表現リスト。

scan(text) → 違反があれば最初の (pattern_name, matched_text) を返す。なければ None。
"""
import re


# 簡体字にしか存在しない字（日本語の常用漢字・繁體字と衝突しないもののみ）。
# 旧実装は re.compile(r"[简体中文]") という「文字クラス」だったため、
# 日本語文中の「体」「文」「中」単独出現（体調/文化/中身など）に誤爆し、
# HAL投稿（日本語+繁體字が同一テキスト）がほぼ毎回NG監査で握りつぶされていた。
#
# 除外した文字と理由（日本語の常用漢字・繁體字と衝突するため）:
#   体/来/会/国/学/与/没/件/那/后/还 は日本語の新字体または繁體字と同一・類似の
#   グリフで、通常の日本語文（体調/来週/会社/国内/学校 等）で高頻度に出現する。
_SIMPLIFIED_ONLY_CHARS = (
    "这说话时现们车门问间觉让对从"
    "为无义乐习书买卖验动过进"
    "华语实际经济发达网络设备软应该"
    "关讲给听识别见"
)
_SIMPLIFIED_ONLY_PATTERN = re.compile("[" + _SIMPLIFIED_ONLY_CHARS + "]")

# AI感の強い定型表現・締めフレーズ（社長のNGルール: 案1/案2やポイント解説、AIっぽい文体は不可）
_AI_BOILERPLATE_PATTERN = re.compile(
    r"(いかがでした(か|でしょうか)|以下の?(通り|点)|ポイントは(以下|次の)|"
    r"案[12１２]|オプション[12１２]|パターン[12１２]|"
    r"それでは(早速)?(ご)?紹介(します|いたします)|まとめると|結論として|"
    r"~という(わけ|こと)です|なので(すが)?今回は)"
)

# 絵文字クラスタ（3個以上連続）— AIっぽい絵文字の羅列を検知
_EMOJI_CLUSTER_PATTERN = re.compile(
    "[\U0001F300-\U0001FAFF☀-➿←-⇿⬀-⯿]{3,}"
)

KCS_NG_CONTENT_PATTERNS = [
    # 存在否定（HAL/すなくんの「中の人」露呈）
    ("ai_disclosure", re.compile(r"(AI|人工知能|ボット|bot|中の人|自動投稿|自動生成)", re.I)),
    # MIMOMI（タイアップ正式決定前）
    ("mimomi_premature", re.compile(r"MIMOMI", re.I)),
    # X本文への外部リンク直貼り（リプ誘導が必須）
    ("body_url_direct", re.compile(r"https?://(?!t\.co)[^\s]+")),
    # 簡体字混入（HALは繁體字必須）— 簡体字専用文字のみで判定
    ("simplified_chinese", _SIMPLIFIED_ONLY_PATTERN),
    # AIっぽい定型文・締めフレーズ
    ("ai_boilerplate_phrase", _AI_BOILERPLATE_PATTERN),
    # 絵文字クラスタ（3個以上連続）
    ("emoji_cluster", _EMOJI_CLUSTER_PATTERN),
    # 過剰煽り・スパム
    ("scam_keyword", re.compile(r"(必ず儲かる|絶対稼げる|誰でも月.+万|今すぐクリック)")),
    # 個人情報（03/06等の2桁市外局番も拾えるよう下限を1桁に修正）
    ("phone_number", re.compile(r"0\d{1,4}-\d{1,4}-\d{4}")),
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
