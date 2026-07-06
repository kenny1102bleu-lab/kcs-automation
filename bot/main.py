"""
Render.com のエントリーポイント。
Webhook サーバー（承認待ち登録）と Discord Bot を同時起動する。

過去のバグ: ここで独自のダミー HealthHandler サーバーを起動しており、
bot/webhook_server.py の start()（実際に pending_approvals へ登録する処理）が
一度も呼ばれていなかった。そのため hal_post.py / sunakun_post.py からの
BOT_WEBHOOK_URL POST は常に「200 OKを返すだけで何も登録しない」ダミー実装に
吸収され、!承認 <ID> が毎回「見つかりません」になり、X投稿（画像/動画付き）が
一度も成功しない原因になっていた。
"""
import os

from bot.webhook_server import set_register_callback, start as start_webhook_server
import bot.discord_bot as bot_module

# コールバック登録 → Webhookサーバー起動（この順序を守らないと起動直後のPOSTを取りこぼす）
set_register_callback(bot_module.register_pending)
start_webhook_server(port=int(os.environ.get("PORT", 10000)))

bot_module.bot.run(bot_module.TOKEN)
