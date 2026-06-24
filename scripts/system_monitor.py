"""
WF-06: システム監視フロー（ケンジ自己修復・完全自律プロトコル）

【意思決定フロー】
  1. Knowledge/ にナレッジがあれば → 即適用、AI推論スキップ
  2. なければ → ケンジ(Claude)に解析依頼
  3. パッチ生成 → NGパターン監査 → 適用
  4. FULL_AUTO_MODE=TRUE: 即master push & 事後報告
     FULL_AUTO_MODE=FALSE: PR作成→Discordに!approve_patch通知
  5. 成功した修復をKnowledge/に保存
"""
import sys
import os
import json
import subprocess
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.common.claude_client import call_claude
from scripts.common.discord_notify import notify
from scripts.common.knowledge import find_similar, save_incident, increment_applied_count
from scripts.common.ng_patterns import scan as ng_scan


KENJI_PROMPT = """あなたはKCS合同会社の自律型AIインフラエンジニア「ケンジ」です。
GitHub Actions、Discord Bot、GAS、Render、Firebase等のエラー通知を解析し自己修復します。

【修復の優先順位】
第一段階: SNS投稿関連エラー → OAuthトークンリフレッシュ提案（patches空, oauth_refresh=true）
第二段階: それ以外 → コード/YAML/設定の具体的パッチ生成

【出力JSON厳守、説明文なし】
{
  "system": "GitHub Actions / Discord Bot / GAS / Render 等",
  "severity": "高 / 中 / 低",
  "reason": "原因の分かりやすい説明",
  "solution": "対策概要",
  "oauth_refresh": false,
  "patches": [
    {"file": "対象ファイル相対パス", "old": "修正前unique", "new": "修正後"}
  ]
}"""


