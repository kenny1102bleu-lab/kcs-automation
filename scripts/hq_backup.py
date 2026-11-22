"""中枢（kcs-hq の Firestore）のバックアップ。全部のコレクションを JSON にして書き出す。

WF-25 が毎日これを動かし、書き出したものを非公開の kcs-hq の backups ブランチへコミットする。
毎日のコミットがそのまま「その日の中身」になる（戻すときは日付のコミットを選ぶ）。戻すのは hq_restore.py。

【公開リポジトリの決まり】このリポジトリは公開で、GitHub Actions のログは誰でも読める。
- ログに出すのは件数だけ。コレクションの中身・ドキュメントの ID は出さない。
- 書き出したファイルはランナーの中（--out）にだけ置く。アーティファクトにはしない。
  kcs-hq へのコミットとプッシュはワークフローの git が行う（鍵は KCS_HQ_BACKUP_TOKEN。値は表示しない）。

ファイルの形（--out の下）:
    snapshot/<コレクション>.json   {"collection": 名前, "docs": {ID: {"data": {...}, "sub": {サブコレクション: {...}}}}}
    manifest.json                   {"version", "takenAt", "collections", "documents", "counts": {コレクション: 件数}}
日時・参照・位置・バイト列は {"__type": ...} の印を付けて残し、戻すときに元の型へ直す。

    python scripts/hq_backup.py --out <フォルダ>            # 書き出す
    python scripts/hq_backup.py --out <フォルダ> --dry-run  # 数えるだけ（書かない）
"""
import argparse
import base64
import datetime
import json
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from scripts.common.env_clean import clean_env  # noqa: E402

VERSION = 1
SNAPSHOT_DIR = "snapshot"
MANIFEST = "manifest.json"
TYPE_KEY = "__type"
DROP_RATIO = 0.5  # 前回より半分以上減ったら知らせる（消えすぎの見張り）
DROP_MIN = 20  # 前回がこれより少ないときは見張らない（始めたばかりは増減が大きい）
READ_WARN = 40000  # 1日の無料の読み取りは5万件。近づいたら知らせる


def firestore_client():
    """Firestore を開く。鍵が無ければ None。値は表示しない。"""
    raw = clean_env("FIREBASE_SERVICE_ACCOUNT_JSON")
    if not raw:
        return None
    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(json.loads(raw)))
    return firestore.client()


# ---- 型の印 ----

def encode(v):
    """Firestore の値を JSON にできる形へ。日時・参照・位置・バイト列には印を付ける。"""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return {TYPE_KEY: "float", "value": repr(v)}
        return v
    if isinstance(v, datetime.datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=datetime.timezone.utc)
        return {TYPE_KEY: "timestamp", "value": v.astimezone(datetime.timezone.utc).isoformat()}
    if isinstance(v, (bytes, bytearray)):
        return {TYPE_KEY: "bytes", "value": base64.b64encode(bytes(v)).decode("ascii")}
    if isinstance(v, (list, tuple)):
        return [encode(x) for x in v]
    if isinstance(v, dict):
        out = {str(k): encode(x) for k, x in v.items()}
        if TYPE_KEY in out:  # 中身にたまたま同じ名前の欄があっても、印と取り違えない
            return {TYPE_KEY: "map", "value": out}
        return out
    kind = type(v).__name__
    if kind == "DocumentReference" or (hasattr(v, "path") and hasattr(v, "collection")):
        return {TYPE_KEY: "ref", "value": v.path}
    if hasattr(v, "latitude") and hasattr(v, "longitude"):
        return {TYPE_KEY: "geo", "lat": v.latitude, "lng": v.longitude}
    raise TypeError(f"バックアップできない型: {kind}")


def decode(v, db=None):
    """encode の逆。参照を戻すには db が要る。"""
    if isinstance(v, list):
        return [decode(x, db) for x in v]
    if not isinstance(v, dict):
        return v
    kind = v.get(TYPE_KEY)
    if kind is None:
        return {k: decode(x, db) for k, x in v.items()}
    if kind == "map":
        return {k: decode(x, db) for k, x in v["value"].items()}
    if kind == "timestamp":
        return datetime.datetime.fromisoformat(v["value"])
    if kind == "bytes":
        return base64.b64decode(v["value"])
    if kind == "float":
        return float(v["value"])
    if kind == "ref":
        if db is None:
            raise ValueError("参照を戻すには db が要る")
        return db.document(v["value"])
    if kind == "geo":
        from google.cloud.firestore import GeoPoint

        return GeoPoint(v["lat"], v["lng"])
    raise ValueError(f"知らない印: {kind}")


# ---- 読み出し ----

