"""
KCS Discord Bot
- 社長のコマンドを受け取り GitHub Actions を起動する
- 承認コマンド (!承認 / !却下) でX投稿を制御する
- Render.com の無料枠でホスティング
"""
import os
import json
import asyncio
import discord
import requests

TOKEN = os.environ["DISCORD_BOT_TOKEN"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ["GITHUB_REPO"]  # 例: kenny1102/kcs-automation

# 承認待ちの投稿を一時保存 {approval_id: {post_text, account, expires_at}}
pending_approvals: dict = {}

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)


def trigger_workflow(event_type: str, payload: dict) -> bool:
    resp = requests.post(
        f"https://api.github.com/repos/{GITHUB_REPO}/dispatches",
        headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        },
        json={"event_type": event_type, "client_payload": payload},
    )
    return resp.status_code == 204


@bot.event
async def on_ready():
    print(f"KCS Bot 起動: {bot.user}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    content = message.content.strip()

    # ── 承認コマンド ──────────────────────────────
    if content.startswith("!承認"):
        parts = content.split()
        if len(parts) < 2:
            await message.reply("使い方: `!承認 <承認ID>`")
            return
        approval_id = parts[1]
        data = pending_approvals.pop(approval_id, None)
        if data is None:
            await message.reply(f"❌ 承認ID `{approval_id}` が見つかりません（期限切れか無効）")
            return
        ok = trigger_workflow("post-approved", {
            "post_text": data["post_text"],
            "account": data["account"],
        })
        if ok:
            await message.reply(f"✅ `{data['account']}` の投稿を承認しました。X投稿を実行します。")
        else:
            await message.reply("⚠️ GitHub Actions の起動に失敗しました。")
        return

    if content.startswith("!却下"):
        parts = content.split()
        if len(parts) < 2:
            await message.reply("使い方: `!却下 <承認ID>`")
            return
        approval_id = parts[1]
        pending_approvals.pop(approval_id, None)
        await message.reply(f"❌ 投稿をキャンセルしました。次のスケジュールで新しい投稿案を生成します。")
        return

    # ── 手動起動コマンド ──────────────────────────
    if content == "!朝礼":
        trigger_workflow("discord-command", {"workflow": "morning_briefing"})
        await message.reply("🌅 朝礼フローを起動しました。")

    elif content.startswith("!HAL"):
        theme = content.replace("!HAL", "").strip()
        trigger_workflow("discord-command", {"workflow": "hal_post", "theme": theme})
        await message.reply(f"🎭 HAL投稿フローを起動しました。{'テーマ: ' + theme if theme else ''}")

    elif content.startswith("!すなくん"):
        theme = content.replace("!すなくん", "").strip()
        trigger_workflow("discord-command", {"workflow": "sunakun_post", "theme": theme})
        await message.reply(f"🛒 すなくん投稿フローを起動しました。{'テーマ: ' + theme if theme else ''}")

    elif content == "!レポート":
        trigger_workflow("discord-command", {"workflow": "daily_report"})
        await message.reply("📊 日次レポートを起動しました。")

    elif content == "!ヘルプ":
        await message.reply(
            "**KCS Bot コマンド一覧**\n"
            "`!朝礼` — 朝礼フロー手動起動\n"
            "`!HAL [テーマ]` — HAL投稿生成\n"
            "`!すなくん [テーマ]` — すなくん投稿生成\n"
            "`!レポート` — 日次レポート手動起動\n"
            "`!承認 <ID>` — X投稿を承認して投稿実行\n"
            "`!却下 <ID>` — X投稿をキャンセル\n"
        )


def register_pending(approval_id: str, post_text: str, account: str):
    """外部から承認待ちを登録する（Webhookエンドポイント等から呼び出し可能）"""
    pending_approvals[approval_id] = {
        "post_text": post_text,
        "account": account,
    }
    # 30分後に自動削除
    async def expire():
        await asyncio.sleep(1800)
        pending_approvals.pop(approval_id, None)
    asyncio.create_task(expire())


bot.run(TOKEN)