def _sh(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()


def _apply_patches(patches: list[dict]) -> tuple[list[str], list[str]]:
    """適用ファイルとNG拒否ファイル両方返す。"""
    applied, rejected = [], []
    for p in patches:
        fp = p.get("file", "").strip().lstrip("./")
        old = p.get("old", "")
        new = p.get("new", "")
        if not fp or not old or not os.path.exists(fp):
            continue
        # NGパターン監査
        ng = ng_scan(new)
        if ng:
            rejected.append(f"{fp} (NG: {ng[0]}={ng[1][:30]})")
            continue
        content = open(fp, encoding="utf-8").read()
        if old not in content:
            continue
        open(fp, "w", encoding="utf-8").write(content.replace(old, new, 1))
        applied.append(fp)
    return applied, rejected


def _full_auto_mode() -> bool:
    """環境変数 or GAS設定シート由来。シンプルに env で制御。"""
    return os.environ.get("FULL_AUTO_MODE", "FALSE").upper() == "TRUE"


def _commit_to_master(commit_msg: str) -> bool:
    """直接masterにpush（FULL_AUTO_MODE時）"""
    try:
        _sh(["git", "config", "user.email", "kenji-bot@kcs.local"])
        _sh(["git", "config", "user.name", "Kenji Bot"])
        _sh(["git", "add", "-A"])
        _sh(["git", "commit", "-m", commit_msg])
        _sh(["git", "push", "origin", "master"])
        return True
    except subprocess.CalledProcessError as e:
        print(f"direct push失敗: {e.output}")
        return False


def _create_pr(branch: str, title: str, body: str) -> str | None:
    try:
        _sh(["git", "config", "user.email", "kenji-bot@kcs.local"])
        _sh(["git", "config", "user.name", "Kenji Bot"])
        _sh(["git", "checkout", "-b", branch])
        _sh(["git", "add", "-A"])
        _sh(["git", "commit", "-m", title])
        _sh(["git", "push", "-u", "origin", branch])
        return _sh(["gh", "pr", "create", "--title", title, "--body", body, "--base", "master"])
    except subprocess.CalledProcessError as e:
        print(f"PR作成失敗: {e.output}")
        return None


def _try_oauth_refresh(account: str = "HAL") -> bool:
    """X OAuth2 トークンリフレッシュ試行。tweepyのrefreshに頼る。実装はsmoke版（要拡張）。"""
    try:
        from scripts.common.twitter_client import _v2_client
        client = _v2_client(account)
        # tweepyのbearer tokenをトリガーするだけのlight call
        client.get_me()
        return True
    except Exception as e:
        print(f"OAuth refresh失敗 ({account}): {e}")
        return False


def run():
    error_info = os.environ.get("ERROR_INFO", "エラー情報なし")
    full_auto = _full_auto_mode()

    # ── Step 1: ナレッジ先行照会 ───────────────────────────
    cached = find_similar(error_info)
    if cached:
        print(f"ナレッジヒット: {cached.get('id')} (適用済み {cached.get('applied_count',0)}回)")
        applied, rejected = _apply_patches(cached.get("patches", []))
        increment_applied_count(cached.get("id", ""))
        msg = (
            f"♻️ **ナレッジ即時適用** [{cached.get('severity','?')}]\n"
            f"既知事象 `{cached.get('id')}` ({cached.get('applied_count',0)+1}回目)\n"
            f"**原因:** {cached.get('cause','')}\n**対策:** {cached.get('solution','')}\n"
            f"適用: {', '.join(applied) or 'なし'}\n"
        )
        if rejected:
            msg += f"⚠️ NG監査で却下: {', '.join(rejected)}\n"
        if applied:
            if full_auto:
                _commit_to_master(f"fix(kenji-cached): {cached.get('cause','?')[:60]}")
                msg += "→ master直push完了（FULL_AUTO_MODE）"
            else:
                pr = _create_pr(f"kenji-cached/{int(time.time())}",
                                f"fix(kenji-cached): {cached.get('cause','?')[:60]}",
                                f"既知事象再適用: {cached.get('id')}")
                if pr:
                    msg += f"→ PR: {pr} (`!approve_patch {pr.rstrip('/').split('/')[-1]}`)"
        notify(msg, channel="error-log")
        return

    # ── Step 2: ケンジ解析 ───────────────────────────
    result_text = call_claude(KENJI_PROMPT, f"以下のエラーを解析:\n{error_info}")
    try:
        text = result_text.strip()
        if "```" in text:
            text = text.split("```")[1].strip()
            if text.startswith("json"):
                text = text[4:].strip()
        result = json.loads(text)
    except Exception:
        notify(f"🔴 **ケンジ解析失敗**\n```\n{result_text[:1500]}\n```", channel="error-log")
        return

    sev_emoji = {"高": "🔴", "中": "🟡", "低": "🟢"}.get(result.get("severity", ""), "⚪")
    message = (
        f"{sev_emoji} **障害 [{result.get('severity','?')}]**\n"
        f"**対象:** {result.get('system','?')}\n"
        f"**原因:** {result.get('reason','?')}\n"
        f"**対策:** {result.get('solution','?')}\n"
    )

    # ── Step 3: OAuth リフレッシュ第一段階 ───────────────────────────
    if result.get("oauth_refresh"):
        ok = _try_oauth_refresh("HAL") and _try_oauth_refresh("SUNAKUN")
        message += "🔄 OAuth リフレッシュ" + ("成功" if ok else "失敗") + "\n"
        if ok:
            save_incident(error_info, result, ["oauth-refresh"], verified=True)
            notify(message + "→ コード修正不要、ナレッジに記録", channel="error-log")
            return

    # ── Step 4: パッチ適用 ───────────────────────────
    patches = result.get("patches") or []
    if not patches:
        notify(message + "（コード修正不要）", channel="error-log")
        return

    applied, rejected = _apply_patches(patches)
    if rejected:
        message += f"⚠️ NG監査で却下: {', '.join(rejected)}\n"
    if not applied:
        message += "⚠️ 適用可能なパッチなし。手動確認をお願いします。\n"
        notify(message, channel="error-log")
        return

    # ── Step 5: モード別展開 ───────────────────────────
    title = f"fix(kenji): {result.get('system','system')} - {result.get('reason','?')[:60]}"
    if full_auto:
        ok = _commit_to_master(title)
        if ok:
            save_incident(error_info, result, applied, verified=True)
            message += "\n🤖 **FULL_AUTO_MODE**: master直push完了、ナレッジ保存"
        else:
            message += "\n⚠️ master push失敗"
    else:
        branch = f"kenji-patch/{int(time.time())}"
        pr = _create_pr(branch, title,
                       f"## 自動修復\n**原因:** {result.get('reason','')}\n**対策:** {result.get('solution','')}\n変更: {', '.join(applied)}\n\n⚠️ ケンジ自動生成。社長レビュー後にmerge。")
        if pr:
            num = pr.rstrip("/").split("/")[-1]
            message += f"\n🤖 **PR作成**\n{pr}\n承認: `!approve_patch {num}`"
            # PRマージ時にKnowledge保存するため、ここでは未verified状態で保存
            save_incident(error_info, result, applied, verified=False)
        else:
            message += "\n⚠️ PR作成失敗"

    notify(message, channel="error-log")
    print(f"監視レポート送信完了 (mode={'AUTO' if full_auto else 'MANUAL'}, applied={len(applied)})")


if __name__ == "__main__":
    run()
