"""
すなくん: 楽天ランキングAPIから実際の商品（画像・価格・アフィリエイトリンク）を取得する。

背景: 旧GAS実装（C:\\tmp\\KCS_GAS_upgrade\\04_suna_product_scraper.gs,
07_suna_auto_pick.gs, 2026-06-09完成）に同等のロジックがあったが、
n8n/Make.com時代のアーキテクチャ用でGitHub Actions移行時に引き継がれず、
現行のsunakun_post.pyはAI生成の商品「風」画像を使っていた
（実商品ではないため社長指摘で本モジュールを新設）。

2026年2月の楽天API刷新（旧 app.rakuten.co.jp エンドポイントは廃止）に伴い、
新エンドポイント(openapi.rakuten.co.jp)・新認証(applicationId+accessKey+
Referer/Originヘッダー必須)に対応。旧GAS実装のジャンルIDは検証の結果
そもそも誤り（例:「スマホ」のつもりのIDが実際は下着・ナイトウェアだった）
だったため、実際に楽天ジャンル検索APIで確認し直した正しいIDに置き換えた。

fetch_trending_product() が None を返した場合、呼び出し側は既存の
AI生成テーマ投稿にフォールバックする（既存挙動を壊さない）。
"""
import json
import pathlib
import random
import re

import requests

from scripts.common.env_clean import clean_env

RANKING_API_URL = "https://openapi.rakuten.co.jp/ichibaranking/api/IchibaItem/Ranking/20220601"

# API利用登録アプリ「KCS収集トレンド」の許可ウェブサイトが google.com のため、
# Referer/Originもこれに合わせる必要がある（変更する場合は要連動）。
API_REFERER = "https://google.com"

# ガジェット系（パソコン・周辺機器/スマートフォン・タブレット/テレビゲーム/家電/TV・オーディオ・カメラ）
GADGET_GENRES = ["100026", "564500", "101205", "562637", "211742"]

# エンジニア向け食品・飲料系（コーヒー/スナック菓子/栄養補助スナック/ラーメン）
# 社長指示: ガジェットだけでなく、こちらも投稿できるようにする
ENGINEER_FOOD_GENRES = ["100356", "562625", "566807", "110487"]

RAKUTEN_GENRES = GADGET_GENRES + ENGINEER_FOOD_GENRES

POSTED_HISTORY_PATH = pathlib.Path("data/suna_posted_products.json")
POSTED_HISTORY_MAX = 200

