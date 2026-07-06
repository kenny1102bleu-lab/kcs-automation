"""
すなくん: 楽天ランキングAPIから実際の商品（画像・価格・アフィリエイトリンク）を取得する。

背景: 旧GAS実装（C:\\tmp\\KCS_GAS_upgrade\\04_suna_product_scraper.gs,
07_suna_auto_pick.gs, 2026-06-09完成）に同等のロジックがあったが、
n8n/Make.com時代のアーキテクチャ用でGitHub Actions移行時に引き継がれず、
現行のsunakun_post.pyはAI生成の商品「風」画像を使っていた
（実商品ではないため社長指摘で本モジュールを新設）。

fetch_trending_product() が None を返した場合、呼び出し側は既存の
AI生成テーマ投稿にフォールバックする（既存挙動を壊さない）。
"""
import json
import pathlib
import random

import requests

from scripts.common.env_clean import clean_env

# PC周辺, スマホ, ゲーム, 家電（旧GAS実装から踏襲）
RAKUTEN_GADGET_GENRES = ["100371", "100433", "562637", "211742"]

POSTED_HISTORY_PATH = pathlib.Path("data/suna_posted_products.json")
POSTED_HISTORY_MAX = 200

OUTPUT_DIR = pathlib.Path("media_out")
OUTPUT_DIR.mkdir(exist_ok=True)


def _load_posted_urls() -> set[str]:
    if not POSTED_HISTORY_PATH.exists():
        return set()
    try:
        return set(json.loads(POSTED_HISTORY_PATH.read_text(encoding="utf-8")))
    except Exception:
        return set()


def record_posted_url(url: str) -> None:
    if not url:
        return
    urls = list(_load_posted_urls())
    urls.append(url)
    urls = urls[-POSTED_HISTORY_MAX:]
    POSTED_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    POSTED_HISTORY_PATH.write_text(json.dumps(urls, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_trending_product() -> dict | None:
    """楽天ランキングAPIからガジェット系カテゴリの商品をランダムに1件取得。
    RAKUTEN_APP_ID未設定・API失敗・全件投稿済みの場合はNone
    （呼び出し側は既存のAI生成テーマ投稿にフォールバック）。"""
    app_id = clean_env("RAKUTEN_APP_ID")
    if not app_id:
        return None

    affiliate_id = clean_env("RAKUTEN_AFFILIATE_ID")
    posted = _load_posted_urls()
    genre_id = random.choice(RAKUTEN_GADGET_GENRES)

    params = {
        "format": "json",
        "genreId": genre_id,
        "applicationId": app_id,
    }
    if affiliate_id:
        params["affiliateId"] = affiliate_id

    try:
        r = requests.get(
            "https://app.rakuten.co.jp/services/api/IchibaItem/Ranking/20220601",
            params=params,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[product_source] rakuten ranking api failed: {e}")
        return None

    items = data.get("Items") or []
    candidates = [it["Item"] for it in items if it.get("Item")]
    unused = [it for it in candidates if it.get("itemUrl") not in posted]
    if not unused and candidates:
        # 全件投稿済みなら履歴を無視して先頭を使う（在庫切れで投稿が止まるのを防ぐ）
        unused = candidates
    if not unused:
        return None

    item = unused[0]
    image_urls = item.get("mediumImageUrls") or []
    image_url = image_urls[0]["imageUrl"] if image_urls else ""
    # 楽天の画像URLは "?_ex=128x128" のようなサイズ指定が付くことがあるため、
    # 大きいサイズに寄せて画質を確保する。
    image_url = image_url.split("?")[0] + "?_ex=800x800" if image_url else ""

    return {
        "source": "rakuten",
        "title": item.get("itemName", ""),
        "price": f"{item.get('itemPrice', '')}円",
        "description": (item.get("itemCaption") or "")[:200],
        "item_url": item.get("itemUrl", ""),
        "affiliate_url": item.get("affiliateUrl") or item.get("itemUrl", ""),
        "image_url": image_url,
    }


def download_product_image(image_url: str, product_name: str, account: str = "SUNAKUN") -> dict:
    """実商品画像をダウンロードしてmedia_out/に保存。ナナのgenerate_media()と
    同じ戻り値形状（path/type）にして呼び出し側の後続処理を共通化する。"""
    if not image_url:
        return {"path": "", "type": "image", "error": "product image_url is empty"}
    try:
        r = requests.get(image_url, timeout=30)
        r.raise_for_status()
    except Exception as e:
        return {"path": "", "type": "image", "error": f"product_image_download_failed: {e}"}

    import datetime
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    ext = ".jpg" if image_url.lower().split("?")[0].endswith((".jpg", ".jpeg")) else ".png"
    path = OUTPUT_DIR / f"{account}_{ts}_product{ext}"
    path.write_bytes(r.content)
    return {"path": str(path), "type": "image", "product_title": product_name}
