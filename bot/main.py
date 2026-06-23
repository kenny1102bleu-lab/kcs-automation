"""
Render.com のエントリーポイント。
Webhook サーバー（8080）と Discord Bot を同時起動する。
"""
from bot.webhook_server import start as start_webhook, set_register_callback
import bot.discord_bot as bot_module

set_register_callback(bot_module.register_pending)
start_webhook()
bot_module.bot.run(bot_module.TOKEN)
