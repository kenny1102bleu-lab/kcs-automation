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
import random
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
    "photorealistic, natural light, candid, do not include any brand logo"
)
SUNAKUN_BASE_STYLE = (
    "product photography style, clean white background or modern desk setup, "
    "tech gadgets focus, vibrant but not flashy, photorealistic"
)

# HAL_PERSONA_BIBLE.md 第7-3章「セルフィー投稿のシチュエーション設定」＋
# 第3章の推し活/サッカー/台湾ルーツ設定を反映したロケーション・バリエーション。
# 旧実装は "daikanyama Tokyo street or cafe" を毎回固定で使っていたため、
# 投稿を重ねるほど背景が代わり映えしない問題があった（社長指摘、2026-07-08）。
#
# キーワードに紐づくシーンは、その日の投稿テーマ（media_prompt）に一致した
# ときだけ優先的に選ばれる。一致しなければ、一般的な日常シーンから
# バイブル記載の頻度（寝室週2・その他週1）に沿って重み付き抽選する。
HAL_KEYWORD_SCENES = [
    (("K-POP", "LE SSERAFIM", "IVE", "推し", "ライブ"),
     "bedroom desk covered in K-pop photocards, light stick, and posters, "
     "excited candid selfie energy, fan merchandise visible"),
    (("サッカー", "代表戦", "スタジアム", "観戦"),
     "cozy living room with a soccer match paused on the TV in the background, "
     "wearing a casual football jersey, excited candid mood"),
    (("台湾", "父", "夜市"),
     "warm string-lit Taiwanese-style night market stall or a retro Taiwanese cafe corner, "
     "nostalgic warm lighting, cozy and a little homesick mood"),
    (("配信", "本番", "スタジオ"),
     "home streaming corner with ring light and microphone visible, casual pre-stream mood"),
]

HAL_PRIVATE_SCENES = [
    ("寝室でリラックス", "cozy bedroom at night, warm low ambient lighting, relaxed nighttime mood", 2),
    ("カフェでの休憩", "trendy Tokyo cafe by a window (Nakameguro or Ebisu style), soft natural daylight, "
     "casual coffee break mood, drink on the table", 1),
    ("おでかけ先", "walking around a lively Tokyo neighborhood such as Shibuya, Harajuku, or Omotesando, "
     "outdoor natural daylight, active going-out mood", 1),
    ("おうちコーデ", "cozy home interior, soft indoor lighting, mirror selfie showing today's outfit", 1),
    ("公園の散歩道", "a quiet Tokyo park path lined with trees, soft natural daylight, relaxed walking mood", 1),
]

HAL_WORK_SCENES = [
    ("スタジオ撮影の裏側", "professional photo studio backstage, softbox lighting rigs visible in the background, "
     "behind-the-scenes work atmosphere"),
    ("屋外ロケ撮影", "outdoor fashion shoot location in Tokyo, professional lighting equipment nearby, "
     "staff working softly out of focus in the background"),
    ("ブランドコラボ撮影", "styled fashion shoot set with clothing racks and mirrors, professional shoot atmosphere"),
    ("雑誌撮影現場", "editorial magazine shoot set, photographer and reflector visible in soft focus background"),
]


def _pick_hal_scene(photo_context: str, prompt_text: str) -> str:
    """投稿テーマ・シチュエーションに応じてロケーションを選ぶ。
    work: 常に仕事シーンからランダム。
    private: テーマにK-POP/サッカー/台湾/配信のキーワードがあれば対応シーンを優先、
             無ければバイブル記載の頻度に沿って一般シーンから重み付き抽選する。"""
    if photo_context == "work":
        return random.choice(HAL_WORK_SCENES)[1]

    text = (prompt_text or "").lower()
    for keywords, scene in HAL_KEYWORD_SCENES:
        if any(k.lower() in text for k in keywords):
            return scene

    scenes, weights = zip(*[(s[1], s[2]) for s in HAL_PRIVATE_SCENES])
    return random.choices(scenes, weights=weights, k=1)[0]


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


