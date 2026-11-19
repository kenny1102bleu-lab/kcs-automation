"""
秘書室: 毎朝8時の報告（WF-15）。

方針（kcs-hq/departments/secretary/CLAUDE.md・2026-11-17 決定）:
- 長くしない。数字と承認待ちだけ。変化が無い日は「変化なし」の一行。
- 失敗したことは必ず入れる。
- AntiGravity や Claude API に依存しない。ここは機械的に集めて並べるだけ（利用枠が読めないため）。
  AIを使う要約は、使えるようになってから足す（足すときは月上限15ドルを守る）。

読むもの:
- kcs-automation 自身の GitHub Actions の失敗（GITHUB_TOKEN。鍵の追加は不要）
- kcs-hq の decisions.md（ワークフロー側で hq/ にチェックアウトできたときだけ）
承認待ち（kcs-hq の approvals コレクション）は FIREBASE_SERVICE_ACCOUNT_JSON があれば Firestore から読む。
無いときは「未接続」と正直に書く。読めなかったときは失敗に入れる（黙って消さない）。
事業カードの数字は、数字の取得元をつなぐまで未接続のまま。

環境変数: GITHUB_TOKEN, GITHUB_REPOSITORY, DISCORD_WEBHOOK_URL(S), FIREBASE_SERVICE_ACCOUNT_JSON（任意）。
DRY_RUN=1 なら送らず、何も送らない（Actions 上では中身もログに出さない。手元の実行なら標準出力に出す）。
"""
import os
import re
import sys
import datetime as dt

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from scripts.common.env_clean import clean_env  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))


def failed_runs(now: dt.datetime) -> list[str]:
    """直近24時間で失敗したワークフロー。取得できないときは、その事実を返す（黙って消さない）。"""
    token = clean_env("GITHUB_TOKEN")
    repo = clean_env("GITHUB_REPOSITORY") or "kenny1102bleu-lab/kcs-automation"
    if not token:
        return ["失敗の確認ができていません（GITHUB_TOKEN なし）"]
    since = (now - dt.timedelta(hours=24)).astimezone(dt.timezone.utc)
    try:
        r = requests.get(
            f"https://api.github.com/repos/{repo}/actions/runs",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            params={"status": "failure", "per_page": 50},
            timeout=30,
        )
        r.raise_for_status()
    except Exception as e:  # 取得失敗そのものを報告に出す
        return [f"失敗の確認に失敗しました（{type(e).__name__}）"]
    out = []
    for run in r.json().get("workflow_runs", []):
        created = dt.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        if created >= since:
            t = created.astimezone(JST).strftime("%m/%d %H:%M")
            out.append(f"{run['name']}（{t}）{run['html_url']}")
    return out


def recent_decisions(now: dt.datetime) -> list[str]:
    """昨日と今日の decisions.md の追記見出し。hq/ が無ければ None 相当で空を返す。"""
    path = os.path.join("hq", "decisions.md")
    if not os.path.exists(path):
        return ["（decisions.md を読めませんでした）"]
    days = {now.strftime("%Y-%m-%d"), (now - dt.timedelta(days=1)).strftime("%Y-%m-%d")}
    heads = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^##\s+(.+?)[（(](\d{4}-\d{2}-\d{2})[）)]\s*(.*)$", line.strip())
            if m and m.group(2) in days:
                heads.append(f"{m.group(1)} {m.group(3)}".strip())
    return heads


def summarize_approvals(rows: list[dict], now: dt.datetime) -> str:
    """承認待ちの行から、件数・いちばん古い待ち日数・先頭の数件を1つの文にする（純粋関数）。"""
    if not rows:
        return "**1. 承認待ち** なし"
    today = now.date()

    def waited(r: dict) -> int:
        created = str(r.get("createdAt") or "")[:10]
        try:
            return max(0, (today - dt.date.fromisoformat(created)).days)
        except ValueError:
            return 0

    oldest = max(waited(r) for r in rows)
    ordered = sorted(rows, key=waited, reverse=True)[:5]
    head = f"**1. 承認待ち** {len(rows)}件（いちばん古いもので {oldest}日待ち）"
    items = [f"- {r.get('department', '')}：{r.get('title', '')}" for r in ordered]
    if len(rows) > len(ordered):
        items.append(f"- ほか {len(rows) - len(ordered)} 件は中枢アプリの「秘書室」")
    return "\n".join([head, *items])


