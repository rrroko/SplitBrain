# app/core/mode_recommend.py

from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 保存先はごく小さいJSON一個だけ
DATA_PATH = Path("outputs") / "assistant"
DATA_PATH.mkdir(parents=True, exist_ok=True)
DATA_FILE = DATA_PATH / "mode_recommend.json"

# 1モード×1コンテキストで保持する最大件数
MAX_ITEMS_PER_BUCKET = 10


def _load_data() -> Dict[str, Dict[str, Dict[str, int]]]:
    """
    データ構造:
    {
      "focus": {
        "work": {"app:code": 5, "url:github.com": 3},
        "private": {...}
      },
      "relax": {...},
      "present": {...}
    }
    """
    if not DATA_FILE.exists():
        return {}
    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_data(data: Dict[str, Dict[str, Dict[str, int]]]) -> None:
    try:
        with DATA_FILE.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        # 保存失敗してもアプリ本体は止めない
        pass


def record_usage(
    mode: Optional[str],
    context: Optional[str],
    target_id: str,
) -> None:
    """
    「このモード＋コンテキストで、このターゲット(app/url)を使った」
    という情報だけをカウントする。

    target_id 例:
      - "app:code"
      - "app:powerpoint"
      - "url:youtube.com"
      - "url:github.com"
    """
    if not target_id:
        return

    m = mode or "unknown"
    c = context or "any"

    data = _load_data()
    mode_bucket = data.setdefault(m, {})
    ctx_bucket = mode_bucket.setdefault(c, {})

    # カウントを増やす
    ctx_bucket[target_id] = ctx_bucket.get(target_id, 0) + 1

    # 多すぎるときは上位 MAX_ITEMS_PER_BUCKET 件だけ残す
    if len(ctx_bucket) > MAX_ITEMS_PER_BUCKET:
        # count の大きい順にソートして先頭だけ残す
        sorted_items = sorted(ctx_bucket.items(), key=lambda x: x[1], reverse=True)
        trimmed = dict(sorted_items[:MAX_ITEMS_PER_BUCKET])
        mode_bucket[c] = trimmed

    _save_data(data)


def top_targets(
    mode: Optional[str],
    context: Optional[str],
    limit: int = 5,
) -> List[Tuple[str, int]]:
    """
    モード＋コンテキストごとの「よく使うターゲット」を上位順で返す。
    例: [("app:code", 12), ("url:github.com", 8)]
    """
    data = _load_data()
    m = mode or "unknown"
    c = context or "any"

    mode_bucket = data.get(m, {})
    ctx_bucket = mode_bucket.get(c, {})

    items = sorted(ctx_bucket.items(), key=lambda x: x[1], reverse=True)
    if limit > 0:
        items = items[:limit]
    return items