WORK_COMPOSITION = (
    "Composition: photographed BY SOMEONE ELSE (a photographer/staff member), "
    "third-person candid photography angle, she is NOT holding a phone or camera, "
    "natural professional photography framing, medium or full shot showing her "
    "interacting with the environment, no visible selfie arm."
)
PRIVATE_COMPOSITION = (
    "Composition: a SELFIE she is taking RIGHT NOW, in this exact moment, on a "
    "smartphone held in her own hand. This is the single most important framing "
    "rule and must ALWAYS be followed: the camera's viewpoint IS the front camera "
    "of the phone she is holding, so her arm MUST always be visibly extended "
    "toward the camera (shoulder/arm leading into frame, slightly bent elbow, "
    "as if she just raised her phone to snap the shot) - this is mandatory, not "
    "optional, in every private/selfie image. Because the phone in her hand is "
    "the camera taking the photo, the phone/camera/device itself must NEVER "
    "appear anywhere in the frame (it cannot photograph itself) - no phone body, "
    "no camera lens, no screen visible anywhere. Close-up front-camera angle "
    "with slight wide-angle selfie distortion, eye-level or slightly elevated "
    "perspective (arm raised above or at face height), casual intimate framing "
    "typical of a personal phone selfie - never a composition that could be "
    "mistaken for a photo taken by someone else."
)


def _current_time_context() -> str:
    """JST（東京）の実際の日付・時間から季節と時間帯を判定し、
    服装や背景の明暗を実際の日時に合わせるための指示文を返す。"""
    now_jst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    month, hour = now_jst.month, now_jst.hour

    if month in (3, 4, 5):
        season = "spring (March-May), mild weather - light cardigans, knitwear, or light jackets"
    elif month in (6, 7, 8):
        season = "summer (June-August), hot weather - short sleeves, light/thin clothing"
    elif month in (9, 10, 11):
        season = "autumn (September-November), cool weather - knitwear, light coats"
    else:
        season = "winter (December-February), cold weather - coats, mufflers, warm layers"

    if 5 <= hour < 9:
        time_of_day = "early morning, soft morning sunlight, few people around"
    elif 9 <= hour < 16:
        time_of_day = "daytime, bright natural sunlight, blue sky if outdoors"
    elif 16 <= hour < 19:
        time_of_day = "evening golden hour, warm orange sunset light"
    elif 19 <= hour < 23:
        time_of_day = "night, warm artificial indoor lighting if indoors, dark sky and street lights if outdoors"
    else:
        time_of_day = "late night, dark and calm atmosphere, only dim indoor ambient lighting"

    return (
        f"Current real-world date/time context (Tokyo/JST, {now_jst.strftime('%Y-%m-%d %H:%M')}): "
        f"season is {season}; time of day is {time_of_day}. "
        "The clothing and the background lighting/brightness in the generated image/video MUST "
        "realistically match this season and time of day. Do not show mismatched weather, "
        "clothing, or lighting (e.g. no summer clothes in winter, no bright daylight at night)."
    )


def generate_media(media_type: str, prompt: str, account: str, photo_context: str = "private") -> dict:
    media_type = (media_type or "none").lower()
    if media_type == "none" or not prompt:
        return {"path": "", "type": "none"}

    is_hal = account.upper() == "HAL"
    base_style = HAL_BASE_STYLE if is_hal else SUNAKUN_BASE_STYLE
    # 構図（仕事/自撮り）の出し分けは人物の自撮り/他撮りが意味を持つHALのみ。
    # すなくんは商品写真スタイルのため、人物代名詞を含むこの指示は混入させない。
    composition = (WORK_COMPOSITION if photo_context == "work" else PRIVATE_COMPOSITION) if is_hal else ""
    # ロケーションのバリエーション（HALのみ）。毎回同じ背景にならないよう、
    # テーマに応じて/頻度に沿ってシーンを1つ選ぶ（HAL_PERSONA_BIBLE.md 第7-3章準拠）。
    scene = f"Scene: {_pick_hal_scene(photo_context, prompt)}" if is_hal else ""
    time_context = _current_time_context()
    segments = [prompt, f"Style: {base_style}"] + ([scene] if scene else []) + ([composition] if composition else []) + [time_context]
    full_prompt = ". ".join(segments)

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