def firestore_client():
    """Firestore を開く。鍵（FIREBASE_SERVICE_ACCOUNT_JSON）が無ければ None。値は表示しない。"""
    raw = clean_env("FIREBASE_SERVICE_ACCOUNT_JSON")
    if not raw:
        return None
    import json

    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(json.loads(raw)))
    return firestore.client()


def waiting_approvals(now: dt.datetime) -> tuple[str, list[str], int | None]:
    """Firestore の承認待ち。(報告の行, 失敗の一覧, 件数) を返す。鍵が無ければ未接続と書く（件数は None）。"""
    try:
        db = firestore_client()
        if db is None:
            return "**1. 承認待ち** 未接続（FIREBASE_SERVICE_ACCOUNT_JSON を GitHub Secrets に置くと件数と待ち日数が出ます）", [], None
        docs = db.collection("approvals").where("status", "==", "待ち").stream()
        rows = [d.to_dict() for d in docs]
    except Exception as e:  # 鍵の値は出さない。種類と説明文（先頭200字）だけ報告に出す
        return "**1. 承認待ち** 読めませんでした", [f"承認待ちの取得に失敗しました（{type(e).__name__}: {str(e)[:200]}）"], None
    return summarize_approvals(rows, now), [], len(rows)


def record_activity(now: dt.datetime, waiting_count: int | None) -> None:
    """「朝の報告を送った」を、中枢アプリのオフィスの「社内の動き」（Firestore の activity）に残す。
    記録できなくても報告は送り済みなので止めない。ただし失敗は黙らず、ログに出す。"""
    try:
        db = firestore_client()
        if db is None:
            return
        tail = f"（承認待ち {waiting_count}件）" if waiting_count is not None else ""
        db.collection("activity").document(f"act_morning_{now.strftime('%Y%m%d')}").set({
            "at": now.astimezone(dt.timezone.utc).isoformat(),
            "staffId": "sakura",
            "kind": "done",
            "text": f"朝の報告を送った{tail}",
            "entityId": "corp",
            "businessId": "company_ops",
        })
    except Exception as e:
        print(f"[activity] 記録に失敗しました（{type(e).__name__}: {str(e)[:200]}）")


def build(now: dt.datetime) -> tuple[str, int | None]:
    fails = failed_runs(now)
    approvals_line, approval_fails, waiting_count = waiting_approvals(now)
    fails = fails + approval_fails
    decided = recent_decisions(now)
    lines = [f"🗂️ **秘書室 朝の報告** {now.strftime('%m/%d')}"]
    lines.append(approvals_line)
    lines.append("**2. 事業カード** 案件 / 占い / 既存のお客さん … 未接続（数字の取得元をつなぐまで空）")
    lines.append("**3. 昨日からの変化**")
    if decided:
        lines += [f"- {d}" for d in decided[-5:]]  # 長くしない。新しい5件まで
        if len(decided) > 5:
            lines.append(f"- ほか {len(decided) - 5} 件は decisions.md")
    else:
        lines.append("- 変化なし")
    lines.append("**4. 失敗**")
    lines += [f"- {x}" for x in fails] if fails else ["- なし"]
    return "\n".join(lines), waiting_count


def run() -> None:
    now = dt.datetime.now(JST)
    msg, waiting_count = build(now)
    # このリポジトリは公開。GitHub Actions の実行ログは誰でも読める。
    # 報告の中身（承認待ちの題名＝取引先名・金額など）は、Actions のログに出さない（Discord にだけ送る）。
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"[報告] 組み立て完了（{len(msg)}文字・承認待ち {waiting_count if waiting_count is not None else '不明'}件）。中身はログに出しません")
    else:
        print(msg)
    if os.environ.get("DRY_RUN") == "1":
        print("[DRY_RUN] 送信しません")
        return
    from scripts.common.discord_notify import notify
    notify(msg, channel="report")
    record_activity(now, waiting_count)


if __name__ == "__main__":
    run()
