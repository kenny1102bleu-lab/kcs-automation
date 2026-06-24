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

from bot.pending_store import get_store

TOKEN = os.environ["DISCORD_BOT_TOKEN"]
GITHUB_TOKEN = os.environ["GH_PAT"]
GITHUB_REPO = os.environ["GITHUB_REPO"]  # 例: kenny1102/kcs-automation

# 承認待ち投稿は Gist に永続化（Bot再起動でも保持）
_store = get_store()


class _PendingDictAdapter:
    """既存コードの dict インターフェイスをstoreに委譲（最小改修）"""
    def pop(self, k, default=None):
        d = _store.pop(k)
        return d if d is not None else default

    def get(self, k, default=None):
        return _store.all().get(k, default)

    def __setitem__(self, k, v):
        _store.set(k, v.get("post_text", ""), v.get("account", ""),
                   v.get("media_path", ""), v.get("media_type", "none"))

    def __contains__(self, k):
        return k in _store.all()

    def __len__(self):
        return len(_store.all())

    def items(self):
        return _store.all().items()


pending_approvals = _PendingDictAdapter()

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
    print(f"KCS Bot 起動: {bot.user}", flush=True)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    content = message.content.strip()
    print(f"[on_message] from={message.author} content={content!r}", flush=True)

    APPROVE_PREFIXES = ("!承認", "!返信承認")
    REJECT_PREFIXES = ("!却下", "!返信スキップ")

    # ── 承認コマンド ──────────────────────────────
    if any(content.startswith(p) for p in APPROVE_PREFIXES):
        parts = content.split()
        if len(parts) < 2:
            await message.reply("使い方: `!承認 <承認ID>` または `!返信承認 <承認ID>`")
            return
        approval_id = parts[1]
        data = pending_approvals.pop(approval_id, None)
        if data is None:
            await message.reply(f"❌ 承認ID `{approval_id}` が見つかりません（期限切れか無効）")
            return
        # media_path に "run_id:filename" を入れている
        media_run_id, media_filename = "", ""
        mp = data.get("media_path", "") if isinstance(data, dict) else ""
        if mp and ":" in mp:
            media_run_id, media_filename = mp.split(":", 1)
        ok = trigger_workflow("post-approved", {
            "post_text": data["post_text"],
            "account": data["account"],
            "media_run_id": media_run_id,
            "media_filename": media_filename,
            "affiliate_link": "",
        })
        if ok:
            await message.reply(f"✅ `{data['account']}` の投稿を承認しました。X投稿を実行します。")
        else:
            await message.reply("⚠️ GitHub Actions の起動に失敗しました。")
        return

    if any(content.startswith(p) for p in REJECT_PREFIXES):
        parts = content.split()
        if len(parts) < 2:
            await message.reply("使い方: `!却下 <承認ID>` または `!返信スキップ <承認ID>`")
            return
        approval_id = parts[1]
        pending_approvals.pop(approval_id, None)
        await message.reply(f"❌ 投稿をキャンセルしました。次のスケジュールで新しい投稿案を生成します。")
        return

    # ── 手動起動コマンド ──────────────────────────
    if content in ("!朝礼", "!ブリーフィング"):
        trigger_workflow("discord-command", {"workflow": "morning_briefing"})
        await message.reply("🌅 朝礼ブリーフィングを起動しました。")
        return

    if content.startswith("!HAL"):
        theme = content.replace("!HAL", "").strip()
        trigger_workflow("discord-command", {"workflow": "hal_post", "theme": theme})
        await message.reply(f"🎭 HAL投稿フローを起動しました。{'テーマ: ' + theme if theme else ''}")
        return

    if content.startswith("!すなくん"):
        theme = content.replace("!すなくん", "").strip()
        trigger_workflow("discord-command", {"workflow": "sunakun_post", "theme": theme})
        await message.reply(f"🛒 すなくん投稿フローを起動しました。{'テーマ: ' + theme if theme else ''}")
        return

    if content == "!レポート":
        trigger_workflow("discord-command", {"workflow": "daily_report"})
        await message.reply("📊 日次レポートを起動しました。")
        return

    if content == "!状況":
        lines = [f"📋 **進行中タスク** ({len(pending_approvals)}件)"]
        if not pending_approvals:
            lines.append("（承認待ちなし）")
        else:
            for aid, d in list(pending_approvals.items())[:20]:
                preview = d["post_text"][:40].replace("\n", " ")
                lines.append(f"• `{aid}` [{d['account']}] {preview}…")
        await message.reply("\n".join(lines))
        return

    if content.startswith("!approve_patch"):
        parts = content.split()
        if len(parts) < 2:
            await message.reply("使い方: `!approve_patch <PR番号>`")
            return
        pr_num = parts[1]
        try:
            r = requests.put(
                f"https://api.github.com/repos/{GITHUB_REPO}/pulls/{pr_num}/merge",
                headers={"Authorization": f"token {GITHUB_TOKEN}",
                         "Accept": "application/vnd.github.v3+json"},
                json={"merge_method": "squash"},
            )
            if r.status_code == 200:
                await message.reply(f"✅ PR #{pr_num} をマージしました。Renderが自動デプロイします。")
            else:
                await message.reply(f"⚠️ マージ失敗: HTTP {r.status_code}\n{r.text[:200]}")
        except Exception as e:
            await message.reply(f"⚠️ マージ失敗: {e}")
        return

    if content == "!在庫":
        await message.reply("📦 Pizza在庫: ストックなし（手動補充してください）")
        return

    if content in ("!ヘルプ", "!help"):
        await message.reply(
            "**KCS Bot コマンド一覧**\n"
            "`!朝礼` / `!ブリーフィング` — 朝礼フロー手動起動\n"
            "`!HAL [テーマ]` — HAL投稿生成\n"
            "`!すなくん [テーマ]` — すなくん投稿生成\n"
            "`!レポート` — 日次レポート手動起動\n"
            "`!承認 <ID>` / `!返信承認 <ID>` — X投稿を承認して投稿実行\n"
            "`!却下 <ID>` / `!返信スキップ <ID>` — X投稿をキャンセル\n"
            "`!状況` — 承認待ち一覧表示\n"
            "`!在庫` — Pizza在庫確認\n"
        )
        return


def register_pending(approval_id: str, post_text: str, account: str,
                     media_filename: str = "", media_run_id: str = "",
                     media_type: str = "none"):
    """外部から承認待ちを登録する。Gist永続化＋30分TTL（store側で管理）。"""
    # media_path フィールドに media_filename を入れて再利用
    extra_path = f"{media_run_id}:{media_filename}" if media_filename else ""
    _store.set(approval_id, post_text, account, extra_path, media_type, ttl_sec=1800)
