"""
一回限りの開発用スクリプト: すなくんの新しいビジュアル（イケメン男性、自撮り投稿用）の
候補画像を複数パターン生成し、社長に選んでもらう。

生成後、data/sunakun_face_candidates/ に保存。GitHub Actions artifactとして回収する想定。
既存の HAL 用 nana._generate_image をそのまま流用（Gemini 2.5 Flash Image、ミオ検品込み）。
"""
import sys
import os
import shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.common.nana import _generate_image

OUT_DIR = "data/sunakun_face_candidates"

CANDIDATES = {
    "A_seiketsu": (
        "Photorealistic portrait of a Japanese man in his mid-20s, short neat black hair, "
        "clean-cut friendly face, casual simple outfit (plain t-shirt), sitting at a "
        "tech-gadget-filled desk with monitors and cables in the background, warm approachable "
        "smile, soft natural lighting, looks like a relatable tech enthusiast, not overly styled"
    ),
    "B_trend": (
        "Photorealistic portrait of a Japanese man in his mid-20s, stylish trendy short "
        "hairstyle with slight texture, streetwear-casual fashion, confident but friendly "
        "expression, sitting in a modern minimalist cafe or stylish apartment, natural window "
        "light, looks like a stylish gadget/tech influencer"
    ),
    "C_kireime": (
        "Photorealistic portrait of a Japanese man in his mid-20s, medium-length neatly styled "
        "hair, smart-casual outfit (simple button shirt or knit), warm gentle smile, sitting in "
        "a bright clean home office with a laptop and gadgets nearby, soft daylight, approachable "
        "and slightly polished look"
    ),
    "D_b_style_c_face": (
        "Photorealistic portrait of a Japanese man in his mid-20s. Face and expression: "
        "medium-length neatly styled hair, soft polished gentle facial features, warm gentle "
        "friendly smile, kind approachable eyes (kireime, refined look). Styling and setting: "
        "streetwear-casual trendy fashion, sitting in a modern minimalist cafe or stylish "
        "apartment with gadgets nearby, natural window light, looks like a stylish approachable "
        "gadget/tech influencer with a soft friendly face"
    ),
    # 2026-07-25保存のreference_ai_image_naturalization_prompts.mdの「人物画像テンプレ」を
    # 反映（nana.pyのNATURALIZATION_SUFFIXは女性代名詞でHAL専用のため、男性向けに書き直して
    # 非対称さ・境目の自然さ・AIっぽいツヤの抑制など、メモリ記載でより詳細な項目を追加）
    "E_refined": (
        "Photorealistic portrait of a Japanese man in his mid-20s. Face and expression: "
        "medium-length neatly styled hair, soft polished gentle facial features, warm gentle "
        "friendly smile, kind approachable eyes (kireime, refined look). Styling and setting: "
        "streetwear-casual trendy fashion, sitting in a modern minimalist cafe or stylish "
        "apartment with gadgets nearby, natural window light, looks like a stylish approachable "
        "gadget/tech influencer with a soft friendly face. "
        "Make this look like an authentic candid photo that could realistically be found on "
        "social media, not a generated image: natural smartphone-camera texture, natural "
        "ambient light matching the scene, a natural relaxed gaze rather than a stiff stare "
        "at the camera, natural-looking hands and fingers with no distortion, balanced facial "
        "proportions with slight human asymmetry rather than perfect symmetry, hair flowing "
        "naturally, skin with natural texture and visible pores rather than overly smoothed, "
        "the light direction on him matching the light direction of the background, natural "
        "blending at the edges between clothes/hair/background, and avoid an overly glossy "
        "AI-generated sheen or overly perfect polish."
    ),
}


def run():
    only = sys.argv[1].split(",") if len(sys.argv) > 1 else None
    os.makedirs(OUT_DIR, exist_ok=True)

    for label, prompt in CANDIDATES.items():
        if only and label not in only:
            continue
        print(f"[dev_sunakun_face_candidates] generating {label} ...")
        result = _generate_image(prompt, account=f"SUNAKUN_CANDIDATE_{label}")
        if result.get("error"):
            print(f"  失敗: {result['error']}")
            continue
        src = result["path"]
        if not src or not os.path.exists(src):
            print("  失敗: 出力ファイルなし")
            continue
        dst = os.path.join(OUT_DIR, f"{label}.png")
        shutil.copy(src, dst)
        print(f"  保存: {dst} (mio_score={result.get('mio_score', '-')})")


if __name__ == "__main__":
    run()
