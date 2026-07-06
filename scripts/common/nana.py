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
from scripts.common.env_clean import clean_env

OUTPUT_DIR = pathlib.Path("media_out")
OUTPUT_DIR.mkdir(exist_ok=True)

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
    """Gemini 2.5 Flash Image (Nano Banana) で画像生成 → ミオ検品"""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-flash-image-preview:generateContent"
    )
    for attempt in range(2):
        try:
            r = requests.post(
                url,
                params={"key": _api_key()},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseModalities": ["IMAGE"]},
                },
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
            parts = data["candidates"][0]["content"]["parts"]
            img_b64 = next(p["inlineData"]["data"] for p in parts if "inlineData" in p)
        except Exception as e:
            return {"path": "", "type": "image", "error": f"gen_failed: {e}"}

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
        "veo-3.0-generate-preview:predictLongRunning"
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
        return {"path": "", "type": "video", "error": f"veo_create_failed: {e}"}

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
            print(f"[nana] poll error: {e}")
    return {"path": "", "type": "video", "error": "veo_timeout"}
