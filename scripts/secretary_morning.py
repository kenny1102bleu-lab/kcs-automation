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
承認待ち・事業カードの数字は Firestore 接続後に足す。今は「未接続」と正直に書く。

環境変数: GITHUB_TOKEN, GITHUB_REPOSITORY, DISCORD_WEBHOOK_URL(S)。DRY_RUN=1 なら送らず標準出力だけ。
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


def build(now: dt.datetime) -> str:
    fails = failed_runs(now)
    decided = recent_decisions(now)
    lines = [f"🗂️ **秘書室 朝の報告** {now.strftime('%m/%d')}"]
    lines.append("**1. 承認待ち** 未接続（Firestore をつなぐと件数と待ち日数が出ます）")
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
    return "\n".join(lines)


def run() -> None:
    msg = build(dt.datetime.now(JST))
    print(msg)
    if os.environ.get("DRY_RUN") == "1":
        print("[DRY_RUN] 送信しません")
        return
    from scripts.common.discord_notify import notify
    notify(msg, channel="report")


if __name__ == "__main__":
    run()
