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

import base64
import requests
from requests_oauthlib import OAuth1Session
from scripts.common.env_clean import clean_env


def _app_only_bearer(ck: str, cs: str) -> str:
    """consumer key/secret から OAuth2 App-Only Bearer を発行（別Secret不要）。"""
    tok = base64.b64encode(f"{ck}:{cs}".encode()).decode()
    r = requests.post(
        "https://api.twitter.com/oauth2/token",
        headers={"Authorization": f"Basic {tok}",
                 "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        data={"grant_type": "client_credentials"},
    )
    if r.status_code == 200:
        return r.json().get("access_token", "")
    print(f"  bearer発行失敗: HTTP {r.status_code} {r.text[:200]}")
    return ""


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

    # 3) ★プロジェクトの使用量/プラン上限（月間cap）。App-Only Bearer必須。
    #    project_usage が project_cap に達していれば投稿(POST /2/tweets)が403になる。
    bearer = _app_only_bearer(ck, cs)
    if bearer:
        r3 = requests.get("https://api.twitter.com/2/usage/tweets",
                          headers={"Authorization": f"Bearer {bearer}"})
        print(f"  GET /2/usage/tweets: HTTP {r3.status_code}  body={r3.text[:500]}")


if __name__ == "__main__":
    for p in ("HAL", "SUNAKUN"):
        try:
            check(p)
        except Exception as e:
            print(f"  例外: {e}")
