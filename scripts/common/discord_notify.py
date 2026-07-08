"""
Discord 通知ユーティリティ。
DISCORD_WEBHOOK_URLS が JSON で複数チャンネル定義されている場合はチャンネル別に送信。
fallback で DISCORD_WEBHOOK_URL (単一) を使う。
"""
import base64
import json
import os
import pathlib
import requests


def _clean_env(name: str) -> str:
    """環境変数からBOMとゼロ幅スペース等の不可視文字を除去。
    Windows経由で GitHub Secrets に貼った値に BOM(﻿) が混入すると requests が
    InvalidSchema: No connection adapters for '﻿https://...' を投げて落ちるため。
    """
    val = os.environ.get(name, "")
    return val.replace('﻿', '').replace('​', '').replace('‌', '').replace('‍', '').strip()


def _clean_url(url: str) -> str:
    """URL文字列からBOM等の不可視文字を除去（WEBHOOKS dict の値にも適用するため）"""
    if not isinstance(url, str):
        return ""
    return url.replace('﻿', '').replace('​', '').replace('‌', '').replace('‍', '').strip()


_RAW_URLS = _clean_env("DISCORD_WEBHOOK_URLS")
try:
    WEBHOOKS = json.loads(_RAW_URLS) if _RAW_URLS else {}
    # 各 webhook URL も念のため BOM 除去
    WEBHOOKS = {k: _clean_url(v) for k, v in WEBHOOKS.items()}
except Exception:
    WEBHOOKS = {}

DEFAULT_WEBHOOK = _clean_env("DISCORD_WEBHOOK_URL")
BOT_WEBHOOK_URL = _clean_env("BOT_WEBHOOK_URL")

# 2026-07-08: HAL/すなくんの投稿プレビュー〜完了/失敗/セルフリプライ通知を
# 専用チャンネルへ分離。新規Incoming Webhookは発行せず、既に全チャンネルへの
# 送信権限を持つBot（bot/discord_bot.py）にchannel_id直送させることで実現。
ACCOUNT_CHANNEL_IDS = {
    "SUNAKUN": "1501098255130427432",
    "HAL": "1494812858830032988",
}


def _resolve(channel: str | None) -> str:
    if channel and channel in WEBHOOKS:
        return WEBHOOKS[channel]
    return DEFAULT_WEBHOOK


def notify_account(message: str, account: str, file_path: str | None = None) -> None:
    """HAL/すなくん専用チャンネルへ、Bot経由（channel_id直送）で送信する。
    対象アカウントでない場合やBot未設定の場合は従来のIncoming Webhookにフォールバック。"""
    channel_id = ACCOUNT_CHANNEL_IDS.get((account or "").upper())
    p = pathlib.Path(file_path) if file_path else None

    if not channel_id or not BOT_WEBHOOK_URL:
        if p and p.exists():
            notify_with_file(message, file_path)
        else:
            notify(message)
        return

    payload = {"action": "notify", "channel_id": channel_id, "message": message}
    if p and p.exists():
        payload["image_b64"] = base64.b64encode(p.read_bytes()).decode("ascii")
        payload["image_filename"] = p.name

    try:
        requests.post(BOT_WEBHOOK_URL, json=payload, timeout=60)
    except Exception:
        # Bot側がRender無料枠のスリープ等で届かない場合も、投稿フロー自体は止めない
        if p and p.exists():
            notify_with_file(message, file_path)
        else:
            notify(message)


def notify(message: str, channel: str | None = None, channel_webhook: str | None = None) -> None:
    """テキスト通知。channel='error-log' などで分離可能。"""
    url = _clean_url(channel_webhook) if channel_webhook else _resolve(channel)
    if not url:
        return
    requests.post(url, json={"content": message})


def notify_with_file(message: str, file_path: str, channel: str | None = None) -> None:
    """メディア添付通知（画像/動画プレビュー用）。"""
    url = _resolve(channel)
    if not url:
        return
    p = pathlib.Path(file_path)
    if not p.exists():
        notify(message, channel)
        return
    with p.open("rb") as f:
        requests.post(url, data={"payload_json": json.dumps({"content": message})},
                      files={"file": (p.name, f)})


def notify_post_preview(post_text: str, account: str, workflow_id: str,
                        media_info: dict | None = None, qa_summary: str | None = None,
                        account_key: str | None = None) -> None:
    """X投稿プレビュー。media_infoがあれば添付して送信。
    qa_summary: ここまでの自動チェックが何を確認済みかを一覧表示し、
    社長の目視確認をトーン/事実確認だけに絞れるようにする。"""
    media_line = ""
    media_path = ""
    if media_info:
        mtype = media_info.get("type", "none")
        media_path = media_info.get("path", "")
        if mtype != "none":
            media_line = f"🎬 メディア: {mtype}" + (
                f" (ミオ評価 {media_info.get('mio_score','?')}/100)" if media_info.get("mio_score") else ""
            ) + "\n"

    qa_block = f"{qa_summary}\n" if qa_summary else ""

    message = (
        f"📝 **{account} 投稿プレビュー**\n"
        f"```\n{post_text}\n```\n"
        f"{qa_block}"
        f"{media_line}"
        f"承認: `!承認 {workflow_id}` （または `!返信承認 {workflow_id}`）\n"
        f"却下: `!却下 {workflow_id}` （または `!返信スキップ {workflow_id}`）\n"
        f"⏱️ 30分以内に返答がない場合は自動キャンセルされます。"
    )
    has_media = bool(media_path and pathlib.Path(media_path).exists())
    if account_key and account_key.upper() in ACCOUNT_CHANNEL_IDS:
        notify_account(message, account_key, media_path if has_media else None)
    elif has_media:
        notify_with_file(message, media_path, channel="pending-approval")
    else:
        notify(message, channel="pending-approval")
