import os
import anthropic


def _clean_secret(name: str) -> str:
    """環境変数からBOM(﻿)とゼロ幅スペース等の不可視文字を除去。
    GitHub Secretsに混入したBOMがHTTPヘッダー送信時にUnicodeEncodeErrorを引き起こすため。
    """
    val = os.environ.get(name, "")
    return val.replace('﻿', '').replace('​', '').replace('‌', '').replace('‍', '').strip()


_api_key = _clean_secret("ANTHROPIC_API_KEY")
if not _api_key:
    raise RuntimeError("ANTHROPIC_API_KEY is empty or missing after BOM-strip")

client = anthropic.Anthropic(api_key=_api_key)

def call_claude(system_prompt: str, user_message: str, model: str = "claude-opus-4-8") -> str:
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text
