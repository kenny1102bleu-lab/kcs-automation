"""
ユキ/タクミの生成出力を {post_text, hashtags, media_type, media_prompt, photo_context} にパース。
JSONで来たらそのまま、生テキストならフォールバック。

post_text と hashtags は別フィールド（2段構成: 1段目=本文、2段目=ハッシュタグ）。
呼び出し側（hal_post.py/sunakun_post.py）が最終的な投稿文を組み立てる。
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
            photo_context = obj.get("photo_context", "private") or "private"
            if photo_context not in ("work", "private"):
                photo_context = "private"
            hashtags = obj.get("hashtags") or []
            if not isinstance(hashtags, list):
                hashtags = []
            hashtags = [str(t).strip() for t in hashtags if str(t).strip()]
            return {
                "post_text": str(obj.get("post_text", "")).strip(),
                "hashtags": hashtags,
                "media_type": obj.get("media_type", "none") or "none",
                "media_prompt": obj.get("media_prompt", "") or "",
                "photo_context": photo_context,
            }
    except Exception:
        pass
    return {"post_text": s, "hashtags": [], "media_type": "none", "media_prompt": "", "photo_context": "private"}


def format_hashtags(hashtags: list[str]) -> str:
    """['ootd','秋コーデ'] のような素の単語でも '#ootd #秋コーデ' 形式で返す。"""
    return " ".join(t if t.startswith("#") else f"#{t}" for t in hashtags)


def assemble_post(main_text: str, hashtags: list[str]) -> str:
    """1段目（本文）と2段目（ハッシュタグ）を空行で分けて結合する。"""
    tag_line = format_hashtags(hashtags)
    if not tag_line:
        return main_text
    return f"{main_text}\n\n{tag_line}"
