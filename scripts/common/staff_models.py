"""スタッフごとに使うAIモデルを1か所で決める。

社長の方針（2026-11-01）: **ジュン専務は全社の判断役なので、いちばん質の高いモデルを使う。**
他のスタッフは用途に合ったモデルでよい（投稿文の生成はGemini、審査や要約は従来のまま）。

ここを唯一の変更点にしてある。各スクリプトにモデル名を直書きすると、
「ジュンだけ上げたい」のような指示が来るたびに全ファイルを探し回ることになる。

将来、スプレッドシートの `カスタムスタッフ` タブに「AIモデル」列を足したら、
この関数がそこを読む形に差し替える。呼び出し側（`model_for("ジュン")`）は変えなくて済む。
"""
from .env_clean import clean_env

# 未指定のスタッフが使うモデル。
# 既存の挙動を変えないため、claude_client.call_claude の従来の既定値をそのまま置いている。
# 個別に上げ下げするときは STAFF_MODELS に1行足す（ここは触らない）。
DEFAULT_MODEL = "claude-opus-4-8"

STAFF_MODELS = {
    "ジュン": "claude-opus-5-5",  # 専務。朝礼の戦略指示・日次の明日の戦略・経営会議の結論
}

# 環境変数で差し替える場合のキー。日本語の環境変数名はWindows・Actions双方で扱いが怪しいので
# ローマ字のキーを明示する。コードを変えずに切り戻せるようにするための緊急用。
_ENV_KEYS = {
    "ジュン": "KCS_MODEL_JUN",
}


def model_for(staff_name: str) -> str:
    """スタッフ名からモデル名を返す。

    優先順位は 環境変数 ＞ STAFF_MODELS ＞ DEFAULT_MODEL。
    環境変数は `clean_env` を通す（GitHub Secrets経由だとBOMが混入して
    モデル名が一致せず404になるため。CLAUDE.md の鉄則1）。
    """
    env_key = _ENV_KEYS.get(staff_name)
    if env_key:
        override = clean_env(env_key)
        if override:
            return override
    return STAFF_MODELS.get(staff_name, DEFAULT_MODEL)
