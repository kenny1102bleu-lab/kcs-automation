"""マネージャーごとの Discord チャンネルを作り、対応表（Firestore の knowledge/discord）を残す。

- 名簿（Firestore の ai_staff）のマネージャーごとに1チャンネル＋全体の「総合」を、カテゴリ「KCS 事業」の中に作る。
- すでにあるチャンネルは作らない（何度実行しても増えない）。
- 既定は dry-run（何も作らない）。作るときは --apply。
- ログに出すのは件数だけ（名前・ID は出さない。公開リポジトリのログは誰でも読めるため）。

    python scripts/hq_discord_channels.py            # 確認だけ
    python scripts/hq_discord_channels.py --apply    # 作る
"""
import argparse
import datetime as dt
import sys

from scripts import hq_discord as hd


def run(db, apply: bool, post=None) -> int:
    post = post or hd.post_to_bot
    staff = [d.to_dict() | {"id": d.id} for d in db.collection("ai_staff").stream()]
    names_by_id = hd.manager_channel_names(staff)
    managers = len(names_by_id) - 1
    print(f"[チャンネル] マネージャー {managers}人 ＋ 総合 → {len(names_by_id)}チャンネルの予定")
    if managers == 0:
        print("[チャンネル] 名簿にマネージャーがいません。先に初期データを入れてください")
        return 1
    if not apply:
        print("[チャンネル] 確認だけです（作るときは --apply）")
        return 0

    res = post({"action": "create_channels", "category": hd.CATEGORY, "names": list(names_by_id.values())})
    if "error" in res:
        reason = {
            "forbidden": "ボットに『チャンネルの管理』の権限がありません",
            "bot_not_ready": "ボットがまだ起動していません（少し待って再実行）",
            "no_guild": "ボットがサーバーに入っていません",
        }.get(res["error"], res["error"])
        print(f"[チャンネル] 作れませんでした: {reason}")
        return 1
    got = res.get("channels", {})
    mapping = {}
    for key, name in names_by_id.items():
        if name in got:
            mapping[key] = got[name]
    missing = len(names_by_id) - len(mapping)
    if missing:
        print(f"[チャンネル] {missing}チャンネルの ID が返りませんでした。対応表は書きません")
        return 1
    db.collection(hd.DOC[0]).document(hd.DOC[1]).set({
        "category": hd.CATEGORY,
        "channels": mapping,
        "updatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "entityId": "corp",
        "businessId": "company_ops",
    })
    print(f"[チャンネル] {len(mapping)}チャンネルを用意し、対応表を Firestore に書きました")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    db = hd.firestore_client()
    if db is None:
        print("[チャンネル] FIREBASE_SERVICE_ACCOUNT_JSON がありません")
        return 1
    return run(db, args.apply)


if __name__ == "__main__":
    sys.exit(main())
