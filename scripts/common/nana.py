"""
ナナ - 画像/動画生成モジュール（Gemini Nano Banana + Veo）

generate_media(media_type, prompt, account) → {"path": "...", "type": "image|video|none", "error": "..."}

【画像】Gemini 2.5 Flash Image (Nano Banana) を使用
【動画】Veo 3 (gemini-2.5-veo) を使用。月間上限あり
"""
import os
import base64
import datetime
import pathlib
import requests

from scripts.common.mio import inspect_media
from scripts.common.env_clean import clean_env, redact_key

OUTPUT_DIR = pathlib.Path("media_out")
OUTPUT_DIR.mkdir(exist_ok=True)

# アカウントごとの顔参照画像。存在すれば生成時にinlineDataとして渡し、
# 「毎回別人が生成される」問題を防いで同一人物の顔を維持する。
REFERENCE_IMAGES = {
    "HAL": pathlib.Path("assets/reference/hal_reference.png"),
}


def _load_reference_b64(account: str) -> str | None:
    p = REFERENCE_IMAGES.get(account.upper())
    if not p or not p.exists():
        return None
    return base64.b64encode(p.read_bytes()).decode()


HAL_BASE_STYLE = (
    "Japanese-Taiwanese mixed female, 21 years old, long brown wavy hair, "
    "K-pop natural makeup, soft healing aura, modest stylish clothing, "
    "photorealistic, daikanyama Tokyo street or cafe background, "
    "natural light, candid, do not include any brand logo"
)
SUNAKUN_BASE_STYLE = (
    "product photography style, clean white background or modern desk setup, "
    "tech gadgets focus, vibrant but not flashy, photorealistic"
)


def _api_key():
    return clean_env("GEMINI_API_KEY")


_redact = redact_key


def _video_usage_path():
    return OUTPUT_DIR / "video_usage.txt"


def _video_count_this_month():
    p = _video_usage_path()
    if not p.exists():
        return 0
    ym = datetime.datetime.utcnow().strftime("%Y-%m")
    return sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.startswith(ym))


def _record_video_use():
    with _video_usage_path().open("a", encoding="utf-8") as f:
        f.write(datetime.datetime.utcnow().isoformat() + "\n")


def generate_media(media_type: str, prompt: str, account: str) -> dict:
    media_type = (media_type or "none").lower()
    if media_type == "none" or not prompt:
        return {"path": "", "type": "none"}

    base_style = HAL_BASE_STYLE if account.upper() == "HAL" else SUNAKUN_BASE_STYLE
    full_prompt = f"{prompt}. Style: {base_style}"

    if media_type == "video":
        max_video = int(os.environ.get("MAX_VIDEO_PER_MONTH", "10"))
        if _video_count_this_month() >= max_video:
            print(f"[nana] video quota exceeded ({max_video}/month) → fallback to image")
            media_type = "image"
        else:
            return _generate_video(full_prompt, account)

    return _generate_image(full_prompt, account)


def _generate_image(prompt: str, account: str) -> dict:
    """Gemini 2.5 Flash Image (Nano Banana) で画像生成 → ミオ検品。
    参照画像があれば同梱し、顔の同一性を維持したまま服装/シーンだけ変える。"""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-flash-image:generateContent"
    )
    ref_b64 = _load_reference_b64(account)

    def _build_request_parts(current_prompt: str) -> list[dict]:
        if not ref_b64:
            return [{"text": current_prompt}]
        text_prompt = (
            "Attached is the reference photo of the character. Keep the EXACT SAME face, "
            "facial features, and identity as the attached reference photo. Do not change "
            "who she is. Only change the outfit, pose, and scene as described below.\n\n"
            f"Scene/outfit description: {current_prompt}"
        )
        return [
            {"text": text_prompt},
            {"inlineData": {"mimeType": "image/png", "data": ref_b64}},
        ]

    for attempt in range(2):
        try:
            r = requests.post(
                url,
                params={"key": _api_key()},
                json={
                    "contents": [{"parts": _build_request_parts(prompt)}],
                    "generationConfig": {"responseModalities": ["IMAGE"]},
                },
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
            response_parts = data["candidates"][0]["content"]["parts"]
            img_b64 = next(p["inlineData"]["data"] for p in response_parts if "inlineData" in p)
        except Exception as e:
            safe_err = _redact(e)
            body = ""
            try:
                body = _redact(r.text[:500])
            except Exception:
                pass
            print(f"[nana] image gen failed: {safe_err} | response_body={body}")
            return {"path": "", "type": "image", "error": f"gen_failed: {safe_err}"}

        ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = OUTPUT_DIR / f"{account}_{ts}_a{attempt}.png"
        path.write_bytes(base64.b64decode(img_b64))

        # ミオ検品
        review = inspect_media(str(path), account=account)
        if review.get("approved"):
            return {"path": str(path), "type": "image", "mio_score": review.get("score")}
        # 却下なら再生成
        prompt = f"{prompt}. AVOID: {review.get('reason', '')}"

    return {"path": str(path), "type": "image", "warning": "mio rejected twice but using last attempt"}


def _generate_video(prompt: str, account: str) -> dict:
    """Veo で動画生成（非同期、ポーリング）"""
    create_url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "veo-3.1-generate-preview:predictLongRunning"
    )
    try:
        r = requests.post(
            create_url,
            params={"key": _api_key()},
            json={
                "instances": [{"prompt": prompt}],
                "parameters": {"durationSeconds": 5, "aspectRatio": "9:16"},
            },
            timeout=30,
        )
        r.raise_for_status()
        op_name = r.json()["name"]
    except Exception as e:
        return {"path": "", "type": "video", "error": f"veo_create_failed: {_redact(e)}"}

    # ポーリング（最大6分）
    poll_url = f"https://generativelanguage.googleapis.com/v1beta/{op_name}"
    import time
    for _ in range(36):
        time.sleep(10)
        try:
            r = requests.get(poll_url, params={"key": _api_key()}, timeout=30)
            r.raise_for_status()
            res = r.json()
            if res.get("done"):
                video_uri = res["response"]["generateVideoResponse"]["generatedSamples"][0]["video"]["uri"]
                # ダウンロード
                vr = requests.get(video_uri, params={"key": _api_key()}, timeout=120)
                ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                path = OUTPUT_DIR / f"{account}_{ts}.mp4"
                path.write_bytes(vr.content)
                _record_video_use()
                return {"path": str(path), "type": "video"}
        except Exception as e:
            print(f"[nana] poll error: {_redact(e)}")
    return {"path": "", "type": "video", "error": "veo_timeout"}
