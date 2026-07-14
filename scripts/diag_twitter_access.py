"""
診断用: HAL/SUNAKUN の X トークンの実権限とAPI書き込み可否を確認する。
投稿はしない（副作用なし）。GitHub Actions の workflow_dispatch から実行する想定。

確認内容:
1. v1.1 account/verify_credentials のレスポンスヘッダ x-access-level
   → "read" なら書き込み権限なし（アプリ設定 or トークン再生成の問題）
   → "read-write" なら権限はある（=403の原因は使用量/プラン/課金側）
2. v2 POST /2/tweets を dry-run 相当で叩けるかは投稿になるので行わず、
   GET /2/users/me で認証自体の健全性のみ確認。
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from requests_oauthlib import OAuth1Session
from scripts.common.env_clean import clean_env


def check(prefix: str) -> None:
    print(f"\n===== {prefix} =====")
    ck = clean_env(f"{prefix}_TWITTER_API_KEY")
    cs = clean_env(f"{prefix}_TWITTER_API_SECRET")
    at = clean_env(f"{prefix}_TWITTER_ACCESS_TOKEN")
    ats = clean_env(f"{prefix}_TWITTER_ACCESS_SECRET")
    if not all([ck, cs, at, ats]):
        print("  !! 認証情報が揃っていない (空のSecretあり)")
        return

    sess = OAuth1Session(ck, cs, at, ats)

    # 1) x-access-level（トークンの実権限）
    r = sess.get("https://api.twitter.com/1.1/account/verify_credentials.json")
    lvl = r.headers.get("x-access-level", "(ヘッダ無し)")
    print(f"  verify_credentials: HTTP {r.status_code}")
    print(f"  >>> x-access-level = {lvl}   "
          f"({'★書き込み不可=権限問題' if lvl == 'read' else '書き込み権限あり' if 'write' in str(lvl) else '不明'})")
    if r.status_code == 200:
        try:
            j = r.json()
            print(f"  screen_name=@{j.get('screen_name')}  id={j.get('id_str')}")
        except Exception:
            pass
    else:
        print(f"  body: {r.text[:300]}")

    # 2) v2 認証健全性（GET /2/users/me）
    r2 = sess.get("https://api.twitter.com/2/users/me")
    print(f"  GET /2/users/me: HTTP {r2.status_code}  body={r2.text[:200]}")


if __name__ == "__main__":
    for p in ("HAL", "SUNAKUN"):
        try:
            check(p)
        except Exception as e:
            print(f"  例外: {e}")
