"""
承認待ち投稿の永続化。
Bot再起動時に消えないようにGitHub Gist（secret gist）に保存。

環境変数:
  GH_PAT - 既存のGitHub Personal Access Token (gist scope必要)
  PENDING_GIST_ID - 既存のGist ID（なければ初回起動時に作成）
"""
import json
import os
import time
import threading
import requests


GIST_FILENAME = "kcs_pending_approvals.json"
GIST_API = "https://api.github.com/gists"


class PendingStore:
    def __init__(self):
        self.token = os.environ.get("GH_PAT", "")
        self.gist_id = os.environ.get("PENDING_GIST_ID", "")
        self._lock = threading.Lock()
        self._cache: dict = {}
        if self.token:
            self._load()

    def _headers(self):
        return {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
        }

    def _load(self):
        if not self.gist_id:
            return
        try:
            r = requests.get(f"{GIST_API}/{self.gist_id}", headers=self._headers(), timeout=10)
            if r.status_code == 200:
                f = r.json().get("files", {}).get(GIST_FILENAME)
                if f and f.get("content"):
                    self._cache = json.loads(f["content"])
                    # 期限切れ掃除
                    now = time.time()
                    self._cache = {k: v for k, v in self._cache.items()
                                   if v.get("expires_at", 0) > now}
                    print(f"[pending_store] loaded {len(self._cache)} entries from Gist", flush=True)
        except Exception as e:
            print(f"[pending_store] load failed: {e}", flush=True)

    def _save(self):
        if not self.token:
            print("[pending_store] save skipped: GH_PAT not set", flush=True)
            return
        body = json.dumps(self._cache, ensure_ascii=False)
        try:
            if self.gist_id:
                r = requests.patch(
                    f"{GIST_API}/{self.gist_id}",
                    headers=self._headers(),
                    json={"files": {GIST_FILENAME: {"content": body}}},
                    timeout=10,
                )
                if r.status_code != 200:
                    print(f"[pending_store] gist update failed: HTTP {r.status_code} {r.text[:200]}", flush=True)
            else:
                r = requests.post(
                    GIST_API,
                    headers=self._headers(),
                    json={
                        "description": "KCS pending approvals",
                        "public": False,
                        "files": {GIST_FILENAME: {"content": body}},
                    },
                    timeout=10,
                )
                if r.status_code == 201:
                    self.gist_id = r.json()["id"]
                    print(f"[pending_store] created gist {self.gist_id} — register PENDING_GIST_ID env var to persist across restarts", flush=True)
                else:
                    print(f"[pending_store] gist create failed: HTTP {r.status_code} {r.text[:200]}", flush=True)
        except Exception as e:
            print(f"[pending_store] save failed: {e}", flush=True)

    def set(self, approval_id: str, post_text: str, account: str,
            media_path: str = "", media_type: str = "none", ttl_sec: int = 1800,
            affiliate_link: str = ""):
        with self._lock:
            self._cache[approval_id] = {
                "post_text": post_text,
                "account": account,
                "media_path": media_path,
                "media_type": media_type,
                "affiliate_link": affiliate_link,
                "expires_at": time.time() + ttl_sec,
            }
            self._save()

    def set_product_proposal(self, approval_id: str, url: str, title: str,
                              affiliate_url: str, ttl_sec: int = 1800):
        """Amazon商品の事前承認提案を保存する（kind="amazon_product"）。
        既存のpost承認（kind省略時は"post"扱い）とは別種のデータとして
        pop/all側で判別できるようにする。"""
        with self._lock:
            self._cache[approval_id] = {
                "kind": "amazon_product",
                "url": url,
                "title": title,
                "affiliate_url": affiliate_url,
                "expires_at": time.time() + ttl_sec,
            }
            self._save()

    def pop(self, approval_id: str):
        with self._lock:
            data = self._cache.pop(approval_id, None)
            if data:
                self._save()
            return data

    def all(self) -> dict:
        with self._lock:
            now = time.time()
            return {k: v for k, v in self._cache.items() if v.get("expires_at", 0) > now}

    def cleanup_expired(self):
        with self._lock:
            now = time.time()
            before = len(self._cache)
            self._cache = {k: v for k, v in self._cache.items() if v.get("expires_at", 0) > now}
            if len(self._cache) != before:
                self._save()


_store = PendingStore()


def get_store() -> PendingStore:
    return _store
