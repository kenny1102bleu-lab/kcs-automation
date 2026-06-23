"""
承認待ち投稿を Bot に登録するための軽量 Webhook サーバー。
GitHub Actions の hal_post.py / sunakun_post.py が
このエンドポイントに POST して pending_approvals に追加する。

Render.com では discord_bot.py と同じサービスで起動できるよう
別スレッドで動かす。
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

_register_callback = None  # discord_bot.py の register_pending を後から注入


def set_register_callback(fn):
    global _register_callback
    _register_callback = fn


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        if _register_callback:
            _register_callback(
                body["approval_id"],
                body["post_text"],
                body["account"],
            )
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, *args):
        pass  # アクセスログを抑制


def start(port: int = None):
    if port is None:
        port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Webhook server listening on :{port}")
