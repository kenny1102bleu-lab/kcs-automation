"""AIの月の利用額を数える（社長の指示の実行用）。

方針（kcs-hq/departments/system/ai_execution_design.md・2026-11-20 謙一 OK）:
- 全体の Claude API は月15ドル。そのうち、社長の指示の実行（Claude と Gemini の合計）は **月5ドル** の枠。
- 80% で警告（1回だけ）、100% で止める。
- 使った額は Firestore の usage/YYYY-MM に、実行のたびに足す。ここは計算だけ（Firestore は触らない）。
"""
import datetime as dt
import os

JST = dt.timezone(dt.timedelta(hours=9))

DEFAULT_BUDGET_USD = 5.0
WARN_RATIO = 0.8

# 100万トークンあたりの単価（入力, 出力）ドル。Claude は公式の価格表（2026-10時点）。
# Gemini は推定値。使い始める前に公式の料金で確かめて直す。
# 考えるトークン（thinking）は出力として数える。知らないモデルは、いちばん高い単価で数える（少なく見積もらない）。
PRICES = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "gemini-2.5-flash": (0.30, 2.50),  # 推定。要確認
}
_FALLBACK_PRICE = (10.0, 50.0)


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    p_in, p_out = PRICES.get(model, _FALLBACK_PRICE)
    return (max(0, input_tokens) * p_in + max(0, output_tokens) * p_out) / 1_000_000


def month_key(now: dt.datetime) -> str:
    """月の区切りは日本の時刻（JST）で決める。"""
    return now.astimezone(JST).strftime("%Y-%m")


def budget_usd() -> float:
    """今月の枠（ドル）。環境変数 KCS_ORDER_BUDGET_USD で変えられる。読めない値は既定に戻す。"""
    raw = os.environ.get("KCS_ORDER_BUDGET_USD", "").strip()
    try:
        v = float(raw)
        return v if v > 0 else DEFAULT_BUDGET_USD
    except ValueError:
        return DEFAULT_BUDGET_USD


def level(spent: float, budget: float) -> str:
    """'ok' | 'warn'（80%以上） | 'stop'（100%以上）"""
    if budget <= 0 or spent >= budget:
        return "stop"
    if spent >= budget * WARN_RATIO:
        return "warn"
    return "ok"
