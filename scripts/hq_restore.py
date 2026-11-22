"""バックアップ（hq_backup.py が書いたもの）から Firestore へ戻す。既定は「比べるだけ」で、何も書かない。

WF-26 が、謙一が選んだ日のバックアップを kcs-hq の backups ブランチから取ってきて、これを動かす。
- 比べるだけ（既定）: コレクションごとに「無くなっている／変わっている／同じ／バックアップに無い」を数える。
- --apply: 無くなっているものと変わっているものを、バックアップの中身で上書きする。
  バックアップに無いもの（あとから増えたもの）は消さない。数えて知らせるだけ。
- 戻すコレクションは --collections で必ず名前を指定する（全部を一度に戻す近道は作らない）。

【公開リポジトリの決まり】ログに出すのは件数だけ。中身・ドキュメントの ID は出さない。

    python scripts/hq_restore.py --snapshot <フォルダ>/snapshot --collections approvals,ideas
    python scripts/hq_restore.py --snapshot <フォルダ>/snapshot --collections approvals --apply
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from scripts import hq_backup as hb  # noqa: E402


def load_snapshot(snap_dir: str) -> dict:
    """snapshot/ のファイルを読んで {コレクション名: docs} にする。名前はファイルの中の collection を使う。"""
    out = {}
    for fname in sorted(os.listdir(snap_dir)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(snap_dir, fname), encoding="utf-8") as f:
            body = json.load(f)
        out[body["collection"]] = body.get("docs") or {}
    return out


def compare(col, docs: dict, plan: list, path: str = "") -> dict:
    """バックアップの docs と、いまの col を比べる。書くものは plan に (ref, data) で足す。件数を返す。"""
    n = {"missing": 0, "changed": 0, "same": 0, "extra": 0}
    current = {s.id: s for s in col.stream()}
    for doc_id, entry in docs.items():
        ref = col.document(doc_id)
        want = entry.get("data") or {}
        snap = current.get(doc_id)
        if snap is None:
            n["missing"] += 1
            plan.append((ref, want))
        elif hb.encode(snap.to_dict() or {}) != want:
            n["changed"] += 1
            plan.append((ref, want))
        else:
            n["same"] += 1
        for sub_name, sub_docs in (entry.get("sub") or {}).items():
            sub_n = compare(ref.collection(sub_name), sub_docs, plan)
            for k in n:
                n[k] += sub_n[k]
    n["extra"] += sum(1 for doc_id in current if doc_id not in docs)
    return n


def run(db, snapshot: dict, names: list, apply: bool = False) -> dict:
    """指定のコレクションを比べ、apply なら書き戻す。ログは件数だけ。"""
    unknown = [x for x in names if x not in snapshot]
    if unknown:
        print(f"バックアップに無いコレクションが {len(unknown)}件 指定されています。止めます")
        return {"error": "unknown"}
    total = {"missing": 0, "changed": 0, "same": 0, "extra": 0, "written": 0}
    for i, name in enumerate(names, 1):
        plan = []
        n = compare(db.collection(name), snapshot[name], plan)
        print(
            f"{i}つめ: 無くなっている {n['missing']}件・変わっている {n['changed']}件・"
            f"同じ {n['same']}件・バックアップに無い {n['extra']}件（消しません）"
        )
        for k in n:
            total[k] += n[k]
        if apply and plan:
            batch, size = db.batch(), 0
            for ref, data in plan:
                batch.set(ref, hb.decode(data, db))
                size += 1
                if size == 400:  # 1回にまとめて書けるのは500件まで
                    batch.commit()
                    batch, size = db.batch(), 0
            if size:
                batch.commit()
            total["written"] += len(plan)
    if apply:
        print(f"書き戻しました: {total['written']}件")
    else:
        print(f"比べただけです。戻すなら {total['missing'] + total['changed']}件 を上書きします")
    return total


def main():
    p = argparse.ArgumentParser(description="バックアップから Firestore へ戻す（既定は比べるだけ）")
    p.add_argument("--snapshot", required=True, help="バックアップの snapshot フォルダ")
    p.add_argument("--collections", required=True, help="戻すコレクション（カンマ区切り）")
    p.add_argument("--apply", action="store_true", help="本当に書き戻す")
    a = p.parse_args()
    names = [x.strip() for x in a.collections.split(",") if x.strip()]
    if not names:
        print("戻すコレクションを指定してください")
        sys.exit(1)
    db = hb.firestore_client()
    if db is None:
        print("FIREBASE_SERVICE_ACCOUNT_JSON がありません")
        sys.exit(1)
    res = run(db, load_snapshot(a.snapshot), names, apply=a.apply)
    if "error" in res:
        sys.exit(1)


if __name__ == "__main__":
    main()
