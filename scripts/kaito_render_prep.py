"""
カイト：動画編集の下ごしらえ支援（人間起動のCLIヘルパー）。
WF-07 (07_video_render.yml / HyperFrames) が要求する variables JSON を、
簡単なブリーフからカイト（Gemini）に組み立てさせる。
WF-07自体は変更しない。動画は可視性が最も高いコンテンツのため、自動実行では
なく人間（ジュン専務/ユキ）が呼び出す補助ツールとして位置づける
（シート上の肩書「動画編集"支援"」を尊重）。

使い方:
    python -m scripts.kaito_render_prep --template hal_x_post --brief "おはようコーデ紹介、10秒、テンポよく"
"""
import argparse
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import google.generativeai as genai
from scripts.common.env_clean import clean_env

KAITO_PROMPT = """あなたはKCS合同会社の動画編集支援担当「カイト」です。
テンポ感とカット割りに強く、渡されたブリーフから HyperFrames 動画レンダリングの
variables JSON を組み立てます。

【出力ルール（JSON形式厳守、説明文なし）】
{
  "duration": <秒数、数値>,
  "caption_duration": <字幕表示秒数、数値>,
  "caption_ja": "<日本語字幕、20字以内>",
  "caption_tc": "<中国語簡体字字幕、20字以内>",
  "media_path": ""
}
- caption_ja/caption_tcはブリーフの内容を簡潔にまとめる。断定的な事実主張はしない
- durationはブリーフに秒数指定があればそれに従い、無指定なら10
- media_pathは空文字のまま（呼び出し側が別途セットする）"""


def build_variables(template: str, brief: str) -> dict:
    api_key = clean_env("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY が未設定です")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.5-flash", system_instruction=KAITO_PROMPT)
    response = model.generate_content(f"テンプレ: {template}\nブリーフ: {brief}")
    text = (response.text or "").strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", required=True, help="hal_x_post / suna_short / moru_shorts")
    parser.add_argument("--brief", required=True, help="動画内容の簡単なブリーフ")
    args = parser.parse_args()

    variables = build_variables(args.template, args.brief)
    print(json.dumps(variables, ensure_ascii=False))
    print(
        f"\n# GitHub Actions手動実行例:\n"
        f"# gh workflow run 07_video_render.yml -f template={args.template} "
        f"-f variables='{json.dumps(variables, ensure_ascii=False)}' -f staff=kaito",
        file=sys.stderr,
    )


if __name__ == "__main__":
    run()
