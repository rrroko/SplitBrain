import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional


APP_NAME = "SplitBrain"
LOG_FILE_NAME = "telemetry.jsonl"


def _get_log_dir() -> str:
    """ログを置くディレクトリ（AppData\Roaming\SplitBrain 想定）"""
    base = os.getenv("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _get_log_path() -> str:
    return os.path.join(_get_log_dir(), LOG_FILE_NAME)


def track_event(event_type: str, **data: Any) -> None:
    """
    汎用イベントロガー。
    event_type: "action", "mode_switch", "snapshot" など
    data: 任意の追加情報（mode, context, action_id, meta など）
    """
    record: Dict[str, Any] = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "event_type": event_type,
        **data,
    }
    path = _get_log_path()
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")
    except Exception as e:
        # ログが壊れてもアプリ本体は落とさない
        print("[telemetry] failed to write log:", e)


def read_events(
    days: Optional[int] = None,
    event_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    telemetry.jsonl を読み込む。
    - days を指定すると、その日数だけさかのぼったイベントだけに絞る
    - event_type を指定すると、その種類だけに絞る
    """
    path = _get_log_path()
    if not os.path.exists(path):
        return []

    since: Optional[datetime] = None
    if days is not None:
        since = datetime.utcnow() - timedelta(days=days)

    events: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # 日付フィルタ
                if since is not None:
                    ts_str = ev.get("ts")
                    if not ts_str:
                        continue
                    try:
                        # "....Z" を想定
                        if ts_str.endswith("Z"):
                            ts_str = ts_str[:-1]
                        ts = datetime.fromisoformat(ts_str)
                    except Exception:
                        continue
                    if ts < since:
                        continue

                # 種類フィルタ
                if event_type is not None and ev.get("event_type") != event_type:
                    continue

                events.append(ev)
    except Exception as e:
        print("[telemetry] failed to read log:", e)

    return events


def clear_events() -> None:
    """ログファイルを削除（新規作成）"""
    path = _get_log_path()
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        print("[telemetry] failed to clear log:", e)
