"""
Render.com のエントリーポイント。
Webhook サーバーと Discord Bot を同時起動する。
"""
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, *args):
        pass


port = int(os.environ.get("PORT", 10000))
server = HTTPServer(("0.0.0.0", port), HealthHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
print(f"HTTP server listening on 0.0.0.0:{port}")

from bot.webhook_server import set_register_callback
import bot.discord_bot as bot_module

set_register_callback(bot_module.register_pending)
bot_module.bot.run(bot_module.TOKEN)
