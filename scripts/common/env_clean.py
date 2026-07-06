"""
GitHub Secrets 経由の環境変数からBOM(﻿)とゼロ幅スペース等の不可視文字を除去する共通ヘルパー。

背景: ANTHROPIC_API_KEY (claude_client.py) と DISCORD_WEBHOOK_URL(S) (discord_notify.py) は
既にBOM除去済みだったが、GEMINI_API_KEY だけ素の os.environ[...] のまま複数箇所
(hal_post.py, sunakun_post.py, nana.py, mio.py, buzz_patterns.py, engagement_loop.py) で
使われており、gRPC呼び出し時に
  grpc._channel._InactiveRpcError: ... status = StatusCode.UNAVAILABLE, details = "Illegal metadata"
  E... plugin_credentials.cc: validate_metadata_from_plugin: INTERNAL:Illegal header value
が発生し、HAL/すなくんの投稿生成が毎回600秒リトライの末に失敗していた
（`| tee` 経由の実行のため GitHub Actions 上は失敗が見えず「成功」表示になっていた）。
"""
import os


def clean_env(name: str) -> str:
    val = os.environ.get(name, "")
    return val.replace('﻿', '').replace('​', '').replace('‌', '').replace('‍', '').strip()
