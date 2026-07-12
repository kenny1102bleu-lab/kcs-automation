"""
KCS_NG_CONTENT_PATTERNS - 投稿テキスト・コードパッチ両方で違反検知に使う正規表現リスト。

scan(text) → 違反があれば最初の (pattern_name, matched_text) を返す。なければ None。
"""
import re


# 繁体字にしか存在しない字（日本語の常用漢字と衝突しないもののみ）。
# HALは簡体字必須・繁体字NGが正しいルール（社長訂正、2026-07-12。以前は
# 逆に「繁體字必須・簡体字NG」として実装されていたが誤りだった。台湾人の父・
# 17LIVE等のキャラクター設定はそのままで、使用する中国語の文字種のみが逆）。
#
# 繁体字と簡体字は同一の発音・語彙で文字の形だけが異なるため機械的な変換差分
# （例: OpenCCのt2s変換）で判定しようとすると、繁体字と共通の字形を持つ日本語
# 漢字（語/調/愛/極/盤/衛/陰/陽/劇等）まで大量に誤検知することを実地確認済み。
# そのため簡体字判定と同じ手法（日本語と衝突しない繁体字専用字のみを列挙）を
# 踏襲する。各字はOpenCCで簡体字への変換が発生すること（＝繁体字専用字である
# こと）を確認済み。
#
# 除外した文字と理由（日本語の常用漢字と同一グリフのため誤爆を避ける）:
#   極/盤/衛/陰/陽/劇/隻 は日本語でも同一字形で高頻度に使われる
#   （積極的/基盤/防衛/陰陽/太陽/演劇/一隻 等）。龍は人名・地名で使われるため除外。
_TRADITIONAL_ONLY_CHARS = (
    "這說讓對從樂賣驗實經濟發應關聽"
    "麼藝藥歡灣蘋雞醫壓歲盡屬點禮"
)
_TRADITIONAL_ONLY_PATTERN = re.compile("[" + _TRADITIONAL_ONLY_CHARS + "]")

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
    # 繁体字混入（HALは簡体字必須）— 繁体字専用文字のみで判定
    ("traditional_chinese", _TRADITIONAL_ONLY_PATTERN),
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
