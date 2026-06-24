"""
ユキ/タクミの生成出力を {post_text, media_type, media_prompt} にパース。
JSONで来たらそのまま、生テキストならフォールバック。
"""
import json
import re


def parse(raw: str) -> dict:
    s = raw.strip()
    # コードフェンス除去
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        obj = json.loads(s)
        if isinstance(obj, dict) and "post_text" in obj:
            return {
                "post_text": str(obj.get("post_text", "")).strip(),
                "media_type": obj.get("media_type", "none") or "none",
                "media_prompt": obj.get("media_prompt", "") or "",
            }
    except Exception:
        pass
    return {"post_text": s, "media_type": "none", "media_prompt": ""}
