"""
ナレッジベース管理。Knowledge/ フォルダ配下のJSONを検索→ヒットしたらAI推論をスキップ。

ファイル構造:
  Knowledge/
    incidents/
      <timestamp>_<slug>.json
        {
          "id": "...",
          "occurred_at": "ISO8601",
          "fingerprint": "エラーの要約キーワード（部分一致用）",
          "system": "GitHub Actions / Discord Bot / GAS / Render",
          "cause": "原因",
          "solution": "対策",
          "patches": [{"file": "...", "old": "...", "new": "..."}],
          "verified": true,
          "applied_count": 3
        }
"""
import json
import os
import pathlib
import datetime
import hashlib

KNOWLEDGE_DIR = pathlib.Path("Knowledge/incidents")


def _norm(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum())


def find_similar(error_info: str) -> dict | None:
    """過去の解決済みインシデントから類似事象を探す。fingerprint部分一致で判定。"""
    if not KNOWLEDGE_DIR.exists():
        return None
    norm_err = _norm(error_info)
    best, best_score = None, 0
    for p in KNOWLEDGE_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not data.get("verified"):
            continue
        fp = data.get("fingerprint", "")
        if not fp:
            continue
        score = sum(1 for tok in _norm(fp).split() if tok in norm_err)
        # 連続部分一致でカウント
        for tok in fp.lower().split():
            if len(tok) >= 5 and _norm(tok) in norm_err:
                score += 2
        if score > best_score:
            best, best_score = data, score
    return best if best_score >= 3 else None


def save_incident(error_info: str, result: dict, applied_files: list[str], verified: bool = True) -> str:
    """成功した修復をKnowledge/に保存。"""
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.utcnow().isoformat()
    slug = hashlib.sha1(error_info.encode()).hexdigest()[:10]
    incident_id = f"{ts.replace(':', '').replace('-', '')[:14]}_{slug}"

    fingerprint = " ".join(
        w for w in error_info.split() if len(w) >= 4 and not w.startswith("http")
    )[:300]

    payload = {
        "id": incident_id,
        "occurred_at": ts,
        "fingerprint": fingerprint,
        "system": result.get("system", "unknown"),
        "severity": result.get("severity", "?"),
        "cause": result.get("reason", ""),
        "solution": result.get("solution", ""),
        "patches": result.get("patches", []),
        "applied_files": applied_files,
        "verified": verified,
        "applied_count": 1,
    }
    path = KNOWLEDGE_DIR / f"{incident_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def increment_applied_count(incident_id: str):
    """既存インシデントの再適用カウンタを増やす。"""
    for p in KNOWLEDGE_DIR.glob(f"{incident_id}*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            d["applied_count"] = d.get("applied_count", 0) + 1
            p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
