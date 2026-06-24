"""
GAS の Pending_News シートからポジティブニュース候補を取得する。

GAS_NEWS_API_URL: GASウェブアプリのURL（GitHub Secretsで設定）
取得失敗・候補ゼロ時は None を返す → 呼び出し側で従来テーマ（Yahoo 1d10 や手動テーマ）にフォールバック。
"""
import os
import requests


def fetch_theme(account: str, timeout: int = 10) -> dict | None:
    url = os.environ.get("GAS_NEWS_API_URL")
    if not url:
        return None
    try:
        r = requests.get(url, params={"account": account.lower()}, timeout=timeout)
        if r.status_code != 200:
            return None
        data = r.json()
        if not data.get("angle"):
            return None
        return {
            "id": data.get("id"),
            "title": data.get("title"),
            "url": data.get("url"),
            "angle": data["angle"],
        }
    except Exception:
        return None


def format_theme_prompt(theme: dict | None, fallback: str) -> str:
    if theme is None:
        return fallback
    return (
        f"本日のテーマ: {theme['angle']}\n"
        f"参考ニュース: {theme['title']}\n"
        f"（このニュースを直接引用せず、切り口だけ借りて投稿を作成してください）"
    )