def dump_collection(col, pool) -> dict:
    """1つのコレクションを {ID: {"data", "sub"}} にする。サブコレクションも深くたどる。"""
    snaps = list(col.stream())
    subs = list(pool.map(lambda s: list(s.reference.collections()), snaps))
    docs = {}
    for snap, sub_cols in zip(snaps, subs):
        entry = {"data": encode(snap.to_dict() or {})}
        if sub_cols:
            entry["sub"] = {c.id: dump_collection(c, pool) for c in sub_cols}
        docs[snap.id] = entry
    return docs


def count_docs(docs: dict) -> int:
    n = 0
    for entry in docs.values():
        n += 1
        for sub in (entry.get("sub") or {}).values():
            n += count_docs(sub)
    return n


def take_snapshot(db, now=None) -> dict:
    """全部のコレクションを読む。{"takenAt", "collections": {名前: docs}}。"""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cols = {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        for col in db.collections():
            cols[col.id] = dump_collection(col, pool)
    return {"takenAt": now.isoformat(), "collections": cols}


# ---- 書き出し ----

def manifest_of(snapshot: dict) -> dict:
    counts = {name: count_docs(docs) for name, docs in sorted(snapshot["collections"].items())}
    return {
        "version": VERSION,
        "takenAt": snapshot["takenAt"],
        "collections": len(counts),
        "documents": sum(counts.values()),
        "counts": counts,
    }


def read_manifest(out_dir: str) -> dict:
    try:
        with open(os.path.join(out_dir, MANIFEST), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def dropped(prev: dict, cur: dict) -> bool:
    """前回より大きく減ったか（消えすぎの見張り）。"""
    before = int(prev.get("documents") or 0)
    if before < DROP_MIN:
        return False
    return cur["documents"] < before * (1 - DROP_RATIO)


def safe_name(name: str) -> str:
    """コレクション名をファイル名にする。Firestore の名前に / は入らないが、念のため外へ出られないようにする。"""
    bad = '<>:"/\\|?*'
    out = "".join("_" if c in bad or ord(c) < 32 else c for c in name)
    return out if out not in ("", ".", "..") else "_" + out


def write_snapshot(out_dir: str, snapshot: dict) -> dict:
    """snapshot/ を作り直して、コレクションごとのファイルと manifest.json を書く。"""
    snap_dir = os.path.join(out_dir, SNAPSHOT_DIR)
    if os.path.isdir(snap_dir):
        shutil.rmtree(snap_dir)  # 消えたコレクションのファイルを残さない（前の日の分は git の履歴にある）
    os.makedirs(snap_dir)
    used = set()
    for name, docs in sorted(snapshot["collections"].items()):
        fname = safe_name(name)
        while fname.lower() in used:  # 大文字小文字だけ違う名前を、Windows で開いても上書きしない
            fname += "_"
        used.add(fname.lower())
        body = {"collection": name, "docs": docs}
        with open(os.path.join(snap_dir, fname + ".json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(body, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
    man = manifest_of(snapshot)
    with open(os.path.join(out_dir, MANIFEST), "w", encoding="utf-8", newline="\n") as f:
        json.dump(man, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    return man


def set_output(key: str, value: str):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def run(db, out_dir: str, dry_run: bool = False, now=None) -> dict:
    """読んで書く。ログは件数だけ。戻り値は {"collections", "documents", "dropped"}。"""
    prev = read_manifest(out_dir)
    snapshot = take_snapshot(db, now)
    man = manifest_of(snapshot)
    drop = dropped(prev, man)
    print(f"読んだもの: コレクション {man['collections']}件・ドキュメント {man['documents']}件")
    if prev:
        print(f"前回: ドキュメント {int(prev.get('documents') or 0)}件")
    if man["documents"] > READ_WARN:
        print(f"注意: 1回で {man['documents']}件 読んでいます（無料の枠は1日5万件）")
    if drop:
        print("注意: 前回より半分以上減っています。消えすぎていないか確かめてください")
    if dry_run:
        print("数えるだけなので、書いていません")
    else:
        write_snapshot(out_dir, snapshot)
        print("書き出しました")
    set_output("dropped", "true" if drop else "false")
    return {"collections": man["collections"], "documents": man["documents"], "dropped": drop}


def main():
    p = argparse.ArgumentParser(description="中枢の Firestore を JSON に書き出す")
    p.add_argument("--out", required=True, help="書き出すフォルダ（kcs-hq の backups ブランチを取ってきた場所）")
    p.add_argument("--dry-run", action="store_true", help="数えるだけで書かない")
    a = p.parse_args()
    db = firestore_client()
    if db is None:
        print("FIREBASE_SERVICE_ACCOUNT_JSON がありません")
        sys.exit(1)
    os.makedirs(a.out, exist_ok=True)
    run(db, a.out, dry_run=a.dry_run)


if __name__ == "__main__":
    main()
