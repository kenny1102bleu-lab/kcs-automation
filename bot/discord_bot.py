"""
KCS Discord Bot
- 社長のコマンドを受け取り GitHub Actions を起動する
- 承認コマンド (!承認 / !却下) でX投稿を制御する
- クラウドワークスの新着通知にボタンを付ける（下書きを出す / 応募した / 見送る）
  ※ クラウドワークスへの送信は一切しない。応募は社長が手で行う
- Render.com の無料枠でホスティング
"""
import io
import re
import json
import base64
import asyncio
import uuid
import discord
import requests

from bot.pending_store import get_store
from scripts.common.env_clean import clean_env
from scripts.common.product_source import scrape_amazon_product


def _required_env(name: str) -> str:
    """必須の環境変数。clean_env を通してから、空なら起動時に落とす。

    CLAUDE.md §1: Windows経由でSecretsに貼るとBOM(﻿)やゼロ幅文字が混入し、
    Discord Webhook の InvalidSchema や Twitter API の
    「215 - Bad Authentication data」のような分かりにくいエラーになる。
    値を目で見ても気づけないので、必ず clean_env を経由する。
    """
    val = clean_env(name)
    if not val:
        raise RuntimeError(f"環境変数 {name} が設定されていません（Renderの環境変数を確認してください）")
    return val


TOKEN = _required_env("DISCORD_BOT_TOKEN")
GITHUB_TOKEN = _required_env("GH_PAT")
GITHUB_REPO = _required_env("GITHUB_REPO")  # 例: kenny1102bleu-lab/kcs-automation

# クラウドワークス案件監視GASの承認Webhook。未設定でもBot全体は起動できるようにする
# （os.environ[...]の必須方式にすると設定漏れだけでBot全体が起動不能になるため）
CW_APPROVAL_WEBAPP_URL = clean_env("CW_APPROVAL_WEBAPP_URL")
CW_APPROVAL_SECRET = clean_env("CW_APPROVAL_SECRET")

# 記録を書き換えるボタン・コマンドを押せる人（社長のDiscordユーザーID）。
# 未設定でも動くが、その場合は誰でも押せるので、返信でそう伝える。
OWNER_DISCORD_USER_ID = clean_env("OWNER_DISCORD_USER_ID")

# 応募文の下書きを出すチャンネル（#承認待ち）。未設定ならボタンを押したチャンネルに出す。
CW_DRAFT_CHANNEL_ID = clean_env("CW_DRAFT_CHANNEL_ID")

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
                   v.get("media_path", ""), v.get("media_type", "none"),
                   affiliate_link=v.get("affiliate_link", ""))

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


AMAZON_QUEUE_PATH = "data/amazon_product_queue.json"