# 社長がDiscordで貼ったAmazon商品URLの手動キュー（PA-API未承認のため、
# 楽天のような自動選定は不可。人間が選んだURLをFIFOで消費する）。
# 追加は bot/discord_bot.py の !すなくんAmazon コマンド（GitHub Contents API
# 経由）、消費はここ(pop_amazon_queue_url)で行い、ワークフロー側が
# git commitで永続化する。
AMAZON_QUEUE_PATH = pathlib.Path("data/amazon_product_queue.json")

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
    """楽天ランキングAPIからガジェット系/エンジニア食品系カテゴリの商品を
    ランダムに1件取得。RAKUTEN_APP_ID/RAKUTEN_ACCESS_KEY未設定・API失敗・
    全件投稿済みの場合はNone（呼び出し側は既存のAI生成テーマ投稿にフォールバック）。"""
    app_id = clean_env("RAKUTEN_APP_ID")
    access_key = clean_env("RAKUTEN_ACCESS_KEY")
    if not app_id or not access_key:
        return None

    affiliate_id = clean_env("RAKUTEN_AFFILIATE_ID")
    posted = _load_posted_urls()
    genre_id = random.choice(RAKUTEN_GENRES)

    params = {
        "format": "json",
        "genreId": genre_id,
        "applicationId": app_id,
        "accessKey": access_key,
    }
    if affiliate_id:
        params["affiliateId"] = affiliate_id

    try:
        r = requests.get(
            RANKING_API_URL,
            params=params,
            headers={"Referer": API_REFERER, "Origin": API_REFERER},
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


def load_amazon_queue() -> list[str]:
    if not AMAZON_QUEUE_PATH.exists():
        return []
    try:
        return json.loads(AMAZON_QUEUE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_amazon_queue(urls: list[str]) -> None:
    AMAZON_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    AMAZON_QUEUE_PATH.write_text(json.dumps(urls, ensure_ascii=False, indent=2), encoding="utf-8")


def pop_amazon_queue_url() -> str | None:
    """キューの先頭(最も古く追加されたURL)を取り出して削除する。空ならNone。"""
    urls = load_amazon_queue()
    if not urls:
        return None
    first, rest = urls[0], urls[1:]
    _save_amazon_queue(rest)
    return first


def _extract_meta(html: str, property_name: str) -> str:
    pattern = re.compile(
        rf'<meta[^>]*(?:property|name)=["\']{ re.escape(property_name) }["\'][^>]*content=["\']([^"\']*)["\']',
        re.IGNORECASE,
    )
    m = pattern.search(html)
    if m:
        return m.group(1)
    pattern2 = re.compile(
        rf'<meta[^>]*content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']{ re.escape(property_name) }["\']',
        re.IGNORECASE,
    )
    m2 = pattern2.search(html)
    return m2.group(1) if m2 else ""


def _extract_amazon_title(html: str) -> str:
    m = re.search(r'<span[^>]*id=["\']productTitle["\'][^>]*>(.*?)</span>', html, re.DOTALL)
    return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""


def _extract_amazon_image(html: str) -> str:
    """Amazon商品ページにはog:imageが無いため(実地検証済み)、
    id="imgTagWrapperId" 直下の<img>から取得する。data-old-hiresがあれば
    そちらを優先し、無ければsrcのサイズ指定サフィックス(._AC_SY300_..._)を
    除去して原寸大画像URLにする。"""
    wrapper = re.search(r'id=["\']imgTagWrapperId["\'][^>]*>\s*<img([^>]*)>', html, re.DOTALL)
    if not wrapper:
        return ""
    img_tag = wrapper.group(1)
    hires = re.search(r'data-old-hires=["\']([^"\']+)["\']', img_tag)
    if hires and hires.group(1):
        return hires.group(1)
    src = re.search(r'src=["\']([^"\']+)["\']', img_tag)
    if not src:
        return ""
    return re.sub(r"\._[A-Z0-9,_]+_\.", ".", src.group(1))


def scrape_amazon_product(url: str) -> dict | None:
    """社長がDiscordで貼った実在のAmazon商品URLから、実際の商品名・画像を
    取得する。PA-API未承認（過去30日で対象売上10件が必要、実地確認済み未達）
    のため、軽量スクレイピングで代用。Amazon商品ページにはog:title/og:image
    が存在しないことを実際に確認したため、productTitle span要素と
    imgTagWrapperId配下のimg要素から抽出する（旧GAS実装
    04_suna_product_scraper.gs の_scrapeAmazonと同じ発想をPythonに移植）。

    AMAZON_ASSOCIATE_TAG未設定・取得失敗時はNoneを返す
    （呼び出し側はキューの次のURL、または既存のフォールバックへ）。"""
    associate_tag = clean_env("AMAZON_ASSOCIATE_TAG")
    if not associate_tag:
        return None

    try:
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=20,
        )
        r.raise_for_status()
        html = r.text
    except Exception as e:
        print(f"[product_source] amazon page fetch failed: {e}")
        return None

    title = _extract_amazon_title(html)
    image_url = _extract_amazon_image(html)
    description = _extract_meta(html, "og:description")

    asin_match = re.search(r"/dp/([A-Z0-9]{10})", url, re.IGNORECASE) or re.search(
        r"/gp/product/([A-Z0-9]{10})", url, re.IGNORECASE
    )
    asin = asin_match.group(1) if asin_match else ""
    if not asin or not title:
        print(f"[product_source] amazon scrape incomplete: asin={asin!r} title={title!r}")
        return None

    affiliate_url = f"https://www.amazon.co.jp/dp/{asin}?tag={associate_tag}"

    return {
        "source": "amazon",
        "title": title,
        "price": "商品ページでご確認ください",  # 価格は変動が早くスクレイピング精度が低いため断定しない
        "description": description[:200],
        "item_url": url,
        "affiliate_url": affiliate_url,
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
