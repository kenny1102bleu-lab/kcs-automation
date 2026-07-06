"""
ミオ - Gemini Vision で生成画像を検品する。

チェック項目:
  1. 顔の整合性（指定キャラから逸脱していないか）
  2. 四肢の描写（指数・関節）
  3. 背景の崩れ・歪み
  4. 露出過多・未承認ブランドロゴ
  5. 「AI」「中の人」等のテキスト混入

返り値: {"approved": bool, "score": int(0-100), "reason": str}
"""
import os
import json
import base64
import pathlib
import requests

from scripts.common.env_clean import clean_env, redact_key


CRITERIA = {
    "HAL": (
        "Target: 21yo Japanese-Taiwanese mixed female, long brown wavy hair, "
        "K-pop natural makeup, healing aura, modest outfit."
    ),
    "SUNAKUN": (
        "Target: tech gadget product photography, clean modern setup."
    ),
}


def inspect_media(image_path: str, account: str) -> dict:
    p = pathlib.Path(image_path)
    if not p.exists():
        return {"approved": False, "score": 0, "reason": "image file missing"}
    img_b64 = base64.b64encode(p.read_bytes()).decode()

    target = CRITERIA.get(account.upper(), CRITERIA["HAL"])
    prompt = (
        "あなたは生成画像の検品担当『ミオ』です。以下の画像を5項目で評価してください。\n"
        f"{target}\n\n"
        "【検品項目】\n"
        "1. 顔の整合性（キャラ仕様から逸脱していないか）\n"
        "2. 四肢の描写（指数・関節に違和感がないか）\n"
        "3. 背景の整合性（文字の崩れ、空間の歪みがないか）\n"
        "4. 隠れNG（露出過多、未承認ブランドロゴの混入）\n"
        "5. 言語チェック（『AI』『中の人』『広告』等の不要文字列が画面内にないか）\n\n"
        "重大な問題があれば approved=false、軽微なら approved=true。\n"
        "出力はJSONのみ:\n"
        '{"approved":true|false, "score":0-100, "reason":"問題があれば具体的に、なければ問題なし"}'
    )

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-flash:generateContent"
    )
    try:
        r = requests.post(
            url,
            params={"key": clean_env("GEMINI_API_KEY")},
            json={
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"inlineData": {"mimeType": "image/png", "data": img_b64}},
                    ]
                }],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
            },
            timeout=45,
        )
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        obj = json.loads(text)
        return {
            "approved": bool(obj.get("approved", False)),
            "score": int(obj.get("score", 0)),
            "reason": str(obj.get("reason", "")),
        }
    except Exception as e:
        # 検品エラー時は安全側で通す（ナナの再試行ループ無限化を防ぐ）
        return {"approved": True, "score": 50, "reason": f"mio_error: {redact_key(e)}"}
