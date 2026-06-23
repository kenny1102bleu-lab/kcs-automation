import os
import requests

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

def notify(message: str, channel_webhook: str = None) -> None:
    url = channel_webhook or WEBHOOK_URL
    requests.post(url, json={"content": message})

def notify_post_preview(post_text: str, account: str, workflow_id: str) -> None:
    """X投稿プレビューをDiscordに送信。社長がBotコマンドで承認する。"""
    message = (
        f"📝 **{account} 投稿プレビュー**\n"
        f"```\n{post_text}\n```\n"
        f"承認する場合: `!承認 {workflow_id}`\n"
        f"却下する場合: `!却下 {workflow_id}`\n"
        f"⏱️ 30分以内に返答がない場合は自動キャンセルされます。"
    )
    notify(message)
