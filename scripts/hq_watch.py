"""見張り（WF-22）: GitHub Actions の失敗と、止められた定時実行を、Discord の「システム」チャンネルへ知らせる。

- ワークフローの一覧は持たない（GitHub に聞く）。あとから増えたワークフローも、そのまま見張る。
- 失敗（failure・timed_out・startup_failure）を知らせる。同じワークフローは、6時間に1回まで（15分ごとの仕事が失敗し続けても、うるさくしない）。
- GitHub は、公開リポジトリの定時実行を、60日動きがないと止める（disabled_inactivity）。止められたものも知らせる。
- 知らせた記録は Firestore の system_watch/actions（非公開）。二度送らない。
- 送り先は、Discord の対応表（knowledge/discord）の mgr_system（システム部）。無ければ総合。
- 費用は0円（AIを使わない）。ログに出すのは件数だけ。

    python scripts/hq_watch.py [--dry-run]
"""
import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import hq_discord as hd  # noqa: E402

FAIL = ("failure", "timed_out", "startup_failure")
STATE = ("system_watch", "actions")
QUIET_HOURS = 6  # 同じワークフローの知らせは、この時間に1回まで
LOOKBACK_HOURS = 24
KEEP_IDS = 300
JST = dt.timezone(dt.timedelta(hours=9))
LABEL = {"failure": "失敗", "timed_out": "時間切れ", "startup_failure": "起動できず"}


def github_fetch(path: str, params: dict | None = None) -> dict:
    import requests

    repo = os.environ.get("GITHUB_REPOSITORY", "kenny1102bleu-lab/kcs-automation")
    r = requests.get(
        f"https://api.github.com/repos/{repo}{path}",
        headers={"Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN', '')}", "Accept": "application/vnd.github+json"},
        params=params or {}, timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _jst(iso: str) -> str:
    try:
        t = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(JST)
        return f"{t.month}/{t.day} {t.hour}:{t.minute:02d}"
    except ValueError:
        return ""


def message_of(groups: dict, disabled: list[str]) -> str:
    """groups: {ワークフロー名: [失敗した実行（新しい順）]}。"""
    lines = ["**見張り**: GitHub Actions に止まった仕事があります"]
    for name, runs in groups.items():
        last = runs[0]
        more = f"（ほか {len(runs) - 1}回）" if len(runs) > 1 else ""
        lines.append(f"- {name}: {LABEL.get(last.get('conclusion'), last.get('conclusion'))}・{_jst(last.get('created_at', ''))}{more}\n  {last.get('html_url', '')}")
    for name in disabled:
        lines.append(f"- {name}: 定時実行が GitHub に止められました（60日動きがなかったため）。Actions の画面で「Enable workflow」を押すと戻ります")
    lines.append("→ Claude Code に「見張りの失敗を直して」と頼んでください")
    return "\n".join(lines)


def run(db, dry_run: bool = False, fetch=None, send=None, now=None) -> dict:
    fetch = fetch or github_fetch
    send = send or hd.send_to_channel
    now = now or dt.datetime.now(dt.timezone.utc)
    ref = db.collection(STATE[0]).document(STATE[1])
    snap = ref.get()
    state = (snap.to_dict() or {}) if snap.exists else {}
    notified = set(state.get("notifiedIds") or [])
    last_by = dict(state.get("lastByWorkflow") or {})
    known_disabled = set(state.get("disabledNotified") or [])

    since = (now - dt.timedelta(hours=LOOKBACK_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    runs = []
    for c in FAIL:
        runs += fetch("/actions/runs", {"status": c, "created": f">={since}", "per_page": 100}).get("workflow_runs", [])
    fresh = sorted((r for r in runs if r.get("id") not in notified), key=lambda r: r.get("created_at", ""), reverse=True)

    groups, quiet = {}, 0
    cutoff = (now - dt.timedelta(hours=QUIET_HOURS)).isoformat()
    for r in fresh:
        name = r.get("name") or "（名前なし）"
        if last_by.get(name, "") >= cutoff and name not in groups:
            quiet += 1
            continue
        groups.setdefault(name, []).append(r)

    workflows = fetch("/actions/workflows", {"per_page": 100}).get("workflows", [])
    disabled_now = sorted(w.get("name", "") for w in workflows if w.get("state") == "disabled_inactivity")
    disabled_new = [n for n in disabled_now if n not in known_disabled]

    sent = False
    if groups or disabled_new:
        doc = db.collection(hd.DOC[0]).document(hd.DOC[1]).get()
        channels = hd.effective_channels(doc.to_dict() or {}) if doc.exists else {}
        channel = channels.get("mgr_system") or channels.get(hd.GENERAL)
        if not channel:
            print("[見張り] Discord の対応表に、システム部と総合のチャンネルがありません")
            return {"ok": False, "reason": "no_channel", "failed": len(fresh)}
        if dry_run:
            sent = True
        else:
            sent = send(channel, message_of(groups, disabled_new))
    if not dry_run and (sent or not (groups or disabled_new)):
        ids = [r["id"] for r in fresh if "id" in r] + [i for i in state.get("notifiedIds") or [] if i not in {r.get("id") for r in fresh}]
        for name in groups:
            last_by[name] = now.isoformat()
        ref.set({
            "notifiedIds": ids[:KEEP_IDS], "lastByWorkflow": last_by,
            "disabledNotified": disabled_now, "checkedAt": now.isoformat(),
        })
    tail = "（dry-run: 送っていません）" if dry_run else ""
    print(f"[見張り] 新しい失敗 {len(fresh)}件（知らせる {sum(len(v) for v in groups.values())}件・{len(groups)}本／6時間内で控える {quiet}件）"
          f"・止められた定時実行 {len(disabled_now)}本（新しく {len(disabled_new)}本）・送信 {'あり' if sent else 'なし'}{tail}")
    return {"ok": True, "failed": len(fresh), "workflows": len(groups), "quiet": quiet, "disabled": len(disabled_new), "sent": sent}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    db = hd.firestore_client()
    if db is None:
        print("[見張り] FIREBASE_SERVICE_ACCOUNT_JSON がありません")
        return 1
    return 0 if run(db, dry_run=args.dry_run)["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
