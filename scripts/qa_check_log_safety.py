"""公開リポジトリの Actions ログに、報告の中身（取引先名・金額など）が出ないことの回帰テスト。

    python scripts/qa_check_log_safety.py
"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scripts import secretary_morning as sm  # noqa: E402

SECRET = "KCS-2026-0001 桜亭 入金が14日遅れています 110,000円"


def check(cond, msg):
    if not cond:
        print(f"NG: {msg}")
        sys.exit(1)
    print(f"OK: {msg}")


def run_capture(env):
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    sm.build = lambda now: (f"🗂️ 秘書室 朝の報告\n**1. 承認待ち** 1件\n- 経理部：{SECRET}", 1)
    sm.record_activity = lambda now, n: None
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            sm.run()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return buf.getvalue()


# 1) GitHub Actions 上（ログは公開）: 中身を出さない。件数だけ
out = run_capture({"GITHUB_ACTIONS": "true", "DRY_RUN": "1"})
check("桜亭" not in out and "110,000" not in out and "KCS-2026" not in out, "Actions のログに、取引先名・金額・請求書番号が出ない")
check("承認待ち 1件" in out, "件数だけはログに出る")

# 2) 手元の実行（ログは自分だけ）: 全文を出す
out2 = run_capture({"GITHUB_ACTIONS": "", "DRY_RUN": "1"})
check(SECRET in out2, "手元の実行では全文を確認できる")

print("すべて通過")
