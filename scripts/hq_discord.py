"""中枢アプリ（kcs-hq）の Discord 連携の共通部品。

【公開リポジトリの決まり】このリポジトリは公開で、GitHub Actions のログは誰でも読める。
- スタッフの名前・マネージャーの名前・取引先名・金額は、**コードにも、ログにも出さない**（Firestore と Discord にだけ置く）。
- ログに出すのは件数だけ。
- どのマネージャーがどのチャンネルかは、Firestore の knowledge/discord に置く（このリポジトリには置かない）。

鍵は新しく増やさない: Firestore は FIREBASE_SERVICE_ACCOUNT_JSON、Discord へは既存の BOT_WEBHOOK_URL（ボット経由）。
"""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from scripts.common.env_clean import clean_env  # noqa: E402

CATEGORY = "KCS 事業"  # カテゴリ名（チャンネルをまとめる箱）。事業の中身ではないので、ここに書いてよい
GENERAL = "general"  # 全体のチャンネルを指す、対応表のキー
GENERAL_NAME = "総合"
DOC = ("knowledge", "discord")  # 対応表の置き場（Firestore）


def firestore_client():
    """Firestore を開く。鍵が無ければ None。値は表示しない。"""
    raw = clean_env("FIREBASE_SERVICE_ACCOUNT_JSON")
    if not raw:
        return None
    import json

    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(json.loads(raw)))
    return firestore.client()


def bot_url():
    return clean_env("BOT_WEBHOOK_URL")


def post_to_bot(payload: dict, timeout: int = 60) -> dict:
    """ボット（Render）へ送る。応答の JSON を返す。届かなければ {"error": 種類}。"""
    url = bot_url()
    if not url:
        return {"error": "BOT_WEBHOOK_URL なし"}
    try:
        r = requests.post(url, json=payload, timeout=timeout)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}"}
        try:
            return r.json()
        except ValueError:
            return {"error": "応答が JSON ではない"}
    except Exception as e:
        return {"error": type(e).__name__}


def send_to_channel(channel_id: str, message: str) -> bool:
    """既存の notify（チャンネル指定の直送）で送る。ボットは受け付けるだけで結果は返さないので、届いた保証は無い。"""
    res = post_to_bot({"action": "notify", "channel_id": channel_id, "message": message})
    return "error" not in res


def manager_channel_names(staff_rows: list) -> dict:
    """名簿（ai_staff）のマネージャーから、{マネージャーの id: チャンネル名} を作る。全体用の総合も足す。"""
    out = {GENERAL: GENERAL_NAME}
    for m in staff_rows:
        if m.get("kind") == "manager" and m.get("id") and m.get("name"):
            out[m["id"]] = str(m["name"]).strip()
    return out


def channel_for(approval: dict, businesses: dict, channels: dict) -> str:
    """承認待ちを送るチャンネル。事業の担当マネージャーのチャンネル。無ければ総合。"""
    biz = businesses.get(approval.get("businessId")) or {}
    return channels.get(biz.get("managerId")) or channels.get(GENERAL) or ""
