"""
HyperFrames ランナー（KCSAPP 全スタッフ共通モジュール）

責務：
- ローカル実行：テンプレ＋変数 → MP4 書き出し（WF-07 内で使用）
- ディスパッチ：他のworkflowやGASからWF-07をrepository_dispatchで起動

テンプレ規約：
  video_templates/<name>/
    ├── template.html        # {{VAR}} プレースホルダー入り
    ├── hyperframes.json
    ├── package.json
    └── assets/              # フォント・ロゴ等の固定素材（任意）

レンダリング時：
  template.html の {{VAR}} を variables[VAR] で置換 → index.html として書き出し
  variables.media に絶対パスがあれば assets/media.mp4 (or .png) としてコピー
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request


# テンプレ置き場（リポジトリルートからの相対パス）
TEMPLATES_DIR = pathlib.Path(__file__).resolve().parents[2] / "video_templates"


def _substitute(text: str, variables: dict) -> str:
    """{{KEY}} を variables[KEY] で置換。未定義キーはそのまま残す（描画時に空白として目立つ）。"""
    def repl(m):
        key = m.group(1).strip()
        if key in variables:
            v = variables[key]
            return "" if v is None else str(v)
        return m.group(0)
    return re.sub(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", repl, text)


def _prepare_workspace(template_name: str, variables: dict) -> pathlib.Path:
    """テンプレを一時workdirにコピーし、placeholderを置換、メディアを配置。workdir Path返却。"""
    src_dir = TEMPLATES_DIR / template_name
    if not src_dir.is_dir():
        raise FileNotFoundError(f"テンプレが見つかりません: {src_dir}")

    workdir = pathlib.Path(tempfile.mkdtemp(prefix=f"hf_{template_name}_"))

    # テンプレファイルをコピー（template.html は index.html に変換）
    for item in src_dir.iterdir():
        if item.name == "template.html":
            continue
        if item.is_dir():
            shutil.copytree(item, workdir / item.name, dirs_exist_ok=True)
        else:
            shutil.copy2(item, workdir / item.name)

    # template.html → 置換 → index.html
    template_html = (src_dir / "template.html").read_text(encoding="utf-8")
    index_html = _substitute(template_html, variables)
    (workdir / "index.html").write_text(index_html, encoding="utf-8")

    # メディア注入（variables['media_path'] があれば assets/main.mp4 等に配置）
    assets_dir = workdir / "assets"
    assets_dir.mkdir(exist_ok=True)
    if variables.get("media_path"):
        src_media = pathlib.Path(variables["media_path"])
        if src_media.is_file():
            ext = src_media.suffix.lower()
            dest_name = "main" + ext
            shutil.copy2(src_media, assets_dir / dest_name)
            # テンプレ側は固定パス assets/main.mp4 等を参照すれば良い

    return workdir


def render_local(template_name: str, variables: dict, output_path: str | None = None) -> str:
    """テンプレ＋変数 → MP4 生成。WF-07内で呼ばれる想定（Node+FFmpeg必須）。

    Returns: 生成されたMP4の絶対パス
    """
    workdir = _prepare_workspace(template_name, variables)

    start = time.time()
    cmd = ["npx", "--yes", "hyperframes@0.7.17", "render"]
    proc = subprocess.run(
        cmd, cwd=workdir, capture_output=True, text=True, timeout=900
    )
    elapsed = time.time() - start

    if proc.returncode != 0:
        raise RuntimeError(
            f"HyperFrames render failed (exit={proc.returncode}) after {elapsed:.1f}s\n"
            f"STDOUT: {proc.stdout[-2000:]}\nSTDERR: {proc.stderr[-2000:]}"
        )

    # renders/ から最新MP4を拾う
    renders_dir = workdir / "renders"
    if not renders_dir.is_dir():
        raise RuntimeError(f"renders ディレクトリが存在しません: {renders_dir}")
    mp4s = sorted(renders_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not mp4s:
        raise RuntimeError(f"レンダリング結果のMP4が見つかりません: {renders_dir}")
    src_mp4 = mp4s[0]

    if output_path:
        dest = pathlib.Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_mp4, dest)
        return str(dest)
    return str(src_mp4)


def dispatch_render(template_name: str, variables: dict, staff: str = "", source: str = "") -> dict:
    """GitHub repository_dispatch で WF-07 を起動。GAS or 他workflowから video render を依頼する用。

    必須env: GITHUB_TOKEN (workflow scope), GITHUB_REPOSITORY (例: 'kenny1102bleu-lab/kcs-automation')
    Returns: {'ok': bool, 'status': int, 'message': str}
    """
    token = os.environ.get("GITHUB_DISPATCH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "kenny1102bleu-lab/kcs-automation")
    if not token:
        return {"ok": False, "status": 0, "message": "GITHUB_DISPATCH_TOKEN/GITHUB_TOKEN 未設定"}

    url = f"https://api.github.com/repos/{repo}/dispatches"
    payload = json.dumps({
        "event_type": "render_video",
        "client_payload": {
            "template": template_name,
            "variables": variables,
            "staff": staff,
            "source": source,
            "ts": int(time.time()),
        }
    }).encode("utf-8")

    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return {"ok": r.status in (200, 201, 204), "status": r.status, "message": "dispatched"}
    except Exception as e:
        return {"ok": False, "status": 0, "message": f"dispatch failed: {e}"}


# ── CLI（WF-07 のジョブステップから呼ばれる）─────────────────
def _cli_main():
    """JSON を stdin or --vars で受け取り render_local を実行。MP4 パスを stdout に出す。"""
    import argparse
    parser = argparse.ArgumentParser(description="HyperFrames runner CLI")
    parser.add_argument("--template", required=True, help="テンプレ名（video_templates/ 配下）")
    parser.add_argument("--vars", help="変数JSON文字列。省略時は stdin から読む")
    parser.add_argument("--output", help="出力MP4パス（省略時はworkdir内）")
    args = parser.parse_args()

    if args.vars:
        variables = json.loads(args.vars)
    else:
        raw = sys.stdin.read().strip()
        variables = json.loads(raw) if raw else {}

    mp4_path = render_local(args.template, variables, args.output)
    print(mp4_path)


if __name__ == "__main__":
    _cli_main()
