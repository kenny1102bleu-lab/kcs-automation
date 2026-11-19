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
_notify_callback = None  # discord_bot.py の notify_channel を後から注入（アカウント別チャンネル直送用）
_channels_callback = None  # discord_bot.py の create_channels を後から注入（チャンネルをまとめて作る）

MAX_CHANNELS = 30  # 一度に作れる数の上限（暴走・誤送信の歯止め）
MAX_NAME_LEN = 80


def set_register_callback(fn):
    global _register_callback
    _register_callback = fn


def set_notify_callback(fn):
    global _notify_callback
    _notify_callback = fn


def set_channels_callback(fn):
    global _channels_callback
    _channels_callback = fn


def validate_channel_request(body: dict):
    """チャンネル作成の依頼を確かめる。(名前の一覧, カテゴリ名) を返す。おかしければ ValueError。"""
    names = body.get("names")
    category = str(body.get("category") or "").strip()
    if not isinstance(names, list) or not names:
        raise ValueError("names が空です")
    if len(names) > MAX_CHANNELS:
        raise ValueError(f"一度に作れるのは {MAX_CHANNELS} 件までです")
    clean = []
    for n in names:
        n = str(n).strip()
        if not n or len(n) > MAX_NAME_LEN:
            raise ValueError("チャンネル名が空、または長すぎます")
        clean.append(n)
    if not category or len(category) > MAX_NAME_LEN:
        raise ValueError("category が空、または長すぎます")
    return clean, category


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def do_POST(self):
        print(f"[webhook_server] POST received from {self.client_address}", flush=True)
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            if body.get("action") == "create_channels":
                # 結果（チャンネルの ID）を返す必要があるので、他の action と違い、終わるまで待って答える
                names, category = validate_channel_request(body)
                result = _channels_callback(names, category) if _channels_callback else {"error": "channels callback not set"}
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
                return
            if body.get("action") == "notify":
                # アカウント別チャンネルへの直接通知（discord_notify.notify_account経由）
                if _notify_callback:
                    _notify_callback(
                        body["channel_id"],
                        body["message"],
                        body.get("image_b64"),
                        body.get("image_filename"),
                    )
            elif _register_callback:
                _register_callback(
                    body["approval_id"],
                    body["post_text"],
                    body["account"],
                    media_filename=body.get("media_filename", ""),
                    media_run_id=body.get("media_run_id", ""),
                    media_type=body.get("media_type", "none"),
                    affiliate_link=body.get("affiliate_link", ""),
                )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        except Exception as e:
            print(f"[webhook_server] do_POST failed: {e}", flush=True)
            try:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"status":"error"}')
            except Exception:
                pass

    def log_message(self, *args):
        pass  # アクセスログを抑制


def start(port: int = None):
    if port is None:
        port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Webhook server listening on :{port}")