def add_to_amazon_queue(url: str) -> str:
    """社長がDiscordで貼ったAmazon商品URLをGitHub Contents API経由でキューに
    追加する（Amazon PA-API未承認のため、楽天のような自動選定ができない間の
    手動キュー方式）。ワークフロー実行を待たず即時反映される。"""
    api = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{AMAZON_QUEUE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        r = requests.get(api, headers=headers, timeout=10)
        if r.status_code == 200:
            file_data = r.json()
            queue = json.loads(base64.b64decode(file_data["content"]).decode("utf-8"))
            sha = file_data["sha"]
        elif r.status_code == 404:
            queue, sha = [], None
        else:
            return f"⚠️ キュー読み込み失敗: HTTP {r.status_code}"
    except Exception as e:
        return f"⚠️ キュー読み込み失敗: {e}"

    queue.append(url)
    body = {
        "message": "chore: add amazon product to queue (via Discord)",
        "content": base64.b64encode(json.dumps(queue, ensure_ascii=False, indent=2).encode("utf-8")).decode("ascii"),
    }
    if sha:
        body["sha"] = sha

    try:
        r2 = requests.put(api, headers=headers, json=body, timeout=10)
        if r2.status_code in (200, 201):
            return f"✅ Amazonキューに追加しました（現在{len(queue)}件待ち）\n{url[:150]}"
        return f"⚠️ キュー追加失敗: HTTP {r2.status_code} {r2.text[:200]}"
    except Exception as e:
        return f"⚠️ キュー追加失敗: {e}"


DISCORD_API = "https://discord.com/api/v10"

# 監視GASの新着通知から job_id を拾う。案件URLは必ず入っているので、ここを目印にする
CW_JOB_URL_RE = re.compile(r"crowdworks\.jp/public/jobs/(\d+)")

# GAS・ネットワークのエラーを、社長が次に何をすればいいか分かる言葉に直す
CW_ERROR_HELP = {
    "not_configured":
        "⚠️ CW承認機能が未設定です。Render の環境変数 `CW_APPROVAL_WEBAPP_URL` と "
        "`CW_APPROVAL_SECRET` を確認してください（値は貼るだけ。こちらでは見ません）。",
    "unauthorized":
        "⚠️ 認証エラーです。GAS「設定」シートの `CW_APPROVAL_SECRET` と Render の値が合っていません。"
        "見た目が同じでも、貼り付け時に見えない文字が混ざっていると一致しません。両方を貼り直してください。",
    "bad_url":
        "⚠️ `CW_APPROVAL_WEBAPP_URL` の形がおかしいです（見えない文字が混ざっている可能性）。貼り直してください。",
    "not_json":
        "⚠️ GAS Web App が JSON を返していません。デプロイの「アクセスできるユーザー」が"
        "**全員**になっているか確認してください（Googleログイン画面に飛ばされています）。",
    "sheet_not_found": "⚠️ GAS側に「通知済み」シートがありません。`setupCrowdworksMonitor()` を1回実行してください。",
    "locked_try_again": "⚠️ ほかの処理と重なりました。少し待ってもう一度押してください。",
    "missing_job_id": "⚠️ job_id が空です。",
    "bad_ref": "⚠️ 案件URLか job_id（数字）を指定してください。",
    "unknown_action": "⚠️ GAS側が古いです。`clasp push` して新しいデプロイを出し直してください。",
    "internal_error": "⚠️ GAS側でエラーになりました。Apps Script の実行ログを確認してください。",
    "already_skipped": "⚠️ この案件は見送り済みです。",
}


def cw_error_message(err: str, job_id: str = "") -> str:
    """GASの戻りのerrorを、社長向けの文にする。"""
    if err == "not_found":
        return (
            f"❌ job_id `{job_id}` が監視シートにありません。\n"
            "通知メッセージのIDを確認してください。監視に乗っていない案件を手で応募した場合は "
            "`!CW応募済み <案件URL>` で記録できます。"
        )
    if err.startswith("http_"):
        code = err[5:]
        if code in ("301", "302", "401", "403"):
            return (
                f"⚠️ GAS Web App が {code} を返しました。デプロイの「アクセスできるユーザー」が"
                "**全員**になっていません。新しいデプロイを出し直してください。"
            )
        return f"⚠️ GAS Web App が HTTP {code} を返しました。デプロイを新しいバージョンで出し直してください。"
    return CW_ERROR_HELP.get(err, f"⚠️ 処理に失敗しました（{err}）。")


def cw_call(action: str, payload: dict, timeout: int = 25) -> dict:
    """クラウドワークス案件監視GASのWeb App(doPost)を叩く。

    ここでもクラウドワークスへの送信は一切しない。やるのはスプレッドシートの
    読み書きだけ（自動応募は規約と営業部マニュアルで禁止）。
    """
    if not CW_APPROVAL_WEBAPP_URL or not CW_APPROVAL_SECRET:
        return {"ok": False, "error": "not_configured"}
    body = {"secret": CW_APPROVAL_SECRET, "action": action}
    body.update(payload)
    try:
        r = requests.post(CW_APPROVAL_WEBAPP_URL, json=body, timeout=timeout)
        if r.status_code != 200:
            return {"ok": False, "error": f"http_{r.status_code}"}
        return r.json()
    except requests.exceptions.InvalidSchema:
        # URLにBOM・ゼロ幅文字が残っていると、requestsはスキーマ不明としてここで落ちる
        return {"ok": False, "error": "bad_url"}
    except ValueError:
        return {"ok": False, "error": "not_json"}
    except Exception as e:
        # 例外のstr()にはURLが入ることがあるため、種類だけを返す
        return {"ok": False, "error": type(e).__name__}


def cw_approve_job(job_id: str) -> dict:
    """「通知済み」シートの応募済み(I列)・応募日時(J列)を埋める。応募はしない。"""
    return cw_call("approve", {"job_id": job_id})


def cw_get_draft(job_id: str) -> dict:
    """「通知済み」シートの提案下書き(H列)を読む。"""
    return cw_call("draft", {"job_id": job_id})


def cw_skip_job(job_id: str) -> dict:
    """見送り(K列)を立てる。"""
    return cw_call("skip", {"job_id": job_id})


def cw_record_applied(ref: str, memo: str = "") -> dict:
    """監視に乗っていない案件を「通知済み」シートに記録する（行が無ければ追加）。"""
    return cw_call("record", {"ref": ref, "memo": memo})


def is_owner(user_id) -> bool:
    """記録を書き換える操作を押せるのは社長だけ。未設定のときは通す（運用を止めないため）。"""
    if not OWNER_DISCORD_USER_ID:
        return True
    return str(user_id) == OWNER_DISCORD_USER_ID


OWNER_UNSET_NOTE = (
    "\n\n— `OWNER_DISCORD_USER_ID` が未設定のため、いまは誰でも押せます。"
    "社長のDiscordユーザーIDをRenderに入れると、社長だけに絞れます。"
)


def cw_button_row(job_id: str) -> list:
    """新着案件に付けるボタン。custom_id に job_id を入れるので、IDを目で写さなくてよい。"""
    return [{
        "type": 1,
        "components": [
            {"type": 2, "style": 1, "label": "下書きを出す", "custom_id": f"cw:draft:{job_id}"},
            {"type": 2, "style": 3, "label": "応募した", "custom_id": f"cw:applied:{job_id}"},
            {"type": 2, "style": 2, "label": "見送る", "custom_id": f"cw:skip:{job_id}"},
        ],
    }]


def _post_message(channel_id: str, payload: dict) -> dict:
    """チャンネルへ直接POSTする。discord.pyのsend()では生のcomponentsを送れないため。"""
    try:
        r = requests.post(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        if r.status_code not in (200, 201):
            print(f"[cw] message post failed: HTTP {r.status_code} {r.text[:200]}", flush=True)
            return {}
        return r.json()
    except Exception as e:
        print(f"[cw] message post failed: {type(e).__name__}", flush=True)
        return {}


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
    if not OWNER_DISCORD_USER_ID:
        print("[cw] OWNER_DISCORD_USER_ID が未設定。CWのボタンは誰でも押せる状態です", flush=True)


async def _attach_cw_buttons(message: discord.Message, job_id: str):
    """監視GASの新着通知（Incoming Webhook）にボタンを付ける。

    Webhookが出したメッセージ自体にボタンを後付けすることはできない（押しても
    受け取るアプリが居ない）ので、Botが直後に返信としてボタンだけを出す。
    こうすると監視GAS側の設定を一切増やさずにボタンが使える。
    """
    res = await asyncio.to_thread(_post_message, str(message.channel.id), {
        "content": f"案件 `{job_id}` — どうしますか？（押しても**クラウドワークスには送りません**）",
        "components": cw_button_row(job_id),
        "message_reference": {"message_id": str(message.id), "fail_if_not_exists": False},
        "allowed_mentions": {"parse": []},
    })
    if res:
        print(f"[cw] attached buttons job_id={job_id}", flush=True)


@bot.event
async def on_interaction(interaction: discord.Interaction):
    """ボタン押下。GAS呼び出しは数秒かかるので、必ず先に defer する（Discordの3秒制限）。"""
    if interaction.type is not discord.InteractionType.component:
        return
    custom_id = (interaction.data or {}).get("custom_id", "")
    if not custom_id.startswith("cw:"):
        return

    parts = custom_id.split(":", 2)
    if len(parts) != 3:
        return
    _, action, job_id = parts

    if not is_owner(interaction.user.id):
        await interaction.response.send_message(
            "このボタンは社長だけが押せます。", ephemeral=True)
        return

    try:
        await interaction.response.defer(ephemeral=True, thinking=True)
    except discord.HTTPException:
        return  # 3秒を過ぎた・二重押しなど。ここで諦める

    note = "" if OWNER_DISCORD_USER_ID else OWNER_UNSET_NOTE

    if action == "draft":
        result = await asyncio.to_thread(cw_get_draft, job_id)
        if not result.get("ok"):
            await interaction.followup.send(
                cw_error_message(result.get("error", "unknown"), job_id), ephemeral=True)
            return
        draft = (result.get("draft") or "").strip()
        title = result.get("title", "")
        url = result.get("url", "")
        if not draft:
            await interaction.followup.send(
                f"下書きがまだありません（**{title}**）。\n"
                "監視GASの「設定」シートに `GEMINI_API_KEY` が無いか、初回実行で生成を飛ばした案件です。\n"
                f"{url}", ephemeral=True)
            return
        body = (
            f"✍️ **応募文の下書き**（案件 `{job_id}`）\n"
            f"**{title}**\n{url}\n\n{draft}\n\n"
            "— このままでは送られていません。**内容を案件に合わせて直してから、"
            "クラウドワークスの応募欄に貼って送ってください。**\n"
            "送ったら下の「応募した」を押してください（記録だけ付けます）。"
        )
        target = CW_DRAFT_CHANNEL_ID or str(interaction.channel_id)
        posted = await asyncio.to_thread(_post_message, target, {
            "content": body,
            "components": cw_button_row(job_id),
            "allowed_mentions": {"parse": []},
        })
        if posted:
            where = "#承認待ち" if CW_DRAFT_CHANNEL_ID else "このチャンネル"
            await interaction.followup.send(f"下書きを{where}に出しました。{note}", ephemeral=True)
        else:
            await interaction.followup.send(
                "⚠️ 下書きを出せませんでした（Botのチャンネル権限、または "
                "`CW_DRAFT_CHANNEL_ID` の値を確認してください）。", ephemeral=True)
        return

    if action == "skip":
        result = await asyncio.to_thread(cw_skip_job, job_id)
        if not result.get("ok"):
            await interaction.followup.send(
                cw_error_message(result.get("error", "unknown"), job_id), ephemeral=True)
            return
        await interaction.followup.send(
            f"見送りにしました（**{result.get('title', '')}**）。{note}", ephemeral=True)
        return

    if action == "applied":
        result = await asyncio.to_thread(cw_approve_job, job_id)
        if not result.get("ok"):
            await interaction.followup.send(
                cw_error_message(result.get("error", "unknown"), job_id), ephemeral=True)
            return
        if result.get("already_applied"):
            await interaction.followup.send(
                f"すでに応募済みで記録されています（{result.get('applied_at', '')}）\n"
                f"**{result.get('title', '')}**", ephemeral=True)
            return
        await interaction.followup.send(
            f"✅ 応募済みとして記録しました（{result.get('applied_at', '')}）\n"
            f"**{result.get('title', '')}**\n名義: 個人 / 事業: サイト経由{note}", ephemeral=True)
        return


@bot.event
async def on_message(message: discord.Message):
    # 監視GASの新着通知にボタンを付ける（Webhookが出すメッセージなので author.bot は True）
    if message.webhook_id and "新着案件" in message.content:
        found = CW_JOB_URL_RE.search(message.content)
        if found:
            await _attach_cw_buttons(message, found.group(1))
        return

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

        if isinstance(data, dict) and data.get("kind") == "amazon_product":
            await message.reply(add_to_amazon_queue(data["url"]))
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
            "affiliate_link": data.get("affiliate_link", "") if isinstance(data, dict) else "",
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
        data = pending_approvals.pop(approval_id, None)
        if isinstance(data, dict) and data.get("kind") == "amazon_product":
            await message.reply("❌ 商品案を却下しました。キューには追加されません。")
            return
        await message.reply("❌ 投稿をキャンセルしました。次のスケジュールで新しい投稿案を生成します。")
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

    if content.startswith("!すなくんAmazon"):
        url = content.replace("!すなくんAmazon", "", 1).strip()
        if not url or "amazon" not in url.lower():
            await message.reply(
                "使い方: `!すなくんAmazon <Amazon商品URL>`\n"
                "例: `!すなくんAmazon https://www.amazon.co.jp/dp/B0XXXXXXX`"
            )
            return
        await message.reply("🔍 商品情報を取得中…")
        product = scrape_amazon_product(url)
        if not product:
            await message.reply(
                "⚠️ 商品情報の取得に失敗しました"
                "（ページ取得失敗・AMAZON_ASSOCIATE_TAG未設定・Bot対策の可能性）。\n"
                "URLを確認するか、時間を置いて再度お試しください。"
            )
            return
        approval_id = str(uuid.uuid4())[:8]
        _store.set_product_proposal(approval_id, url=url, title=product["title"],
                                    affiliate_url=product["affiliate_url"])
        await message.reply(
            "🛒 **Amazon商品プレビュー**\n"
            f"商品名: {product['title']}\n"
            f"アフィリエイトURL: {product['affiliate_url']}\n\n"
            f"承認（キュー追加）: `!承認 {approval_id}`\n"
            f"却下（破棄）: `!却下 {approval_id}`\n"
            "⏱️ 30分以内に返答がない場合は自動キャンセルされます。"
        )
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
                if d.get("kind") == "amazon_product":
                    lines.append(f"• `{aid}` [Amazon商品案] {d.get('title', '')[:40]}…")
                    continue
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

    if content.startswith("!CW応募済み"):
        # 監視に乗っていない案件を手で応募したときの記録口。
        # 例: 13622691（myfans動画切り抜き）は監視シートに無いので !CW承認 では見つからない。
        if not is_owner(message.author.id):
            await message.reply("このコマンドは社長だけが使えます。")
            return
        rest = content.replace("!CW応募済み", "", 1).strip()
        if not rest:
            await message.reply(
                "使い方: `!CW応募済み <案件URL または job_id> [メモ]`\n"
                "例: `!CW応募済み https://crowdworks.jp/public/jobs/13622691 相談ID 315263497`"
            )
            return
        parts = rest.split(maxsplit=1)
        ref = parts[0]
        memo = parts[1] if len(parts) > 1 else ""
        result = cw_record_applied(ref, memo)
        if not result.get("ok"):
            await message.reply(cw_error_message(result.get("error", "unknown"), ref))
            return
        if result.get("already_applied"):
            await message.reply(
                f"⚠️ この案件は既に応募済みで記録されています（{result.get('applied_at', '')}）\n"
                f"{result.get('title', '')}"
            )
            return
        made = "新しい行を作って記録しました" if result.get("created") else "既にある行に記録しました"
        await message.reply(
            f"✅ {made}（{result.get('applied_at', '')}）\n"
            f"案件 `{result.get('job_id', '')}` {result.get('title', '')}\n"
            f"{result.get('url', '')}\n"
            "名義: 個人 / 事業: サイト経由（追記85: CW経由は全部個人）"
        )
        return

    if content.startswith("!CW承認"):
        parts = content.split()
        if len(parts) < 2:
            await message.reply("使い方: `!CW承認 <job_id>`")
            return
        if not is_owner(message.author.id):
            await message.reply("このコマンドは社長だけが使えます。")
            return
        job_id = parts[1]
        result = cw_approve_job(job_id)

        if not result.get("ok"):
            await message.reply(cw_error_message(result.get("error", "unknown"), job_id))
            return

        if result.get("already_applied"):
            await message.reply(
                f"⚠️ この案件は既に承認/応募済みです（{result.get('applied_at', '')}）\n"
                f"{result.get('title', '')}"
            )
            return

        await message.reply(
            "✅ 記録しました。**この操作ではクラウドワークスに送られていません。**\n"
            "応募欄に貼って送るのは社長の手作業です。\n"
            f"**{result.get('title', '')}**\n{result.get('url', '')}"
        )
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
            "`!すなくんAmazon <URL>` — Amazon商品URLをスクレイピングし、商品名+アフィリエイトURLをプレビュー表示（`!承認`でキュー追加、`!却下`で破棄）\n"
            "`!レポート` — 日次レポート手動起動\n"
            "`!承認 <ID>` / `!返信承認 <ID>` — X投稿を承認して投稿実行\n"
            "`!却下 <ID>` / `!返信スキップ <ID>` — X投稿をキャンセル\n"
            "`!状況` — 承認待ち一覧表示\n"
            "`!CW承認 <job_id>` — 監視中の案件を「応募済み」として記録（**応募はしません**）\n"
            "`!CW応募済み <案件URL or job_id> [メモ]` — 監視に乗っていない案件を記録（行が無ければ作る）\n"
            "`!在庫` — Pizza在庫確認\n"
            "\n新着案件の通知には **［下書きを出す］［応募した］［見送る］** のボタンが付きます。\n"
            "どのボタンも**クラウドワークスには送りません**。応募は社長が貼って送ってください。\n"
        )
        return


async def _send_channel_notification(channel_id: str, message: str,
                                      image_b64: str | None, image_filename: str | None):
    """webhook_server経由で受けたアカウント別通知を、Botの権限でチャンネルへ直接送信する。
    Incoming Webhookを新規発行せずに済むため、Discord Webhook URLをSecretsに追加する
    手作業が不要になる（[[feedback-irreversible-paid-actions]]のトークン入力制約を回避）。"""
    try:
        channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
        if image_b64:
            data = base64.b64decode(image_b64)
            await channel.send(content=message, file=discord.File(io.BytesIO(data), filename=image_filename or "image.jpg"))
        else:
            await channel.send(content=message)
    except Exception as e:
        print(f"[notify_channel] failed: channel_id={channel_id} error={e}", flush=True)


def notify_channel(channel_id: str, message: str,
                   image_b64: str | None = None, image_filename: str | None = None):
    """webhook_server（別スレッド、同期）からBotのasyncioイベントループへ橋渡しする。"""
    loop = getattr(bot, "loop", None)
    if loop is None or not loop.is_running():
        print(f"[notify_channel] bot loop not ready, dropping notification for channel_id={channel_id}", flush=True)
        return
    asyncio.run_coroutine_threadsafe(
        _send_channel_notification(channel_id, message, image_b64, image_filename), loop
    )


def _channel_slug(name: str) -> str:
    """Discord は、チャンネル名を小文字にし、空白を - にする。それに合わせて、既にあるかを見分ける。"""
    return name.strip().lower().replace(" ", "-").replace("\u3000", "-")[:90]


async def _create_channels(names: list, category_name: str) -> dict:
    """カテゴリの中にテキストチャンネルを作る。すでにあるものは作らず、その ID を返す（何度呼んでも増えない）。
    戻り値: {"channels": {依頼した名前: チャンネルID}} または {"error": 理由}。"""
    guild = bot.guilds[0] if bot.guilds else None
    if guild is None:
        return {"error": "no_guild"}
    try:
        category = discord.utils.get(guild.categories, name=category_name) or await guild.create_category(category_name)
        out = {}
        for name in names:
            slug = _channel_slug(name)
            ch = discord.utils.get(category.text_channels, name=slug) or discord.utils.get(guild.text_channels, name=slug)
            if ch is None:
                ch = await guild.create_text_channel(slug, category=category)
            out[name] = str(ch.id)
        return {"channels": out}
    except discord.Forbidden:
        return {"error": "forbidden"}  # ボットに「チャンネルの管理」の権限が無い
    except Exception as e:  # 中身は返さない。種類だけ
        return {"error": type(e).__name__}


def create_channels(names: list, category_name: str, timeout: int = 60) -> dict:
    """webhook_server（別スレッド、同期）からBotのasyncioイベントループへ橋渡しして、結果を待つ。"""
    loop = getattr(bot, "loop", None)
    if loop is None or not loop.is_running():
        return {"error": "bot_not_ready"}
    fut = asyncio.run_coroutine_threadsafe(_create_channels(names, category_name), loop)
    try:
        return fut.result(timeout=timeout)
    except Exception as e:
        return {"error": type(e).__name__}


def register_pending(approval_id: str, post_text: str, account: str,
                     media_filename: str = "", media_run_id: str = "",
                     media_type: str = "none", affiliate_link: str = ""):
    """外部から承認待ちを登録する。Gist永続化＋3時間TTL（store側で管理）。

    TTLは30分→3時間に延長（2026-07-19）。30分だと社長が席を外している間に
    プレビューが失効し、2026-07-18は生成4回全てが未承認のまま消えて
    すなくんが35時間無投稿になった。3時間は各アカウントの投稿間隔
    （1日4回、約3〜7時間間隔）に合わせた値で、次のプレビューが届く頃には
    前のものが自然失効する。メディアartifactの保持期間(1日)にも収まる。
    """
    # media_path フィールドに media_filename を入れて再利用
    extra_path = f"{media_run_id}:{media_filename}" if media_filename else ""
    _store.set(approval_id, post_text, account, extra_path, media_type, ttl_sec=10800,
               affiliate_link=affiliate_link)
